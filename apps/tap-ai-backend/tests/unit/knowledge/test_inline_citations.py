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


@pytest.mark.parametrize(
    ("text", "claim", "answer"),
    [
        (
            "- 期限最长6个月[S2]。具体以保单为准。",
            "期限最长6个月。",
            "- 期限最长6个月。具体以保单为准。",
        ),
        (
            "- The term is at most 6 months[S2]. See the policy.",
            "The term is at most 6 months.",
            "- The term is at most 6 months. See the policy.",
        ),
    ],
    ids=("zh", "en"),
)
def test_a_list_item_claim_keeps_the_terminator_after_its_marker(text, claim, answer) -> None:
    parsed_answer, claims = _parse(text, _steps_pool())

    assert [item.text for item in claims] == [claim]
    assert parsed_answer == answer


@pytest.mark.parametrize(
    ("text", "answer"),
    [
        (
            "- 尊享智选康惠团体医疗保险的被保险人为团体成员[S1]。\n"
            "- 尊享智选康惠团体医疗保险的投保年龄为0周岁至70周岁[S2]。具体以保单为准。",
            "- 尊享智选康惠团体医疗保险的被保险人为团体成员。\n- 具体以保单为准。",
        ),
        (
            "1. 尊享智选康惠团体医疗保险的被保险人为团体成员[S1]。\n"
            "2. 尊享智选康惠团体医疗保险的投保年龄为0周岁至70周岁[S2]，另见条款",
            "1. 尊享智选康惠团体医疗保险的被保险人为团体成员。\n2. 另见条款",
        ),
    ],
    ids=("bullet", "numbered"),
)
def test_a_rejected_span_keeps_the_list_prefix_of_the_text_left_on_its_line(text, answer) -> None:
    from test_grounded_output import GROUP, LIFE

    parsed_answer, claims = _parse(text, (GROUP, LIFE))

    assert [item.text for item in claims] == ["尊享智选康惠团体医疗保险的被保险人为团体成员。"]
    assert parsed_answer == answer


def test_premise_and_unstated_value_rules_are_scoped() -> None:
    from tap.modules.knowledge.adapters import litellm as knowledge_litellm
    from tap.modules.knowledge.domain.abstention import INSUFFICIENT_EVIDENCE_SENTINEL

    for contract in (knowledge_litellm._ANSWER_PROMPT, knowledge_litellm._TEMPLATED_ANSWER_PROMPT):
        assert (
            "lead with the evidence's value and never restate the query's assumed value as true"
            in contract
        )
        # The abstention condition comes first; the "does not state it"
        # sentence applies only when another selected document does state
        # the value (stated after it, b-001 kept writing a cited
        # "not mentioned" sentence instead of abstaining: 2 of 6 real runs).
        abstain = (
            "Otherwise, if the query asks only for a specific fact or value (such as an address, "
            "date, amount, duration or name) that no supplied evidence states, "
            f"output exactly {INSUFFICIENT_EVIDENCE_SENTINEL}."
        )
        scoped = "When some selected documents state that value and another selected document"
        assert abstain in contract and scoped in contract
        assert contract.index(abstain) < contract.index(scoped)
    assert knowledge_litellm._INSUFFICIENT_EVIDENCE_SENTINEL is INSUFFICIENT_EVIDENCE_SENTINEL


def test_a_composed_request_is_answered_and_only_a_missing_lookup_abstains() -> None:
    """D1-R: the unstated-value abstention made real qwen-plus abstain on a
    request to compose test checks and Playwright steps from sufficient
    evidence (it read the requested checks as values the evidence must
    state). Abstention is scoped to lookups; a composed request is answered
    with cited facts and uncited composed content."""
    from tap.modules.knowledge.adapters import litellm as knowledge_litellm
    from tap.modules.knowledge.domain.abstention import INSUFFICIENT_EVIDENCE_SENTINEL

    for contract in (knowledge_litellm._ANSWER_PROMPT, knowledge_litellm._TEMPLATED_ANSWER_PROMPT):
        assert "If no supplied evidence states the value the query asks about" not in contract
        # "cannot answer the query" made a tool-offering governed Turn abstain
        # on Q3 (real qwen-plus ablation); abstention is now only for
        # conflicts, irrelevant evidence and unstated looked-up values.
        assert "cannot answer the query" not in contract
        assert (
            f"If the evidence conflicts, output exactly {INSUFFICIENT_EVIDENCE_SENTINEL} and "
            "nothing else." in contract
        )
        assert (
            "A request to compose something from the evidence, such as test checks, steps, a "
            "script, a plan, a summary or a comparison, is answerable whenever supplied evidence "
            "is relevant to it, even though no evidence states what you compose" in contract
        )
        assert "cite the evidence facts you use, leave what you compose uncited" in contract
        assert "abstain only when no supplied evidence is relevant to the request" in contract
        # Order (fix round 1, real ablation on the governed tool path): the
        # premise-correction rule, then the composed rule, end the opening
        # (Q3 16/20 over two batches, c-001 10/10); with the composed rule
        # just before the lookup rule instead, Q3 answered 8/30. The
        # exemption is stated once, in the composed rule ("abstain only
        # when no supplied evidence is relevant"); the lookup rule's
        # "Otherwise" only continues the per-document value rules before it.
        opening = knowledge_litellm._ANSWER_PROMPT_OPENING
        premise = opening.index("If the query assumes")
        correction = opening.index(
            "This correction is part of answering the query, never an ancillary fact, and comes "
            "first even when the rest of the query is answerable."
        )
        composed = opening.index("A request to compose something")
        assert premise < correction < composed
        assert opening.endswith(
            "abstain only when no supplied evidence is relevant to the request."
        )
        assert contract.index("A request to compose something") < contract.index(
            "Otherwise, if the query asks only for a specific fact or value (such as an address, "
            "date, amount, duration or name) that no supplied evidence states, "
            f"output exactly {INSUFFICIENT_EVIDENCE_SENTINEL}."
        )
        assert contract.count("A request to compose something") == 1
        assert "this never applies to a request" not in contract
    # Sibling task prompts exempt composed requests from their own abstention.
    for sibling in (
        knowledge_litellm._RELATION_ANSWER_PROMPT,
        knowledge_litellm._FLOWCHART_ANSWER_PROMPT,
    ):
        assert (
            "unless the query asks you to compose something from the evidence, output "
            f"{INSUFFICIENT_EVIDENCE_SENTINEL}" in sibling
        )


def test_evidence_labels_appear_only_as_markers() -> None:
    """N1: real answers mentioned labels in code comments ("依据 S9/S1/S8") and
    closing remarks ("（S1–S10）"), and wrote a fake "[webapp-testing skill]"."""
    from tap.modules.knowledge.adapters import litellm as knowledge_litellm

    for contract in (knowledge_litellm._ANSWER_PROMPT, knowledge_litellm._TEMPLATED_ANSWER_PROMPT):
        assert (
            "Evidence labels appear only as bracketed markers right after the text they "
            "support; never mention a label anywhere else, including inside code, comments or "
            "source lists, and never write a bracketed source, document or Skill name as a "
            "citation." in contract
        )


def _three_label_pool():
    return (
        *_steps_pool(),
        evidence(label="S3", content="严重缺陷需在24小时内修复。"),
    )


async def _final_answer(reply: str, pool, question: str = "问题") -> str:
    class Gateway:
        async def chat_stream(self, request) -> AsyncIterator[ModelStreamEvent]:
            yield ModelTextDelta(reply)
            yield _result(reply)

    generation = await _answer_gateway(Gateway()).answer(question, pool, "quick-hybrid-v1")
    assert generation.claims
    return generation.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "answer"),
    [
        # (a) a bracketed or parenthesized list, with or without a cue inside
        (
            "- 验证码固定为123456[S1]。\n\n以上检查点均有资料支撑（S1–S2）。",
            "- 验证码固定为123456。\n\n以上检查点均有资料支撑。",
        ),
        ("期限最长6个月[S2]。(S1, S2)", "期限最长6个月。"),
        ("期限最长6个月[S2]（来源：S1/S2）。", "期限最长6个月。"),
        # (b) a list right after a citation cue word goes with its cue
        ("期限最长6个月[S2]。以上步骤依据 S2/S1 整理。", "期限最长6个月。以上步骤整理。"),
        ("期限最长6个月[S2]。参见 S1、S2。", "期限最长6个月。"),
        ("The term is 6 months[S2]. See S1, S2.", "The term is 6 months."),
        # (c) a list standing alone on its own line
        ("期限最长6个月[S2]。\n\nS1/S2\n\n验证码固定为123456[S1]。", None),
    ],
)
async def test_a_bare_label_list_used_as_a_citation_is_removed(reply, answer) -> None:
    text = await _final_answer(reply, _steps_pool())

    if answer is None:
        assert "S1/S2" not in text and "期限最长6个月。" in text and "123456。" in text
    else:
        assert text == answer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tail",
    [
        "\n\n```python\n# 依据 S1/S2\npage.fill('#otp', '123456')\n```",
        "\n\n~~~\n# 依据 S1/S2\n~~~",
        "\n\n```python\n# 依据 S1/S2 (unclosed fence)",
        "\n\n    # 依据 S1/S2\n    page.fill('#otp', '123456')",
        "运行 `check(S1/S2)` 即可。",
        "型号 S1000/S2000 与 PS1/S2 不同。",
        "S1 档位单独适用。",
        "参见 S1/S9 不是本次标签。",
        "Sales/Service 流程。",
        # Domain text that merely contains a label-like list stays.
        "S1/S2 级缺陷需在24小时内修复。",
        "缺陷分 S1-S4 四级。",
        "比较 S1/S2 版本差异。",
    ],
)
async def test_label_like_domain_text_is_kept(tail) -> None:
    pool = (*_three_label_pool(), evidence(label="S4", content="缺陷分级。"))

    assert await _final_answer("期限最长6个月[S2]。" + tail, pool) == "期限最长6个月。" + tail


@pytest.mark.asyncio
async def test_a_cited_claim_with_a_label_like_list_keeps_it() -> None:
    text = await _final_answer("S1/S2 级缺陷需在24小时内修复[S3]。", _three_label_pool())

    assert text == "S1/S2 级缺陷需在24小时内修复。"


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["question", "evidence"])
async def test_a_list_that_appears_in_the_question_or_evidence_is_kept(where) -> None:
    question = "依据 S1/S2 标准如何处理？" if where == "question" else "问题"
    pool = _steps_pool()
    if where == "evidence":
        pool = (*pool, evidence(label="S3", content="缺陷依据 S1/S2 标准分级。"))
    reply = "期限最长6个月[S2]。依据 S1/S2 标准处理。"

    assert await _final_answer(reply, pool, question) == "期限最长6个月。依据 S1/S2 标准处理。"


def test_relation_labels_in_domain_text_are_kept() -> None:
    from tap.modules.knowledge.adapters.litellm import _without_bare_label_lists

    text = "比较 R1/R2 版本差异[R3]。"

    assert _without_bare_label_lists(text, frozenset({"R1", "R2", "R3"}), ()) == text
    assert _without_bare_label_lists("关系，见 R1/R2。", frozenset({"R1", "R2"}), ()) == "关系。"


_FOUR = frozenset({"S1", "S2", "S3", "S4"})


@pytest.mark.parametrize(
    "text",
    [
        "常见 S1/S2 缺陷需要优先修复。",
        "详见 S1/S2 级缺陷的处理规定。",
        "意见 S1、S2 已合并。",
        "根据 S1-S4 四级标准评估。",
        "Per S1/S2 severity rules, fix them first.",
        "可见 S1/S2 两级。",
        "关系见 S1/S2 两条。",
    ],
)
def test_a_cue_inside_a_word_or_an_unclosed_cued_list_is_kept(text) -> None:
    """Fix round 2: 见 matched inside 常见/详见/意见/可见, and a cued run was
    removed even when the clause went on ("根据 S1-S4 四级标准")."""
    from tap.modules.knowledge.adapters.litellm import _without_bare_label_lists

    assert _without_bare_label_lists(text, _FOUR, ()) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("如下所示，参见 S1/S2。", "如下所示。"),
        ("期限最长6个月。详见 S1/S2。", "期限最长6个月。"),
        ("期限最长6个月。依据 S1/S2，可得结论。", "期限最长6个月。可得结论。"),
        ("以上步骤依据 S2/S1 整理。", "以上步骤整理。"),
        ("Fix them first, see S1/S2.", "Fix them first."),
        ("期限最长6个月；见 S1、S2", "期限最长6个月"),
    ],
)
def test_a_closed_cued_list_is_removed_with_its_dangling_separator(text, expected) -> None:
    from tap.modules.knowledge.adapters.litellm import _without_bare_label_lists

    assert _without_bare_label_lists(text, _FOUR, ()) == expected


@pytest.mark.parametrize(
    "code",
    ["~~~\n验证码固定为123456[S1]\n~~~\n\n", "```python\n# 验证码固定为123456[S1]\n"],
)
def test_markers_in_tilde_or_unclosed_fences_are_not_claims(code) -> None:
    """The label-list filter and the marker parser share one code grammar:
    ~~~ fences and a fence left open to the end are code too."""
    text = "期限最长6个月[S2]。\n\n" + code
    answer, claims = _parse(text, _steps_pool())

    assert [claim.evidence_labels for claim in claims] == [("S2",)]
    # The code is kept verbatim, not treated as a rejected cited span.
    assert answer == "期限最长6个月。\n\n" + code


def test_the_value_lookup_abstention_is_scoped_to_a_specific_fact() -> None:
    """N5: on the governed tool path the generic "looks up a fact or value"
    abstention still beat the composed-request rule (Q3 7/10; without it 9/10;
    scoped to a specific fact 10/10, b-001 still 6/6 abstained)."""
    from tap.modules.knowledge.adapters import litellm as knowledge_litellm

    for contract in (knowledge_litellm._ANSWER_PROMPT, knowledge_litellm._TEMPLATED_ANSWER_PROMPT):
        assert "looks up a fact or value" not in contract


_CODE_LABELS = frozenset({"S1", "S2", "S8", "S9"})


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (
            "```python\n# 1. 使用 TEST_ 账号登录（S9）\n"
            "page.fill('#username', 'TEST_01')  # 测试账号（S1, S8）\n```",
            "```python\n# 1. 使用 TEST_ 账号登录\n"
            "page.fill('#username', 'TEST_01')  # 测试账号\n```",
        ),
        ("```js\n// 登录 (S1/S2)\nlogin();\n```", "```js\n// 登录\nlogin();\n```"),
        ("~~~\n# 验证码固定（S2）\n~~~", "~~~\n# 验证码固定\n~~~"),
        # Real round-3 probe forms: a cue inside the parentheses, and a label
        # prefixing a description (only the label goes).
        ("```python\n# 2. 进入快速通道（依据 S1, S8）\n```", "```python\n# 2. 进入快速通道\n```"),
        (
            "```python\n# 4. 处理验证码（S2：固定值 123456）\n```",
            "```python\n# 4. 处理验证码（固定值 123456）\n```",
        ),
        ("说明。\n\n    # 重置数据（S9）\n    reset()", "说明。\n\n    # 重置数据\n    reset()"),
    ],
)
def test_a_parenthesized_label_in_a_code_comment_is_removed(code, expected) -> None:
    """N1-R: the model kept writing "（S9）" and "（S1, S8）" into code comments."""
    from tap.modules.knowledge.adapters.litellm import _without_bare_label_lists

    assert _without_bare_label_lists(code, _CODE_LABELS, ()) == expected


@pytest.mark.parametrize(
    "code",
    [
        "```python\nS1 = load('S1')\nresult = run(S1)  # 运行 S1 档位\n```",
        "```python\nvalue = check(S1, S2)\n```",
        "```python\npage.fill('#otp（S1）', '123456')\n```",
        "```js\nfetch('http://host/a(S1)')  // 请求\n```",
        "```python\n# 档位（S5）\n```",
        "```python\n# 档位（S1 级）\n```",
        "运行 `step（S1）` 即可。",
    ],
)
def test_code_label_tokens_outside_a_comment_label_are_kept(code) -> None:
    from tap.modules.knowledge.adapters.litellm import _without_bare_label_lists

    assert _without_bare_label_lists(code, _CODE_LABELS, ()) == code


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("按 Skill 指引执行[webapp-testing]。", "按 Skill 指引执行 webapp-testing。"),
        ("参考 [webapp-testing skill] 的做法。", "参考 webapp-testing skill 的做法。"),
        ("见 [ webapp-testing ]。", "见 webapp-testing。"),
    ],
)
def test_a_bracketed_skill_name_becomes_plain_text(text, expected) -> None:
    """N1-R: "[webapp-testing]" reads like a citation; it is not one. Only the
    brackets go (the conservative option), so no content is lost."""
    from tap.modules.knowledge.adapters.litellm import _without_skill_pseudo_citations

    assert _without_skill_pseudo_citations(text, ("webapp-testing",)) == expected


@pytest.mark.parametrize(
    "text",
    [
        "参见 [webapp-testing](https://example.test/skill) 文档。",
        "勾选 [x] 后提交。",
        "其他 [other-skill] 标记保留。",
        "```python\nitems = ['webapp-testing']\nx = d[webapp-testing]\n```",
    ],
)
def test_other_brackets_are_not_skill_pseudo_citations(text) -> None:
    from tap.modules.knowledge.adapters.litellm import _without_skill_pseudo_citations

    assert _without_skill_pseudo_citations(text, ("webapp-testing",)) == text


@pytest.mark.asyncio
async def test_a_governed_answer_drops_comment_labels_and_skill_brackets() -> None:
    reply = (
        "期限最长6个月[S2]。按 Skill 指引执行[Test steps]。\n\n"
        "```python\n# 登录测试环境（S1）\npage.goto(url)\n```"
    )

    class Gateway:
        async def chat_stream(self, request) -> AsyncIterator[ModelStreamEvent]:
            yield ModelTextDelta(reply)
            yield _result(reply)

    generation = await _answer_gateway(Gateway()).answer(
        "问题", _steps_pool(), "quick-hybrid-v1", governance=_governance()
    )

    assert generation.text == (
        "期限最长6个月。按 Skill 指引执行 Test steps。\n\n"
        "```python\n# 登录测试环境\npage.goto(url)\n```"
    )


def test_a_sentence_about_methods_or_a_skill_carries_no_label() -> None:
    """N6: a closing sentence about networkidle and the Skill's best practice
    cited two unrelated test-environment rules."""
    from tap.modules.knowledge.adapters import litellm as knowledge_litellm

    for contract in (knowledge_litellm._ANSWER_PROMPT, knowledge_litellm._TEMPLATED_ANSWER_PROMPT):
        assert (
            "Label only text that restates supplied evidence; a sentence about methods, tools or "
            "a Skill's guidance carries no label." in contract
        )


@pytest.mark.parametrize(
    "code",
    [
        # An escaped quote does not end the string, so "#" is still inside it.
        '```python\ns = "a\\"b # (S1)"\n```',
        # A label right after a call name is an argument list, not a citation.
        "```python\n# call check(S1) before submit\n```",
        "```python\n# wraps validate(S1, S2)\n```",
        "```js\n// see obj.run(S1)\n```",
    ],
)
def test_escaped_quotes_and_call_arguments_in_code_are_kept(code) -> None:
    from tap.modules.knowledge.adapters.litellm import _without_bare_label_lists

    assert _without_bare_label_lists(code, _CODE_LABELS, ()) == code


def test_a_comment_after_an_escaped_quote_still_loses_its_label() -> None:
    from tap.modules.knowledge.adapters.litellm import _without_bare_label_lists

    code = "```python\nx = 'it\\'s' # note (S1)\n```"

    assert _without_bare_label_lists(code, _CODE_LABELS, ()) == (
        "```python\nx = 'it\\'s' # note\n```"
    )


_ROUND4_LABELS = frozenset(f"S{number}" for number in range(1, 10))


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # Exact capability-4 round-4 Q1 comment lines.
        (
            'page.fill("#smsCode", "123456")  # 固定验证码 [S2]',
            'page.fill("#smsCode", "123456")  # 固定验证码',
        ),
        ("# 1. 使用 TEST_ 账号登录（满足 S9）", "# 1. 使用 TEST_ 账号登录"),
        (
            "# 6. 输入银行信息并提交（验证第三方账户被拦截）[S7][S8]",
            "# 6. 输入银行信息并提交（验证第三方账户被拦截）",
        ),
        (
            "# 2. 导航至小额理赔快速通道（隐含险种过滤：仅个人意外险/医疗险可见）[S1][S3]",
            "# 2. 导航至小额理赔快速通道（隐含险种过滤：仅个人意外险/医疗险可见）",
        ),
        ("// 提交前校验（对应 S1、S2）", "// 提交前校验"),
        ("# 上传材料（参考 S8）", "# 上传材料"),
        ("# 时效（符合 S1）", "# 时效"),
        ("# 3. 进入通道，验证时效提示 [S1]。", "# 3. 进入通道，验证时效提示。"),
        # A trailing comment after code that is only a marker run (the most
        # common Q1 leak in the 20-run recheck) goes with its comment token.
        (
            'assert page.is_visible("text=审核时限为3个工作日")  # [S1]',
            'assert page.is_visible("text=审核时限为3个工作日")',
        ),
        ('page.fill("#sms-code", "123456")  # [S2]', 'page.fill("#sms-code", "123456")'),
        # A marker run after a closed call at the end of a code line.
        (
            'assert not page.locator("text=第三方账户").is_visible() [S7]',
            'assert not page.locator("text=第三方账户").is_visible()',
        ),
        # A marker closing the comment's own parentheses.
        (
            "# 4. 跳过纸质材料上传（验证无强制字段 [S1][S8]）",
            "# 4. 跳过纸质材料上传（验证无强制字段）",
        ),
        ("// 提交银行信息 [S7][S8]", "// 提交银行信息"),
        # In-process Q1 probe forms after the first fix.
        (
            "# （依据S1：无需邮寄纸质材料；故该页应无“身份证复印件”等必填控件）",
            "# （无需邮寄纸质材料；故该页应无“身份证复印件”等必填控件）",
        ),
        (
            'assert len(page.locator("input[required]").all()) == 0, "不应存在" [S1][S8]',
            'assert len(page.locator("input[required]").all()) == 0, "不应存在"',
        ),
        (
            "# 2. 导航至小额理赔入口（需 recon 后替换 selector；S1, S3）",
            "# 2. 导航至小额理赔入口（需 recon 后替换 selector）",
        ),
        (
            'assert page.inner_text("text=审核将在3个工作日内完成").strip() != "" [S1]',
            'assert page.inner_text("text=审核将在3个工作日内完成").strip() != ""',
        ),
    ],
)
def test_bracketed_markers_and_cued_labels_in_code_comments_are_removed(line, expected) -> None:
    """N1-R2: inside code a `[Sn]` marker is never rendered as a citation, so
    the round-4 comments showed `[S2]`, `[S7][S8]` and `（满足 S9）` raw."""
    from tap.modules.knowledge.adapters.litellm import _without_bare_label_lists

    block = "```python\n" + line + "\n```"

    assert (
        _without_bare_label_lists(block, _ROUND4_LABELS, ()) == "```python\n" + expected + "\n```"
    )


@pytest.mark.parametrize(
    "line",
    [
        # An index glued to a name or a call is code, not a citation.
        "# 读取 items[S1] 的值",
        "value = data[S1]  # 取值",
        "value = fetch()[S1]",
        "value = table[0][S1]",
        "# call check(S1) before submit",
        "# 未提供的标签 [S12]",
        "# 满足条件时提交（满足 S1 级要求）",
        "# 参数（mode，S1）",
        "# 选项（A；S9 级）",
        "# 参考（手册）与依据（规范）",
    ],
)
def test_indexes_unsupplied_markers_and_label_like_text_in_code_are_kept(line) -> None:
    from tap.modules.knowledge.adapters.litellm import _without_bare_label_lists

    block = "```python\n" + line + "\n```"

    assert _without_bare_label_lists(block, _ROUND4_LABELS, ()) == block


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # Exact capability-4 round-5 Q1 lines: markers in string literals (N8),
        # in the middle of a comment (N1-R3) and after a cue (N9).
        (
            'assert page.is_visible("text=审核时限：3个工作日"), "未显示3个工作日审核承诺[S1]"',
            'assert page.is_visible("text=审核时限：3个工作日"), "未显示3个工作日审核承诺"',
        ),
        (
            'assert not page.is_visible("text=现金领取"), "错误显示现金领取选项[S7]"',
            'assert not page.is_visible("text=现金领取"), "错误显示现金领取选项"',
        ),
        ("# 2. 使用TEST_账号登录（依据[S9]）", "# 2. 使用TEST_账号登录"),
        ("# 7. 填写银行账户（依据[S8][S7]）", "# 7. 填写银行账户"),
        (
            "# 3. 进入小额理赔快速通道（依据[S1]，需先确保保单类型为个人意外险/医疗险[S3]）",
            "# 3. 进入小额理赔快速通道（需先确保保单类型为个人意外险/医疗险）",
        ),
        (
            "# 6. 处理短信验证（依据[S2]：固定验证码123456）",
            "# 6. 处理短信验证（固定验证码123456）",
        ),
        (
            "assert page.is_hidden('text=身份证复印件') is True  # [S8]中列出但[S1]豁免",
            "assert page.is_hidden('text=身份证复印件') is True  # 中列出但豁免",
        ),
        # Round-4 Q1 lines not covered by the earlier fix.
        (
            "# 8. 验证提交成功页 & 支付方式说明 [S7]",
            "# 8. 验证提交成功页 & 支付方式说明",
        ),
        # Every other supplied marker not glued to a name goes too.
        ('page.fill("#otp", "[S2]")  # 输入验证码', 'page.fill("#otp", "")  # 输入验证码'),
        ("value = data [S1]", "value = data"),
        ('text = "备注 [S1]"', 'text = "备注"'),
        ('text = "未闭合 [S1]', 'text = "未闭合'),
        ('assert ok, "未显示银行转账支付方式[S7]"', 'assert ok, "未显示银行转账支付方式"'),
        ("# pattern [S1]", "# pattern"),
        ("# group [S1] captures the id", "# group captures the id"),
        ("# - [S1] pending review", "# - pending review"),
        ("# checklist: [S1] done, [S2] todo", "# checklist: done, todo"),
        ("# steps: [S1][S2]", "# steps:"),
        ("# 例如 [ S1 ]", "# 例如"),
        ("x = 1  # 见 [S1]、[S2]", "x = 1"),
        ("# 登录，参见 [S9]。", "# 登录。"),
        ("x = 1  # [S1][S12]", "x = 1  # [S12]"),
        ("# 值 [S8][S12][S7]", "# 值 [S12]"),
        ("# 值 [S12][S7]", "# 值 [S12]"),
        ("# 登录（依据（见[S1]））", "# 登录"),
        ("// 提交 [R1] [I2]", "// 提交"),
    ],
)
def test_every_supplied_marker_in_a_code_block_is_removed(line, expected) -> None:
    """N8/N1-R3/N9: inside code a marker is never rendered as a citation, so
    every supplied `[Sn]`/`[Rn]`/`[In]` goes -- in comments, strings or after
    code -- unless it is glued to a name or call as an index (`items[S1]`).
    A cue left with nothing to cite goes with it."""
    from tap.modules.knowledge.adapters.litellm import _without_bare_label_lists

    labels = _ROUND4_LABELS | {"R1", "I2"}
    block = "```python\n" + line + "\n```"

    assert _without_bare_label_lists(block, labels, ()) == "```python\n" + expected + "\n```"


def test_a_code_line_left_empty_by_marker_removal_is_dropped() -> None:
    from tap.modules.knowledge.adapters.litellm import _without_bare_label_lists

    block = "```python\nstep()\n\n    # [S1]\n    # 依据 [S2]\nnext()\n```"

    assert _without_bare_label_lists(block, _ROUND4_LABELS, ()) == (
        "```python\nstep()\n\nnext()\n```"
    )


def test_a_trailing_source_legend_is_removed_with_its_lead_in() -> None:
    """N10: the round-5 Q1-a answer closed with a legend whose lines start with
    a marker; claim checks dropped all but its last line ("支持重置后稳定性。")."""
    from tap.modules.knowledge.adapters.litellm import _without_bare_label_lists

    text = (
        "再依据输出结果修正选择器。\n\n--- \n\n"
        "所有检查点与步骤均严格引用所选资料中的业务规则：  \n"
        "[S1] 定义通道适用条件与时效；  \n"
        "[S2] 提供验证码固定值；  \n"
        "- [S9]：规定自动化账号命名；  \n"
        "[S6] 支持重置后稳定性。"
    )

    assert _without_bare_label_lists(text, _ROUND4_LABELS, ()) == (
        "再依据输出结果修正选择器。\n\n--- \n"
    )


@pytest.mark.parametrize(
    "text",
    [
        # A marker closing a claim on the line before is not a legend.
        "审核时限为3个工作日。\n[S1]\n",
        # A line that starts with an unsupplied label is ordinary text.
        "[S12] 号缺陷已关闭。",
        # Only a legend that closes the answer goes; a list or a wrapped line
        # in the middle of the answer is kept.
        "## 核心规则\n\n关键规则如下：\n- [S1] 金额低于 5000 元走快速通道。\n"
        "- [S2] 需上传身份证。\n\n## 测试步骤\n1. 登录。",
        "审核需3个工作日 [S1]。审核规则\n[S2] 另有说明，需人工复核。\n下一段。",
        "步骤如下：\n1. [S1] 登录系统\n2. [S2] 提交申请\n\n完成后检查状态。",
        "> [S1] 原文摘录",
    ],
)
def test_text_that_is_not_a_source_legend_is_kept(text) -> None:
    from tap.modules.knowledge.adapters.litellm import _without_bare_label_lists

    assert _without_bare_label_lists(text, _ROUND4_LABELS, ()) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("本次推荐的 Skill 是 [webapp-testing]。", "本次推荐的 Skill 是 webapp-testing。"),
        (
            "可选工具包括 Playwright 与 [webapp-testing]，后者更快。",
            "可选工具包括 Playwright 与 webapp-testing，后者更快。",
        ),
        ("可以用 Skill [webapp-testing]。", "可以用 Skill webapp-testing。"),
        ("安装 skill: [webapp-testing]", "安装 skill: webapp-testing"),
        ("先完成侦察 [webapp-testing]，再执行。", "先完成侦察 webapp-testing，再执行。"),
    ],
)
def test_a_skill_name_used_in_the_sentence_keeps_its_name(text, expected) -> None:
    from tap.modules.knowledge.adapters.litellm import _without_skill_pseudo_citations

    assert _without_skill_pseudo_citations(text, ("webapp-testing",)) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Exact round-5 Q1-b lines: a bracketed Skill name after the sentence,
        # before its punctuation, is a pseudo-citation and goes entirely.
        (
            "> - 所有 `page.wait_for_load_state('networkidle')` 均遵循 Skill 要求，"
            "确保JS渲染完成后再操作 [webapp-testing]；  ",
            "> - 所有 `page.wait_for_load_state('networkidle')` 均遵循 Skill 要求，"
            "确保JS渲染完成后再操作；  ",
        ),
        (
            "实际实施前需先执行 `element_discovery.py` 进行DOM侦察 [webapp-testing]。",
            "实际实施前需先执行 `element_discovery.py` 进行DOM侦察。",
        ),
        ("按指引完成侦察 [webapp-testing]", "按指引完成侦察"),
        # Replayed Q1 forms: right after inline code or a closing parenthesis.
        (
            '可按需添加 `page.screenshot()` 或 `page.on("console", ...)` [webapp-testing]。',
            '可按需添加 `page.screenshot()` 或 `page.on("console", ...)`。',
        ),
        (
            "需配合脚本启动服务（例：`npm run dev`）[webapp-testing]。",
            "需配合脚本启动服务（例：`npm run dev`）。",
        ),
    ],
)
def test_a_skill_name_cited_at_the_end_of_a_sentence_is_removed(text, expected) -> None:
    from tap.modules.knowledge.adapters.litellm import _without_skill_pseudo_citations

    assert _without_skill_pseudo_citations(text, ("webapp-testing",)) == expected
