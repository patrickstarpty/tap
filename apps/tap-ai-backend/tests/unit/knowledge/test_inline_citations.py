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
from tap.modules.knowledge.adapters.litellm import KnowledgeModelGateway
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
    the real `[S1]` marker elsewhere in the same sentence still grounds it."""
    answer, claims = _parse(text, (evidence(label="S1"),))

    assert literal in answer
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


def _result(output: str) -> ModelResult:
    return ModelResult(
        output=output,
        actual_model="qwen-plus",
        usage=ModelUsage(),
        actual_provider="dashscope",
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
    mangle ordinary text such as a version number; "v 1.2" must survive."""
    answer, claims = _parse("当前版本为 v 1.2[S1]。", (evidence(label="S1"),))

    assert "v 1.2" in answer
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
    from tap.modules.knowledge.application.answer_templates import assemble_answer, get_template

    chunks = ["理赔需在 30 日内提交", "[S1]。"]

    class PlanBoundStreamGateway:
        async def chat_stream(self, request) -> AsyncIterator[ModelStreamEvent]:
            for chunk in chunks:
                yield ModelTextDelta(chunk)
            yield _result("".join(chunks))

    selected = get_template("general", "1")
    assembled = assemble_answer(
        template_id="general",
        template_version="1",
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
    from tap.modules.knowledge.application.answer_templates import assemble_answer, get_template

    class InsufficientEvidenceGateway:
        async def chat_stream(self, request) -> AsyncIterator[ModelStreamEvent]:
            yield ModelTextDelta("INSUFFICIENT_EVIDENCE")
            yield _result("INSUFFICIENT_EVIDENCE")

    selected = get_template("general", "1")
    assembled = assemble_answer(
        template_id="general",
        template_version="1",
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
