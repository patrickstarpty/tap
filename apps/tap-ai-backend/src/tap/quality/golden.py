"""Golden-set regression for knowledge answers through the production answer path.

The runner parses and chunks a fixed corpus with the production parser and chunker, keeps
the chunks in a process-local search index, and answers each case through
``AnswerService`` -> ``KnowledgeAPI`` -> ``KnowledgeModelGateway``. Only the model and the
storage/search backends are replaced.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from tap.modules.access.adapters.validation import (
    VALIDATION_SCOPE,
    ValidationAuthorizationPolicy,
    ValidationScopeProvider,
)
from tap.modules.access.domain.authorization import ActorPrincipal
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.adapters.litellm import LiteLLMModelGatewayConfig
from tap.modules.ai.adapters.litellm_catalog import ModelRoles
from tap.modules.ai.ports.gateway import ModelGateway
from tap.modules.knowledge.adapters.document_chunker import StructuralChunker
from tap.modules.knowledge.adapters.document_parsers import ParserRegistry
from tap.modules.knowledge.adapters.litellm import KnowledgeModelGateway
from tap.modules.knowledge.adapters.pattern_redaction import PatternEgressRedactor
from tap.modules.knowledge.api import KnowledgeAPI
from tap.modules.knowledge.application.answers import AnswerService
from tap.modules.knowledge.application.demo_policy import (
    DemoCurrentPolicyVerifier,
    build_demo_policy_context,
)
from tap.modules.knowledge.application.graph_enrichment import GraphAnswerEnricher
from tap.modules.knowledge.domain.documents import (
    PARSER_VERSION,
    ChunkDraft,
    DocumentId,
    DocumentSource,
    MediaType,
    canonical_sha256,
    logical_chunk_projection_id,
    revision_id_for,
)
from tap.modules.knowledge.domain.models import (
    AnswerRequest,
    ContentRole,
    DocumentAnchor,
    IndexRevision,
    ResourceMode,
    ResourceRef,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
)
from tap.modules.knowledge.domain.sources import legacy_source_id
from tap.modules.knowledge.ports.answers import AnswerSnapshot, ReadyDocumentRevision
from tap.modules.knowledge.ports.errors import (
    AnswerUnavailable,
    ModelUnavailable,
    SearchUnavailable,
)
from tap.modules.knowledge.ports.models import SearchExecution, SearchHit
from tap.quality.golden_graph import build_golden_graph
from tap.testing.deterministic_model_gateway import DeterministicModelGateway, overlap_terms

__all__ = [
    "CATEGORIES",
    "METRICS",
    "CaseResult",
    "GoldenCase",
    "GoldenReport",
    "compare",
    "deterministic_golden_gateway",
    "format_table",
    "load_cases",
    "run_golden",
]

CATEGORIES = ("fact", "procedure", "abstain", "relation")
METRICS = ("citationRecall", "keywordRecall", "relationRecall", "abstainAccuracy", "answered")
_PHYSICAL_INDEX = "kb_doc_v1_tapper_demo"
_SCHEMA_VERSION = "doc-schema-v1"


@dataclass(frozen=True)
class GoldenCase:
    id: str
    category: str
    question: str
    sources: tuple[str, ...]
    abstain: bool
    cited_documents: tuple[str, ...]
    must_contain: tuple[str, ...]
    relations: tuple[str, ...]


@dataclass(frozen=True)
class CaseResult:
    id: str
    category: str
    question: str
    abstained: bool
    answer: str
    cited_documents: tuple[str, ...]
    abstention_reason: str | None = None
    error: str | None = None
    relation_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class GoldenReport:
    metrics: dict[str, dict[str, float]]
    cases: tuple[CaseResult, ...] = field(default=())

    def to_json(self) -> dict[str, object]:
        return {
            "schema": "kb-golden-report-v1",
            "metrics": self.metrics,
            "cases": [
                {
                    "id": item.id,
                    "category": item.category,
                    "question": item.question,
                    "abstained": item.abstained,
                    "abstentionReason": item.abstention_reason,
                    "answer": item.answer,
                    "citedDocuments": list(item.cited_documents),
                    "error": item.error,
                    "relationTypes": list(item.relation_types),
                }
                for item in self.cases
            ],
        }


def load_cases(path: Path) -> tuple[GoldenCase, ...]:
    cases: list[GoldenCase] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(_case(json.loads(line)))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"{path.name}:{number}: invalid golden case: {error}") from None
    if len({case.id for case in cases}) != len(cases):
        raise ValueError(f"{path.name}: golden case IDs must be unique")
    return tuple(cases)


def _case(row: object) -> GoldenCase:
    if not isinstance(row, dict):
        raise TypeError("case must be an object")
    expect = row["expect"]
    if not isinstance(expect, dict):
        raise TypeError("expect must be an object")
    case = GoldenCase(
        id=_text(row["id"]),
        category=_text(row["category"]),
        question=_text(row["question"]),
        sources=_texts(row["sources"]),
        abstain=expect["abstain"],
        cited_documents=_texts(expect.get("citedDocuments", [])),
        must_contain=_texts(expect.get("mustContain", [])),
        relations=_texts(expect.get("relations", [])),
    )
    if case.category not in CATEGORIES:
        raise ValueError(f"unknown category {case.category!r}")
    if type(case.abstain) is not bool:
        raise TypeError("expect.abstain must be a boolean")
    if not case.sources or len(set(case.sources)) != len(case.sources):
        raise ValueError("sources must be a non-empty unique list")
    if not case.abstain and not case.cited_documents:
        raise ValueError("answerable cases require expect.citedDocuments")
    if not set(case.cited_documents) <= set(case.sources):
        raise ValueError("expect.citedDocuments must be listed in sources")
    return case


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected a non-empty string")
    return value


def _texts(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError("expected a list of strings")
    return tuple(_text(item) for item in value)


async def _redact(text: str) -> str:
    return await PatternEgressRedactor(max_chars=262144).redact_text(text)


def deterministic_golden_gateway() -> DeterministicModelGateway:
    """Network-free gateway answering with the best-overlapping evidence sentence."""

    config = LiteLLMModelGatewayConfig(
        base_url="http://127.0.0.1:4000",
        api_key="golden-deterministic",
        roles=ModelRoles("qwen-plus", "text-embedding-v4", None),
        embedding_dimension=1536,
    )
    return DeterministicModelGateway(
        config, scope=VALIDATION_SCOPE, redact=_redact, answer_mode="best-overlap"
    )


@dataclass(frozen=True)
class _IndexedChunk:
    filename: str
    revision: ReadyDocumentRevision
    draft: ChunkDraft
    anchor: DocumentAnchor
    vector: tuple[float, ...]
    terms: frozenset[str]


async def _ingest(
    corpus_dir: Path, filenames: Sequence[str], models: KnowledgeModelGateway
) -> tuple[dict[str, ReadyDocumentRevision], tuple[_IndexedChunk, ...]]:
    parser, chunker = ParserRegistry(), StructuralChunker()
    revisions: dict[str, ReadyDocumentRevision] = {}
    chunks: list[_IndexedChunk] = []
    for filename in sorted(set(filenames)):
        content = (corpus_dir / filename).read_bytes()
        document_id = DocumentId("doc_" + hashlib.sha256(filename.encode()).hexdigest()[:32])
        source_hash = canonical_sha256(content)
        revision_id = revision_id_for(document_id, source_hash, PARSER_VERSION)
        artifact = parser.parse(
            DocumentSource(filename, MediaType.MARKDOWN, content, document_id, revision_id)
        )
        drafts = chunker.chunk(artifact)
        embeddings = await models.embed_documents(
            tuple(draft.content for draft in drafts),
            model_alias=models.embedding_model_id,
            chunk_ids=tuple(str(draft.chunk_id) for draft in drafts),
        )
        revision = ReadyDocumentRevision(
            document_id,
            revision_id,
            source_hash,
            legacy_source_id(VALIDATION_SCOPE.project_id, document_id),
            filename=filename,
        )
        revisions[filename] = revision
        for draft, vector in zip(drafts, embeddings.vectors, strict=True):
            anchor = json.loads(draft.anchor_json)
            chunks.append(
                _IndexedChunk(
                    filename,
                    revision,
                    draft,
                    DocumentAnchor(
                        heading_path=tuple(anchor["headingPath"]),
                        page=anchor.get("page"),
                        bbox=tuple(anchor.get("bbox", ())),
                        start_offset=anchor["startOffset"],
                        end_offset=anchor["endOffset"],
                        inventory_item_id=anchor.get("inventoryItemId"),
                    ),
                    vector,
                    overlap_terms(draft.content),
                )
            )
    return revisions, tuple(chunks)


class _GoldenSearchIndex:
    """Process-local hybrid (dense + lexical, reciprocal-rank fused) doc index."""

    def __init__(self, chunks: tuple[_IndexedChunk, ...]) -> None:
        self._chunks = chunks

    async def search(self, execution: SearchExecution) -> tuple[SearchHit, ...]:
        plan = execution.plan
        scoped = {
            (resource.source_id, resource.revision, resource.source_content_hash)
            for resource in plan.resources
            if resource.mode is ResourceMode.SCOPE
        }
        candidates = [
            item
            for item in self._chunks
            if (
                item.revision.source_id,
                item.revision.revision_id,
                item.revision.source_content_hash,
            )
            in scoped
        ]
        query_terms = overlap_terms(plan.sanitized_query)
        dense = sorted(candidates, key=lambda item: -_cosine(item.vector, execution.query_vector))
        lexical = sorted(
            (item for item in candidates if item.terms & query_terms),
            key=lambda item: -len(item.terms & query_terms),
        )
        fused: dict[str, float] = {}
        for ranking in (dense, lexical):
            for rank, item in enumerate(ranking, start=1):
                key = str(item.draft.chunk_id)
                fused[key] = fused.get(key, 0.0) + 1.0 / (60 + rank)
        ordered = sorted(candidates, key=lambda item: -fused[str(item.draft.chunk_id)])
        return tuple(
            self._hit(item, rank, fused[str(item.draft.chunk_id)], execution)
            for rank, item in enumerate(ordered[: plan.candidate_limit], start=1)
        )

    @staticmethod
    def _hit(item: _IndexedChunk, rank: int, score: float, execution: SearchExecution) -> SearchHit:
        draft = item.draft
        return SearchHit(
            family=SourceFamily.DOC,
            chunk_id=str(draft.chunk_id),
            logical_chunk_id=logical_chunk_projection_id(draft.logical_chunk_id),
            title=item.filename,
            content=draft.content,
            source=SourceRevisionRef(
                source_id=item.revision.source_id or "",
                source_type="doc",
                revision_kind=RevisionKind.BLOB_VERSION,
                revision=item.revision.revision_id,
                source_content_hash=item.revision.source_content_hash,
                anchor=item.anchor,
            ),
            chunk_content_hash=draft.chunk_content_hash,
            content_role=ContentRole.SOURCE,
            index_revision=IndexRevision(
                physical_index=_PHYSICAL_INDEX,
                schema_version=_SCHEMA_VERSION,
                corpus_version=execution.plan.corpus_version,
            ),
            embedding_model_version=execution.plan.embedding_model_id,
            score=score,
            local_rank=rank,
        )


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    norm = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    return 0.0 if norm == 0 else sum(a * b for a, b in zip(left, right, strict=True)) / norm


class _GoldenRevisionRepository:
    """Document ledger and answer snapshot storage held in memory."""

    def __init__(self, revisions: Mapping[str, ReadyDocumentRevision]) -> None:
        self._revisions = tuple(revisions.values())
        self.snapshots: list[AnswerSnapshot] = []

    @property
    def scope(self) -> ProjectScopeContext:
        return VALIDATION_SCOPE

    async def load_source_revisions(
        self, source_ids: tuple[str, ...]
    ) -> tuple[ReadyDocumentRevision, ...]:
        return tuple(item for item in self._revisions if item.source_id in source_ids)

    async def load_current_source_revisions(
        self, selected: tuple[tuple[str, str, str], ...]
    ) -> tuple[ReadyDocumentRevision, ...]:
        return tuple(
            item
            for item in self._revisions
            if (item.source_id, item.revision_id, item.source_content_hash) in selected
        )

    async def load_approved_chunk_ids(
        self, item_scope: tuple[tuple[str, tuple[str, ...]], ...]
    ) -> tuple[str, ...] | None:
        return None

    async def load_ready_revisions(
        self, document_ids: tuple[str, ...]
    ) -> tuple[ReadyDocumentRevision, ...]:
        return tuple(item for item in self._revisions if item.document_id in document_ids)

    async def load_revision_selection(
        self, revision_ids: tuple[str, ...]
    ) -> tuple[ReadyDocumentRevision, ...]:
        return tuple(item for item in self._revisions if item.revision_id in revision_ids)

    async def save_answer_with_citations(self, snapshot: AnswerSnapshot) -> None:
        self.snapshots.append(snapshot)


class _GoldenIdentityRegistry:
    async def get_principal(
        self, enterprise_id: str, project_id: str, actor_id: str
    ) -> ActorPrincipal | None:
        return ActorPrincipal(
            enterprise_id=enterprise_id,
            actor_id=actor_id,
            principal_type="VALIDATION",
            enabled=True,
        )


async def run_golden(
    cases: Sequence[GoldenCase],
    *,
    corpus_dir: Path,
    gateway: ModelGateway,
    chat_alias: str = "qwen-plus",
    embedding_alias: str = "text-embedding-v4",
    embedding_dimension: int = 1536,
    timeout_seconds: float = 15.0,
) -> GoldenReport:
    models = KnowledgeModelGateway(
        gateway,
        scope=VALIDATION_SCOPE,
        redact=_redact,
        embedding_alias=embedding_alias,
        chat_alias=chat_alias,
        embedding_dimension=embedding_dimension,
        timeout_seconds=timeout_seconds,
    )
    revisions, chunks = await _ingest(
        corpus_dir, [source for case in cases for source in case.sources], models
    )
    graph = GraphAnswerEnricher(
        await build_golden_graph([(item.revision, item.draft) for item in chunks])
    )
    repository = _GoldenRevisionRepository(revisions)
    knowledge = KnowledgeAPI(
        search=_GoldenSearchIndex(chunks),
        models=models,
        policy_verifier=DemoCurrentPolicyVerifier(
            repository,
            authorization_policy=ValidationAuthorizationPolicy(_GoldenIdentityRegistry()),
            scope_provider=ValidationScopeProvider(),
        ),
        redactor=PatternEgressRedactor(),
    )
    service = AnswerService(repository=repository, knowledge=knowledge)
    filenames = {
        revision.source_id: filename
        for filename, revision in revisions.items()
        if revision.source_id is not None
    }
    results: list[CaseResult] = []
    for case in cases:
        request = AnswerRequest(
            query=case.question,
            resource_refs=tuple(
                ResourceRef(
                    family=SourceFamily.DOC,
                    source_id=revisions[source].source_id or "",
                    mode=ResourceMode.SCOPE,
                )
                for source in case.sources
            ),
        )
        try:
            if case.category == "relation":
                selected = tuple(
                    sorted(
                        (revisions[source] for source in case.sources),
                        key=lambda item: item.document_id,
                    )
                )

                async def graph_phase(evidence):
                    context = await graph.enrich(
                        VALIDATION_SCOPE,
                        tuple(item.revision_id for item in selected),
                        case.question,
                        chunk_ids=tuple(item.chunk_id for item in evidence),
                        evidence_labels={item.chunk_id: item.evidence_label for item in evidence},
                        hops=2,
                    )
                    return [dict(fact) for fact in context.facts]

                response = await service.answer_frozen(
                    request,
                    selected,
                    build_demo_policy_context(selected, corpus_version="tapper-demo-v1"),
                    governance=None,
                    graph_phase=graph_phase,
                )
            else:
                response = await service.answer(request)
        except (AnswerUnavailable, ModelUnavailable, SearchUnavailable) as error:
            results.append(
                CaseResult(case.id, case.category, case.question, False, "", (), None, str(error))
            )
            continue
        by_citation = {item.citation_id: item.source.source_id for item in response.citations}
        cited = sorted(
            {
                filenames[by_citation[citation_id]]
                for claim in response.claims
                for citation_id in claim.citation_ids
            }
        )
        results.append(
            CaseResult(
                case.id,
                case.category,
                case.question,
                response.abstained,
                response.answer,
                tuple(cited),
                None if response.abstention_reason is None else response.abstention_reason.value,
                relation_types=tuple(
                    sorted({item.relation_type for item in response.relation_citations})
                ),
            )
        )
    return GoldenReport(metrics=_metrics(cases, results), cases=tuple(results))


def _metrics(
    cases: Sequence[GoldenCase], results: Sequence[CaseResult]
) -> dict[str, dict[str, float]]:
    pairs = list(zip(cases, results, strict=True))
    groups = {
        category: [pair for pair in pairs if pair[0].category == category]
        for category in CATEGORIES
    }
    groups["total"] = pairs
    metrics: dict[str, dict[str, float]] = {}
    for name, group in groups.items():
        if not group:
            continue
        answerable = [(case, result) for case, result in group if not case.abstain]
        keyed = [(case, result) for case, result in answerable if case.must_contain]
        values: dict[str, list[float]] = {
            "citationRecall": [
                float(
                    not result.abstained
                    and result.error is None
                    and bool(set(result.cited_documents) & set(case.cited_documents))
                )
                for case, result in answerable
            ],
            "relationRecall": [
                0.0
                if result.abstained or result.error is not None
                else len(set(case.relations) & set(result.relation_types))
                / len(set(case.relations))
                for case, result in answerable
                if case.relations
            ],
            "keywordRecall": [
                0.0
                if result.abstained
                else sum(word.casefold() in result.answer.casefold() for word in case.must_contain)
                / len(case.must_contain)
                for case, result in keyed
            ],
            "abstainAccuracy": [float(result.abstained == case.abstain) for case, result in group],
            "answered": [
                float(not result.abstained and result.error is None) for _, result in answerable
            ],
        }
        metrics[name] = {
            metric: round(sum(scores) / len(scores), 4)
            for metric, scores in values.items()
            if scores
        }
    return metrics


def compare(report: GoldenReport, baseline: Mapping[str, Mapping[str, float]]) -> list[str]:
    """Describe every baseline metric the report lowers or omits; empty means no regression."""

    regressions: list[str] = []
    for category, expected in baseline.items():
        actual = report.metrics.get(category, {})
        for metric, value in expected.items():
            if metric not in actual:
                regressions.append(f"{category}.{metric}: missing (baseline {value})")
            elif actual[metric] < value - 1e-9:
                regressions.append(f"{category}.{metric}: {actual[metric]} < baseline {value}")
    return regressions


def format_table(report: GoldenReport) -> str:
    header = ("category", *METRICS)
    rows = [header] + [
        (
            category,
            *(f"{values[metric]:.4f}" if metric in values else "-" for metric in METRICS),
        )
        for category, values in report.metrics.items()
    ]
    widths = [max(len(row[index]) for row in rows) for index in range(len(header))]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        for row in rows
    )
