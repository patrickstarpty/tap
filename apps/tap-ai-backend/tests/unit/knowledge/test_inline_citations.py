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
