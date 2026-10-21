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
