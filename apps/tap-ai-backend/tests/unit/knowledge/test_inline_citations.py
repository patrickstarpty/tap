"""Inline `[S1]`/`[R1]` citation markers (LangGraph PR 3): `parse_inline_answer` turns a
streamed Markdown chat answer into the same `(answer, claims)` shape
`parse_grounded_answer_payload` produced for the closed JSON schema, and
`KnowledgeModelGateway.answer` now gets there via `ModelGateway.chat_stream`,
reporting every raw text delta to an optional `on_text` callback."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.domain.models import (
    ModelCallAudit,
    ModelOperation,
    ModelResult,
    ModelStreamEvent,
    ModelTextDelta,
    ModelUsage,
    text_digest,
)
from tap.modules.knowledge.adapters.grounded_output import parse_inline_answer
from tap.modules.knowledge.adapters.litellm import _TOOL_LOOP_PROMPT, KnowledgeModelGateway
from tap.modules.knowledge.domain.models import (
    CodeAnchor,
    ContentRole,
    Evidence,
    IndexRevision,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
)

SOURCE_HASH = "sha256:" + "a" * 64
CHUNK_HASH = "sha256:" + "b" * 64


def evidence(*, label: str = "S1", content: str = "Current policy is required.") -> Evidence:
    return Evidence(
        family=SourceFamily.CODE,
        chunk_id="h_" + "1" * 64,
        logical_chunk_id="h_" + "2" * 64,
        title="authorize",
        content=content,
        source=SourceRevisionRef(
            source_id="repo:checkout:payment.py",
            source_type="code",
            revision_kind=RevisionKind.GIT_COMMIT,
            revision="a" * 40,
            source_content_hash=SOURCE_HASH,
            anchor=CodeAnchor(
                repo="checkout",
                path="payment.py",
                symbol="authorize",
                line_start=10,
                line_end=25,
            ),
        ),
        chunk_content_hash=CHUNK_HASH,
        content_role=ContentRole.SOURCE,
        citation_id="citation-1",
        evidence_label=label,
        index_revision=IndexRevision(
            physical_index="kb-code-v1-20260824",
            schema_version="search-schema-v1",
            corpus_version="corpus-17",
        ),
        embedding_model_version="tap-embed-fixed-v1",
        acl_decision_id="decision-17",
        score=1 / 61,
    )


def _parse(text: str, items, **overrides):
    defaults = dict(
        max_answer_chars=16_000, max_claims=64, max_claim_chars=4_000, max_labels_per_claim=16
    )
    defaults.update(overrides)
    return parse_inline_answer(text, items, **defaults)


def test_sentences_with_markers_become_claims() -> None:
    answer, claims = _parse(
        "理赔需在 30 日内提交[S1]。材料包括身份证明[S2][S1]。",
        (evidence(label="S1"), evidence(label="S2")),
    )

    assert "[" not in answer and "]" not in answer
    assert len(claims) == 2
    assert claims[0].evidence_labels == ("S1",)
    assert claims[1].evidence_labels == ("S2", "S1")


def test_unknown_and_code_span_markers_are_not_claims() -> None:
    answer, claims = _parse(
        "见 `[S1]` 示例。结论成立[S9]。",
        (evidence(label="S1"),),
    )

    assert (answer, claims) == ("", ())


def test_relation_labels_are_accepted_via_extra_labels() -> None:
    answer, claims = _parse(
        "核保要求健康告知[R1]。",
        (evidence(label="S1"),),
        extra_labels=frozenset({"R1"}),
    )

    assert claims[0].evidence_labels == ("R1",)
    assert "[" not in answer


def test_markers_after_terminal_punctuation_also_attach_to_the_sentence() -> None:
    answer, claims = _parse(
        "理赔需在 30 日内提交。[S1]",
        (evidence(label="S1"),),
    )

    assert claims[0].evidence_labels == ("S1",)
    assert "[" not in answer


def test_markers_padded_with_spaces_inside_the_brackets_still_ground_claims() -> None:
    """Real qwen-plus output intermittently pads markers as `[ S1 ]`; such a
    marker must still ground its sentence (and be stripped from the answer)
    instead of leaving the turn with no claim and abstaining."""
    answer, claims = _parse(
        "小额理赔的金额标准是低于5000元[ S1 ]。线上快速通道的审核时限是3个工作日[ S1 ][S2]。",
        (evidence(label="S1"), evidence(label="S2")),
    )

    assert answer == "小额理赔的金额标准是低于5000元。线上快速通道的审核时限是3个工作日。"
    assert [claim.evidence_labels for claim in claims] == [("S1",), ("S1", "S2")]


def test_plain_text_without_any_marker_abstains() -> None:
    assert _parse("INSUFFICIENT_EVIDENCE", (evidence(label="S1"),)) == ("", ())


def test_markdown_headings_and_list_markup_are_preserved_in_the_answer() -> None:
    """Ruling (2026-10-16 review): the parser must not flatten Markdown -- the
    heading and the list bullets stay in the returned answer verbatim; only
    the marker syntax itself is removed. Claim text drops the list prefix."""
    answer, claims = _parse(
        "## 结论\n\n- 退款需两人审批[S1]。\n- 材料包括身份证明[S2]。",
        (evidence(label="S1"), evidence(label="S2")),
    )

    assert answer == "## 结论\n\n- 退款需两人审批。\n- 材料包括身份证明。"
    assert [claim.text for claim in claims] == ["退款需两人审批。", "材料包括身份证明。"]
    assert claims[0].evidence_labels == ("S1",)
    assert claims[1].evidence_labels == ("S2",)


def test_uncited_sentences_stay_visible_in_the_answer() -> None:
    """Ruling: an uncited sentence (here, one whose only marker names a label
    that was never supplied) is not hidden from the answer -- only the
    sentence that actually grounds becomes a claim."""
    answer, claims = _parse(
        "合同效力中止[S1]。无依据[S7]。",
        (evidence(label="S1"),),
    )

    assert answer == "合同效力中止。无依据。"
    assert [claim.text for claim in claims] == ["合同效力中止。"]


@pytest.mark.parametrize(
    ("text", "literal"),
    [
        ("参见[docs](https://example.com)获取详情[S1]。", "[docs](https://example.com)"),
        ("勾选[x]表示完成[S1]。", "[x]"),
        ("数组下标 a[0] 为首元素[S1]。", "a[0]"),
    ],
    ids=("markdown-link", "checkbox", "array-index"),
)
def test_marker_grammar_ignores_non_citation_bracket_syntax(text: str, literal: str) -> None:
    """Ruling: `_MARKER` must match only `[S<n>]`/`[R<n>]`; a Markdown link, a
    task-list checkbox, or an unrelated array-index bracket must survive in
    the answer untouched (not be mistaken for, or swallowed as, a marker) while
    the real `[S1]` marker elsewhere in the same sentence still grounds it.
    Round 2: the claim text itself (not just the answer) must still carry the
    literal -- the sentence splitter must not fragment on a non-terminating
    `.`/`?` inside it (a URL's dot, here)."""
    answer, claims = _parse(text, (evidence(label="S1"),))

    assert literal in answer
    assert literal in claims[0].text
    assert claims[0].evidence_labels == ("S1",)


def _answer_gateway(stream_gateway) -> KnowledgeModelGateway:
    async def redact(text: str) -> str:
        return text

    return KnowledgeModelGateway(
        stream_gateway,
        scope=VALIDATION_SCOPE,
        redact=redact,
        embedding_alias="text-embedding-v4",
        chat_alias="qwen-plus",
        embedding_dimension=2,
        timeout_seconds=1,
    )


def _result(output: str, *, tool_calls: tuple[object, ...] = ()) -> ModelResult:
    return ModelResult(
        output=output,
        actual_model="qwen-plus",
        usage=ModelUsage(),
        actual_provider="dashscope",
        tool_calls=tool_calls,
        audit=ModelCallAudit(
            VALIDATION_SCOPE,
            "qwen-plus",
            ModelOperation.CHAT,
            text_digest("prompt"),
            None,
            text_digest("context"),
            "idem-1",
            "dashscope",
            "qwen-plus",
            ModelUsage(),
        ),
    )


def test_unrelated_internal_spacing_is_never_rewritten() -> None:
    """Ruling: dropped the space-before-punctuation rewrite because it could
    mangle ordinary text such as a version number; "v 1.2" must survive intact
    in the claim text itself (round 2), not just somewhere in the answer --
    the `.` inside "1.2" must never split the sentence."""
    answer, claims = _parse("当前版本为 v 1.2[S1]。", (evidence(label="S1"),))

    assert "v 1.2" in answer
    assert claims[0].text == "当前版本为 v 1.2。"
    assert claims[0].evidence_labels == ("S1",)


@pytest.mark.asyncio
async def test_insufficient_evidence_sentinel_abstains() -> None:
    class InsufficientEvidenceGateway:
        async def chat_stream(self, request) -> AsyncIterator[ModelStreamEvent]:
            yield ModelTextDelta("INSUFFICIENT_EVIDENCE")
            yield _result("INSUFFICIENT_EVIDENCE")

    generation = await _answer_gateway(InsufficientEvidenceGateway()).answer(
        "query", (evidence(label="S1"),), "quick-hybrid-v1"
    )

    assert generation.text == ""
    assert generation.claims == ()


@pytest.mark.asyncio
async def test_plan_bound_answer_streams_via_on_text_and_parses_inline() -> None:
    """Critical review finding (2026-10-16): every production retrieval turn
    carries `answer_input` (the answer planner), so it is this plan-bound path
    -- not just the ungoverned one -- that must actually stream and parse
    inline markers."""
    from tap.modules.knowledge.application.answer_templates import (
        assemble_answer,
        get_template,
        latest_template_version,
    )

    chunks = ["理赔需在 30 日内提交", "[S1]。"]

    class PlanBoundStreamGateway:
        async def chat_stream(self, request) -> AsyncIterator[ModelStreamEvent]:
            for chunk in chunks:
                yield ModelTextDelta(chunk)
            yield _result("".join(chunks))

    version = latest_template_version("general")
    selected = get_template("general", version)
    assembled = assemble_answer(
        template_id="general",
        template_version=version,
        template_digest=selected.digest,
        original_question="query",
        standalone_question="query",
        evidence_map={"q1": ("S1",)},
    )
    received: list[str] = []

    async def on_text(text: str) -> None:
        received.append(text)

    generation = await _answer_gateway(PlanBoundStreamGateway()).answer(
        "query",
        (evidence(label="S1"),),
        "quick-hybrid-v1",
        answer_input=assembled,
        on_text=on_text,
    )

    assert received == chunks
    assert generation.text == "理赔需在 30 日内提交。"
    assert generation.claims[0].evidence_labels == ("S1",)


@pytest.mark.asyncio
async def test_plan_bound_answer_abstains_as_insufficient_evidence_on_the_sentinel() -> None:
    """Critical review finding: the plan-bound (governed) path must abstain the
    same way the ungoverned path does when the model has nothing groundable --
    `AnswerGeneration(text="", claims=())` is what callers downstream
    (`AuthorizedRetrieval`) key off to set `AbstentionReason.INSUFFICIENT_EVIDENCE`."""
    from tap.modules.knowledge.application.answer_templates import (
        assemble_answer,
        get_template,
        latest_template_version,
    )

    class InsufficientEvidenceGateway:
        async def chat_stream(self, request) -> AsyncIterator[ModelStreamEvent]:
            yield ModelTextDelta("INSUFFICIENT_EVIDENCE")
            yield _result("INSUFFICIENT_EVIDENCE")

    version = latest_template_version("general")
    selected = get_template("general", version)
    assembled = assemble_answer(
        template_id="general",
        template_version=version,
        template_digest=selected.digest,
        original_question="query",
        standalone_question="query",
        evidence_map={"q1": ("S1",)},
    )

    generation = await _answer_gateway(InsufficientEvidenceGateway()).answer(
        "query", (evidence(label="S1"),), "quick-hybrid-v1", answer_input=assembled
    )

    assert generation.text == ""
    assert generation.claims == ()


@pytest.mark.asyncio
async def test_on_text_receives_every_delta() -> None:
    chunks = ["退款需两", "人审批[S1]", "。"]

    class ChunkingGateway:
        async def chat_stream(self, request) -> AsyncIterator[ModelStreamEvent]:
            for chunk in chunks:
                yield ModelTextDelta(chunk)
            yield _result("".join(chunks))

    received: list[str] = []

    async def on_text(text: str) -> None:
        received.append(text)

    generation = await _answer_gateway(ChunkingGateway()).answer(
        "query", (evidence(label="S1"),), "quick-hybrid-v1", on_text=on_text
    )

    assert received == chunks
    assert generation.claims[0].evidence_labels == ("S1",)


@pytest.mark.asyncio
async def test_clarify_prefix_yields_clarification_generation() -> None:
    class ClarifyGateway:
        async def chat_stream(self, request) -> AsyncIterator[ModelStreamEvent]:
            yield ModelTextDelta("  CLARIFY: 你指的是哪个产品版本？")
            yield _result("  CLARIFY: 你指的是哪个产品版本？")

    generation = await _answer_gateway(ClarifyGateway()).answer(
        "query", (evidence(label="S1"),), "quick-hybrid-v1"
    )

    assert generation.answer_kind == "clarification"
    assert generation.text == "你指的是哪个产品版本？"
    assert generation.claims == ()


@pytest.mark.asyncio
async def test_clarify_marker_mid_text_is_ordinary_text() -> None:
    class MidTextGateway:
        async def chat_stream(self, request) -> AsyncIterator[ModelStreamEvent]:
            yield ModelTextDelta("保单生效[S1]。CLARIFY: x")
            yield _result("保单生效[S1]。CLARIFY: x")

    generation = await _answer_gateway(MidTextGateway()).answer(
        "query", (evidence(label="S1"),), "quick-hybrid-v1"
    )

    assert generation.answer_kind == "answer"


@pytest.mark.asyncio
async def test_clarify_prefix_with_no_question_abstains_instead_of_an_empty_clarification() -> None:
    """Ruling (task reviewer, fix round 1): a `CLARIFY:` prefix with nothing
    after it carries no question to show the user -- per the plan ("正文为空
    时按无依据处理"), this is the no-evidence/abstain outcome, not an empty
    clarification, so `answer_kind` must stay `"answer"` (the turn then
    abstains downstream in `verify_answer`, never completes with a blank
    clarification)."""

    class EmptyClarifyGateway:
        async def chat_stream(self, request) -> AsyncIterator[ModelStreamEvent]:
            yield ModelTextDelta("CLARIFY:")
            yield _result("CLARIFY:")

    generation = await _answer_gateway(EmptyClarifyGateway()).answer(
        "query", (evidence(label="S1"),), "quick-hybrid-v1"
    )

    assert generation.answer_kind == "answer"
    assert generation.text == ""
    assert generation.claims == ()


@pytest.mark.asyncio
async def test_answer_with_tools_returns_tool_calls_without_parsing() -> None:
    """PR 5 Task 7: when the model asks to call a tool, `answer` returns the
    tool calls verbatim and never attempts to parse the (discarded) draft
    text as a grounded answer."""
    from tap.modules.ai.domain.models import ModelToolCall

    captured: dict[str, object] = {}

    class ToolCallGateway:
        async def chat_stream(self, request) -> AsyncIterator[ModelStreamEvent]:
            captured["request"] = request
            yield ModelTextDelta("let me check")
            yield _result(
                "let me check",
                tool_calls=(ModelToolCall("c1", "knowledge_search", '{"query":"q"}'),),
            )

    generation = await _answer_gateway(ToolCallGateway()).answer(
        "query", (evidence(label="S1"),), "quick-hybrid-v1", tools=("knowledge.search",)
    )

    assert generation.text == ""
    assert generation.claims == ()
    assert len(generation.tool_calls) == 1
    assert generation.tool_calls[0].name == "knowledge_search"
    assert generation.tool_calls[0].arguments_json == '{"query":"q"}'
    request = captured["request"]
    assert request.tools[0].tool_id == "knowledge.search"
    assert _TOOL_LOOP_PROMPT in request.prompt


@pytest.mark.asyncio
async def test_answer_without_tools_omits_tool_loop_prompt() -> None:
    """PR 5 Task 7: with no tools offered, the request carries no tool specs
    and the prompt is not padded with the tool-loop instructions."""
    captured: dict[str, object] = {}

    class PlainGateway:
        async def chat_stream(self, request) -> AsyncIterator[ModelStreamEvent]:
            captured["request"] = request
            yield ModelTextDelta("结论成立[S1]。")
            yield _result("结论成立[S1]。")

    await _answer_gateway(PlainGateway()).answer(
        "query", (evidence(label="S1"),), "quick-hybrid-v1"
    )

    request = captured["request"]
    assert request.tools == ()
    assert _TOOL_LOOP_PROMPT not in request.prompt


@pytest.mark.asyncio
async def test_transcript_reaches_model_messages_redacted() -> None:
    """PR 5 Task 7: a tool message's `content` in the transcript is redacted
    the same way every other model-bound text is before it reaches the
    gateway's native `ModelMessage` transcript."""
    from tap.modules.ai.domain.models import ModelMessage

    captured: dict[str, object] = {}

    async def redact(text: str) -> str:
        return text.replace("user@example.com", "[REDACTED_EMAIL]")

    class CapturingGateway:
        async def chat_stream(self, request) -> AsyncIterator[ModelStreamEvent]:
            captured["request"] = request
            yield ModelTextDelta("结论成立[S1]。")
            yield _result("结论成立[S1]。")

    gateway = KnowledgeModelGateway(
        CapturingGateway(),
        scope=VALIDATION_SCOPE,
        redact=redact,
        embedding_alias="text-embedding-v4",
        chat_alias="qwen-plus",
        embedding_dimension=2,
        timeout_seconds=1,
    )

    await gateway.answer(
        "query",
        (evidence(label="S1"),),
        "quick-hybrid-v1",
        tools=("knowledge.search",),
        transcript=(
            {
                "role": "assistant",
                "toolCalls": [
                    {"callId": "c1", "name": "knowledge_search", "argumentsJson": '{"query":"q"}'}
                ],
            },
            {"role": "tool", "toolCallId": "c1", "content": "contact user@example.com"},
        ),
    )

    request = captured["request"]
    assert isinstance(request.messages[-1], ModelMessage)
    assert request.messages[-1].role == "tool"
    assert "[REDACTED_EMAIL]" in request.messages[-1].content


def _text_gateway(text: str):
    class TextGateway:
        async def chat_stream(self, request) -> AsyncIterator[ModelStreamEvent]:
            yield ModelTextDelta(text)
            yield _result(text)

    return TextGateway()


@pytest.mark.asyncio
async def test_clarify_reply_at_the_300_character_bound_is_a_clarification() -> None:
    question = "你" * 299 + "？"
    generation = await _answer_gateway(_text_gateway(f"CLARIFY: {question}\n")).answer(
        "query", (evidence(label="S1"),), "quick-hybrid-v1"
    )

    assert generation.answer_kind == "clarification"
    assert generation.text == question


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        "CLARIFY: " + "你" * 301,
        "CLARIFY: 你指的是哪个版本？\n保单生效[S1]。",
    ],
)
async def test_long_or_multiline_clarify_reply_is_a_grounded_answer(reply) -> None:
    """Final review M-5: only a single line of at most 300 characters after
    `CLARIFY:` is a clarification; a longer or multi-line reply is handled
    as an ordinary answer and goes through inline-citation grounding, so a
    model cannot bypass grounding with a `CLARIFY:` prefix."""
    generation = await _answer_gateway(_text_gateway(reply)).answer(
        "query", (evidence(label="S1"),), "quick-hybrid-v1"
    )

    assert generation.answer_kind == "answer"
    if "[S1]" in reply:
        assert generation.claims and generation.claims[0].evidence_labels == ("S1",)
    else:
        assert generation.claims == ()


def _governance():
    from tap.modules.ai.domain.models import GenerationGovernance, schema_digest

    output_schema = {"type": "object"}
    agent = "You are a policy-servicing test analyst."
    skill = "Write numbered test steps and cite the rule each step checks."
    return GenerationGovernance(
        model_alias="qwen-plus",
        system_instruction=agent,
        system_instruction_digest=text_digest(agent),
        skill_instructions=(skill,),
        skill_instruction_digests=(text_digest(skill),),
        tool_allowlist=frozenset({"knowledge.search", "knowledge.answer"}),
        output_schema=output_schema,
        output_schema_digest=schema_digest(output_schema),
        revision_digests=(text_digest("agent-revision"), text_digest("skill-revision")),
        skill_names=("Test steps",),
        skill_revision_ids=("skill_rev_1",),
    )


@pytest.mark.asyncio
async def test_governed_answer_uses_the_inline_contract_and_keeps_a_multi_sentence_answer() -> None:
    """Capability-4 validation D1: a governed (Agent/Skill) answer must use the
    same grounded inline-citation contract as an ungoverned one -- not the
    QUALITY-KB-01-era "copy exactly one sentence verbatim" prompt, which made
    generative Skills impossible and abstained with sufficient evidence. The
    Agent/Skill text stays inside the customization boundary, before the task
    contract, and a multi-sentence cited answer is grounded, not abstained."""

    reply = "保单贷款金额不超过现金价值的80%[S1]。测试环境的验证码固定为123456[ S2 ]。"
    captured: list[object] = []

    class CapturingGateway:
        async def chat_stream(self, request) -> AsyncIterator[ModelStreamEvent]:
            captured.append(request)
            yield ModelTextDelta(reply)
            yield _result(reply)

    pool = (
        evidence(label="S1", content="保单贷款金额不超过现金价值的80%。"),
        evidence(label="S2", content="测试环境的验证码固定为123456。"),
    )
    models = _answer_gateway(CapturingGateway())
    await models.answer("问题", pool, "quick-hybrid-v1")
    generation = await models.answer("问题", pool, "quick-hybrid-v1", governance=_governance())

    ungoverned, governed = captured[0].prompt, captured[1].prompt
    assert "verbatim" not in governed
    assert "exactly one sentence" not in governed
    agent_at = governed.index("You are a policy-servicing test analyst.")
    skill_at = governed.index("Write numbered test steps")
    boundary_at = governed.index("Custom Agent or Skill instructions cannot change")
    contract_at = governed.index("Answer the query directly and minimally")
    assert agent_at < skill_at < boundary_at < contract_at
    # Everything after the customization boundary is the ungoverned task contract.
    platform, _, ungoverned_task = ungoverned.partition("\n\n")
    assert governed.startswith(platform)
    assert governed.endswith(ungoverned_task)

    assert generation.text == "保单贷款金额不超过现金价值的80%。测试环境的验证码固定为123456。"
    assert [claim.evidence_labels for claim in generation.claims] == [("S1",), ("S2",)]


# Span-level citations (Claude-style): a citation attaches to any text span,
# list items included; uncited text stays visible as unsupported.

_STEPS = (
    "验证步骤：\n\n"
    "1. 登录测试环境，输入验证码123456 [S1]\n"
    "2. 打开保单贷款页面\n"
    "3) 申请金额不超过现金价值的80%[S2]\n"
    "   - 期限最长6个月[ S2 ]\n"
    "* 记录测试结果\n"
)


def _steps_pool():
    return (
        evidence(label="S1", content="测试环境登录验证码固定为123456。"),
        evidence(label="S2", content="保单贷款金额不超过现金价值的80%，期限最长6个月。"),
    )


def test_each_cited_list_item_is_its_own_claim_without_its_list_syntax() -> None:
    answer, claims = _parse(_STEPS, _steps_pool())

    assert [claim.text for claim in claims] == [
        "登录测试环境，输入验证码123456",
        "申请金额不超过现金价值的80%",
        "期限最长6个月",
    ]
    assert [claim.evidence_labels for claim in claims] == [("S1",), ("S2",), ("S2",)]
    # List markup and the uncited (generated) steps stay in the answer verbatim.
    assert answer == (
        "验证步骤：\n\n"
        "1. 登录测试环境，输入验证码123456 \n"
        "2. 打开保单贷款页面\n"
        "3) 申请金额不超过现金价值的80%\n"
        "   - 期限最长6个月\n"
        "* 记录测试结果\n"
    )


def test_cited_list_items_resolve_to_exactly_one_answer_span() -> None:
    from tap.modules.knowledge.application.retrieve import _resolve_generated_claims
    from tap.modules.knowledge.ports.models import AnswerGeneration

    answer, claims = _parse(_STEPS, _steps_pool())
    from dataclasses import replace

    pool = tuple(
        replace(item, citation_id=f"citation-{item.evidence_label}") for item in _steps_pool()
    )
    resolved = _resolve_generated_claims(
        AnswerGeneration(answer, claims, "qwen-plus", "grounded-answer-v2", "req-1"),
        pool,
        id_factory=iter(("c1", "c2", "c3")).__next__,
    )

    assert resolved is not None
    assert [answer[claim.answer_start : claim.answer_end] for claim in resolved] == [
        claim.text for claim in claims
    ]


def test_a_label_ending_one_list_item_never_cites_the_next_item() -> None:
    answer, claims = _parse(
        "- 验证码固定为123456[S1]\n- 页面右上角有提交按钮\n- 期限最长6个月[S2]",
        _steps_pool(),
    )

    assert [(claim.text, claim.evidence_labels) for claim in claims] == [
        ("验证码固定为123456", ("S1",)),
        ("期限最长6个月", ("S2",)),
    ]
    assert "页面右上角有提交按钮" in answer


def test_markers_inside_code_in_list_items_are_not_citations() -> None:
    answer, claims = _parse(
        "- 执行 `login --code 123456 [S1]`\n"
        "```\n- 期限最长6个月[S2]\n```\n"
        "- 验证码固定为123456[S1]",
        _steps_pool(),
    )

    assert [claim.text for claim in claims] == ["验证码固定为123456"]
    assert "`login --code 123456 [S1]`" in answer
    assert "- 期限最长6个月[S2]\n```" in answer


def test_answer_contract_cites_spans_and_leaves_composed_text_uncited() -> None:
    """Span-level citations: the task contracts no longer forbid list items,
    ask for labels right after each supported sentence or list item, and keep
    composed or inferred text (e.g. generated test steps) unlabelled."""
    from tap.modules.knowledge.adapters import litellm as knowledge_litellm

    contract = knowledge_litellm._ANSWER_PROMPT
    assert "list item" in contract and "never as a quotation or list item" not in contract
    assert "numbered step" in contract
    assert "carries no label" in contract
    # Real qwen-plus labelled only a list's last item; each item cites itself.
    assert "a label after the last item does not cite the items before it" in contract
    assert "never put a label on text the evidence does not support" in contract
    assert knowledge_litellm._INSUFFICIENT_EVIDENCE_SENTINEL in contract
    for sibling in (
        knowledge_litellm._FLOWCHART_ANSWER_PROMPT,
        knowledge_litellm._INSIGHTS_ANSWER_PROMPT,
    ):
        assert "complete sentence" not in sibling
        assert "sentence or list item" in sibling


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        "期限最长6个月[S2]。\n\nINSUFFICIENT_EVIDENCE",
        "期限最长6个月[S2]。申请流程未说明[INSUFFICIENT_EVIDENCE]。",
        "期限最长6个月[S2]。申请流程未说明INSUFFICIENT_EVIDENCE。",
    ],
)
async def test_a_stray_abstention_sentinel_never_reaches_a_grounded_answer(reply) -> None:
    """Real qwen-plus sometimes appends the abstention sentinel to a partly
    grounded answer; with uncited text now shown, it must not leak."""

    class Gateway:
        async def chat_stream(self, request) -> AsyncIterator[ModelStreamEvent]:
            yield ModelTextDelta(reply)
            yield _result(reply)

    generation = await _answer_gateway(Gateway()).answer("问题", _steps_pool(), "quick-hybrid-v1")

    assert "INSUFFICIENT_EVIDENCE" not in generation.text
    assert [claim.text for claim in generation.claims] == ["期限最长6个月。"]


def test_a_list_item_claim_ends_at_its_last_marker() -> None:
    """Text after the last marker of a list item is uncited, not part of the claim."""
    answer, claims = _parse(
        "- 期限最长6个月[S2]，到期后可再联系客服\n- 验证码固定为123456[S1]。", _steps_pool()
    )

    assert [(claim.text, claim.evidence_labels) for claim in claims] == [
        ("期限最长6个月", ("S2",)),
        ("验证码固定为123456。", ("S1",)),
    ]
    assert answer == "- 期限最长6个月，到期后可再联系客服\n- 验证码固定为123456。"


@pytest.mark.asyncio
@pytest.mark.parametrize("template_version", ["3", "4"])
async def test_templated_answers_carry_exactly_one_citation_format_rule(template_version) -> None:
    """Span-level citations fix round 1: a plan-bound answer gets its citation
    format from the pinned template only -- once for a new (V4) plan, and a
    replayed V3 plan is not contradicted by the span rule."""
    from tap.modules.knowledge.application.answer_templates import (
        SPAN_CITATION_RULE,
        assemble_answer,
        get_template,
    )

    template = get_template("general", template_version)
    answer_input = assemble_answer(
        template_id="general",
        template_version=template_version,
        template_digest=template.digest,
        original_question="问题",
        standalone_question="问题",
        evidence_map={"q1": ("S1",)},
    )
    captured: list[object] = []

    class Gateway:
        async def chat_stream(self, request) -> AsyncIterator[ModelStreamEvent]:
            captured.append(request)
            yield ModelTextDelta("验证码固定为123456[S1]。")
            yield _result("验证码固定为123456[S1]。")

    models = _answer_gateway(Gateway())
    await models.answer("问题", _steps_pool(), "quick-hybrid-v1", answer_input=answer_input)
    await models.answer("问题", _steps_pool(), "quick-hybrid-v1")

    templated, plain = captured[0].prompt, captured[1].prompt
    assert plain.count(SPAN_CITATION_RULE) == 1
    if template_version == "4":
        assert templated.count(SPAN_CITATION_RULE) == 1
    else:
        assert SPAN_CITATION_RULE not in templated
        assert "never as a quotation or list item" in templated


def test_both_answer_contracts_ask_to_correct_a_contradicted_premise() -> None:
    """Span-level citations fix round 1 (fact-recall regression): the former
    prose-only sentence rule made the model restate a corrected value as a
    cited sentence as a side effect; once lists and terse answers are allowed
    it answers only the literal question and leaves a false premise standing
    (c-001: 0 of 32 real runs). Correcting it is now an explicit rule in both
    the untemplated and the templated contract."""
    from tap.modules.knowledge.adapters import litellm as knowledge_litellm

    rule = "If the query assumes a fact or value that the evidence contradicts"
    assert rule in knowledge_litellm._ANSWER_PROMPT
    assert rule in knowledge_litellm._TEMPLATED_ANSWER_PROMPT
