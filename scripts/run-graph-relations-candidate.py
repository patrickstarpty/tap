#!/usr/bin/env python3
"""Produce `graph-relation-observations-v1` observations for the Graph
relation-answer golden set, in two modes:

- `fake` (default CI path, no external services): rule-based extraction
  (`DeterministicGraphExtraction`) over `*.md` documents under `--corpus-dir`,
  merged with PR 2's pure `ProjectGraphMerger`, queried through an in-memory
  `ProjectGraphStorePort` by the registered `"relation-analysis"` agent
  subgraph (`RelationAnalysisAgent`), and answered by the deterministic
  `DeterministicTapperModel` so every answer carries real R-labeled edge
  citations.
- `real` (opt-in, requires `TAPPER_GRAPH_EXTRACTION_MODE=model` and a running
  Tapper API stack): dry-runs each question through the real chat answer
  pipeline via `ConversationGroundingCheck.dry_run_answer`.

Mirrors `scripts/run-quality-graph-candidate.py`'s real-mode refusal shape and
`scripts/evaluate-graph-relations.py`'s thin-CLI-over-a-pure-function style.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.application.agents.protocol import AgentContext
from tap.modules.ai.application.agents.relation_analysis import RelationAnalysisAgent
from tap.modules.graph.adapters.fake_extraction import DeterministicGraphExtraction
from tap.modules.graph.application.alias_index import AliasIndex
from tap.modules.graph.application.merger import ProjectGraphMerger
from tap.modules.graph.domain.extraction import GraphExtractionRequest
from tap.modules.graph.domain.models import GraphSnapshot
from tap.modules.graph.domain.project import (
    Alias,
    EdgeEvidence,
    FragmentRecord,
    NodeSource,
    ProjectEdge,
    ProjectGraphVersion,
    ProjectNode,
    ProjectSubgraph,
)
from tap.modules.knowledge.application.relation_analysis import (
    RelationAnalysisInput,
    RelationContext,
    RelationContextStatus,
    RelationEvidence,
)
from tap.modules.knowledge.domain.models import (
    ContentRole,
    DocumentAnchor,
    Evidence,
    IndexRevision,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
)
from tap.modules.knowledge.ports.answers import _RELATION_CITATION_LABEL_PATTERN
from tap.quality.graph_relations import (
    GoldenQuestion,
    validate_golden,
    validate_regression,
)
from tap.testing.deterministic_model import DeterministicTapperModel

OBSERVATIONS_SCHEMA_VERSION = "graph-relation-observations-v1"
_PROFILE_ID = "quick-hybrid-v1"


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chunk_document(stem: str, text: str) -> list[dict[str, Any]]:
    """Split on blank lines into chunks; `chunkId = f"{stem}-{ordinal}"`, anchor
    shaped like `tests/unit/graph/test_fake_graph_publisher.py`'s `_request`."""
    chunks: list[dict[str, Any]] = []
    offset = 0
    ordinal = 0
    for raw_paragraph in re.split(r"\n\s*\n", text):
        paragraph = raw_paragraph.strip()
        if not paragraph:
            continue
        start = text.index(paragraph, offset)
        end = start + len(paragraph)
        offset = end
        chunks.append(
            {
                "sourceRevisionId": stem,
                "documentRevisionId": stem,
                "chunkId": f"{stem}-{ordinal}",
                "content": paragraph,
                "anchor": {
                    "type": "document",
                    "startOffset": start,
                    "endOffset": end,
                    "headingPath": [],
                },
                "contentDigest": _digest(paragraph),
            }
        )
        ordinal += 1
    return chunks


class _InMemoryProjectGraphStore:
    """Minimal `ProjectGraphStorePort` stand-in built from one merged
    `ProjectGraphDraft`: implements only the `get_current` plus five query
    methods (`nodes_for_chunks`, `nodes`, `match_aliases`, `neighbors`,
    `path`) `RelationAnalysisAgent` actually calls -- a fixture-only adapter
    for the fake candidate run, not a general-purpose store (see
    `tap.modules.graph.adapters.mysql_project_store` for that)."""

    def __init__(
        self,
        nodes: tuple[ProjectNode, ...],
        edges: tuple[ProjectEdge, ...],
        node_sources: tuple[NodeSource, ...],
        edge_evidence: tuple[EdgeEvidence, ...],
        *,
        version: int = 1,
    ) -> None:
        self._nodes_by_id = {node.node_id: node for node in nodes}
        self._edges = edges
        self._edge_by_id = {edge.edge_id: edge for edge in edges}
        self._edge_evidence: dict[str, list[EdgeEvidence]] = {}
        for item in edge_evidence:
            self._edge_evidence.setdefault(item.edge_id, []).append(item)
        self._node_chunks: dict[str, list[str]] = {}
        self._node_sources: dict[str, set[str]] = {}
        for source in node_sources:
            self._node_chunks.setdefault(source.chunk_id, []).append(source.node_id)
            self._node_sources.setdefault(source.node_id, set()).add(
                source.source_revision_id
            )
        aliases = tuple(
            Alias(alias_norm=node.label, node_id=node.node_id, origin="LABEL")
            for node in nodes
        ) + tuple(
            Alias(alias_norm=alias, node_id=node.node_id, origin="LABEL")
            for node in nodes
            for alias in node.aliases
        )
        self._alias_index = AliasIndex.build(aliases)
        self._version = version

    async def get_current(self, scope: object) -> ProjectGraphVersion:
        return ProjectGraphVersion(
            project_id=getattr(scope, "project_id", "project"),
            version=self._version,
            status="READY",
            fragment_digest=_digest("fake-graph"),
            node_count=len(self._nodes_by_id),
            edge_count=len(self._edges),
            merged_at=datetime.now(timezone.utc),
        )

    # `RelationAnalysisAgent` only ever calls `get_current` plus the five
    # query methods below (`nodes_for_chunks`, `nodes`, `match_aliases`,
    # `neighbors`, `path`); these five remaining `ProjectGraphStorePort`
    # members exist only so this fixture-only store satisfies the Protocol
    # for static typing, and are never exercised by the candidate run.
    async def communities(self, scope: object) -> tuple[Any, ...]:
        raise NotImplementedError("fake candidate store does not serve communities")

    async def overview(self, scope: object, **kwargs: object) -> ProjectSubgraph:
        raise NotImplementedError("fake candidate store does not serve overview")

    async def search(
        self, scope: object, text: str, **kwargs: object
    ) -> ProjectSubgraph:
        raise NotImplementedError("fake candidate store does not serve search")

    async def node_detail(self, scope: object, node_id: str, **kwargs: object) -> Any:
        raise NotImplementedError("fake candidate store does not serve node_detail")

    async def highlight(
        self, scope: object, edge_ids: tuple[str, ...], **kwargs: object
    ) -> ProjectSubgraph:
        raise NotImplementedError("fake candidate store does not serve highlight")

    def _edges_authorized(self, source_revision_ids: tuple[str, ...]) -> set[str]:
        if not source_revision_ids:
            return set(self._edge_by_id)
        allowed = set(source_revision_ids)
        return {
            edge_id
            for edge_id, items in self._edge_evidence.items()
            if any(item.source_revision_id in allowed for item in items)
        }

    def _node_visible(self, node_id: str, source_revision_ids: tuple[str, ...]) -> bool:
        if node_id not in self._nodes_by_id:
            return False
        if not source_revision_ids:
            return True
        allowed = set(source_revision_ids)
        return any(
            revision in allowed for revision in self._node_sources.get(node_id, ())
        )

    async def nodes_for_chunks(
        self, scope: object, chunk_ids: tuple[str, ...], *, version: int | None = None
    ) -> tuple[str, ...]:
        result: list[str] = []
        for chunk_id in chunk_ids:
            result.extend(self._node_chunks.get(chunk_id, ()))
        return tuple(result)

    async def nodes(
        self, scope: object, node_ids: tuple[str, ...], *, version: int | None = None
    ) -> tuple[ProjectNode, ...]:
        return tuple(
            self._nodes_by_id[node_id]
            for node_id in node_ids
            if node_id in self._nodes_by_id
        )

    async def match_aliases(
        self, scope: object, text: str, *, version: int | None = None
    ):
        return self._alias_index.match(text)

    async def neighbors(
        self,
        scope: object,
        node_id: str,
        *,
        depth: int = 1,
        node_limit: int = 50,
        source_revision_ids: tuple[str, ...] = (),
        version: int | None = None,
    ) -> ProjectSubgraph:
        if not self._node_visible(node_id, source_revision_ids):
            return ProjectSubgraph(
                version=version or self._version,
                nodes=(),
                edges=(),
                sources=(),
                evidence=(),
            )
        authorized = self._edges_authorized(source_revision_ids)
        visited = {node_id}
        frontier = {node_id}
        selected: set[str] = set()
        for _ in range(depth):
            next_frontier: set[str] = set()
            for edge in self._edges:
                if edge.edge_id not in authorized:
                    continue
                if edge.source_node_id in frontier or edge.target_node_id in frontier:
                    selected.add(edge.edge_id)
                    for candidate in (edge.source_node_id, edge.target_node_id):
                        if candidate not in visited:
                            next_frontier.add(candidate)
            visited |= next_frontier
            frontier = next_frontier
            if not frontier:
                break
        ordering = [node_id] + sorted(visited - {node_id})
        nodes = tuple(
            self._nodes_by_id[candidate]
            for candidate in ordering
            if candidate in self._nodes_by_id
        )[:node_limit]
        selected_node_ids = {node.node_id for node in nodes}
        edges = tuple(
            self._edge_by_id[edge_id]
            for edge_id in selected
            if self._edge_by_id[edge_id].source_node_id in selected_node_ids
            and self._edge_by_id[edge_id].target_node_id in selected_node_ids
        )
        evidence = tuple(
            item for edge in edges for item in self._edge_evidence.get(edge.edge_id, ())
        )
        return ProjectSubgraph(
            version=version or self._version,
            nodes=nodes,
            edges=edges,
            sources=(),
            evidence=evidence,
        )

    async def path(
        self,
        scope: object,
        source_node_id: str,
        target_node_id: str,
        *,
        max_hops: int = 3,
        source_revision_ids: tuple[str, ...] = (),
        version: int | None = None,
    ) -> ProjectSubgraph:
        authorized = self._edges_authorized(source_revision_ids)
        if source_node_id == target_node_id:
            node = self._nodes_by_id.get(source_node_id)
            return ProjectSubgraph(
                version=version or self._version,
                nodes=(node,) if node is not None else (),
                edges=(),
                sources=(),
                evidence=(),
            )
        adjacency: dict[str, list[tuple[str, str]]] = {}
        for edge in self._edges:
            if edge.edge_id not in authorized:
                continue
            adjacency.setdefault(edge.source_node_id, []).append(
                (edge.target_node_id, edge.edge_id)
            )
            adjacency.setdefault(edge.target_node_id, []).append(
                (edge.source_node_id, edge.edge_id)
            )
        visited = {source_node_id}
        queue: deque[tuple[str, tuple[str, ...], tuple[str, ...]]] = deque(
            [(source_node_id, (source_node_id,), ())]
        )
        while queue:
            current, node_path, edge_path = queue.popleft()
            if len(edge_path) >= max_hops:
                continue
            for neighbor, edge_id in adjacency.get(current, ()):
                if neighbor in visited:
                    continue
                new_node_path = node_path + (neighbor,)
                new_edge_path = edge_path + (edge_id,)
                if neighbor == target_node_id:
                    nodes = tuple(
                        self._nodes_by_id[nid]
                        for nid in new_node_path
                        if nid in self._nodes_by_id
                    )
                    edges = tuple(self._edge_by_id[eid] for eid in new_edge_path)
                    evidence = tuple(
                        item
                        for eid in new_edge_path
                        for item in self._edge_evidence.get(eid, ())
                    )
                    return ProjectSubgraph(
                        version=version or self._version,
                        nodes=nodes,
                        edges=edges,
                        sources=(),
                        evidence=evidence,
                    )
                visited.add(neighbor)
                queue.append((neighbor, new_node_path, new_edge_path))
        return ProjectSubgraph(
            version=version or self._version,
            nodes=(),
            edges=(),
            sources=(),
            evidence=(),
        )


class _FakeSnippetReader:
    """`ChunkSnippetReader` backed by the chunk text built while chunking the
    fixture documents -- every chunk's content is already in memory, so there
    is nothing to resolve against a real document store."""

    def __init__(
        self, chunk_content: Mapping[str, str], document_ids: Mapping[str, str]
    ) -> None:
        self._chunk_content = chunk_content
        self._document_ids = document_ids

    async def snippets(self, refs: tuple[tuple[str, str], ...]) -> Mapping[str, str]:
        return {
            chunk_id: self._chunk_content[chunk_id]
            for _document_revision_id, chunk_id in refs
            if chunk_id in self._chunk_content
        }

    async def document_ids(self, revision_ids: tuple[str, ...]) -> Mapping[str, str]:
        return {
            revision_id: self._document_ids[revision_id]
            for revision_id in revision_ids
            if revision_id in self._document_ids
        }


def _edge_citation_dict(
    relation: RelationEvidence, graph_version: str
) -> dict[str, Any]:
    return {
        "citationId": "c-" + relation.edge_id,
        "kind": "edge",
        "evidenceLabel": relation.label,
        "edgeId": relation.edge_id,
        "graphVersion": graph_version,
        "subject": {
            "nodeId": relation.subject_node_id,
            "label": relation.subject_label,
        },
        "object": {"nodeId": relation.object_node_id, "label": relation.object_label},
        "relationType": relation.relation_type,
        "relationLabel": relation.relation_label,
    }


def _fake_evidence(relation: RelationEvidence, content: str) -> Evidence:
    return Evidence(
        family=SourceFamily.DOC,
        chunk_id="chunk-" + relation.edge_id,
        logical_chunk_id="logical-" + relation.edge_id,
        title=None,
        content=content,
        source=SourceRevisionRef(
            source_id="source-fake",
            source_type="doc",
            revision_kind=RevisionKind.BLOB_VERSION,
            revision="rev-fake",
            source_content_hash=_digest("fake-source"),
            anchor=DocumentAnchor(start_offset=0, end_offset=len(content)),
        ),
        chunk_content_hash=_digest(content),
        content_role=ContentRole.SOURCE,
        citation_id="c-" + relation.edge_id,
        evidence_label=relation.label,
        index_revision=IndexRevision(
            physical_index="fake", schema_version="v1", corpus_version="fake"
        ),
        embedding_model_version="fake",
        acl_decision_id="fake",
        score=1.0,
    )


async def _fake_answer(context: RelationContext, question: str) -> dict[str, Any]:
    """Feed each ranked edge's first evidence snippet to the deterministic
    model and build `kind: "edge"` citations only for the edges whose R label
    the model actually referenced in a generated claim (validated against
    `ports/answers.py`'s R-label pattern)."""
    if context.status is not RelationContextStatus.APPLIED or not context.relations:
        return {"answer": None, "abstained": True, "citations": []}

    by_label = {relation.label: relation for relation in context.relations}
    evidence: list[Evidence] = []
    for relation in context.relations:
        content = relation.support[0].snippet or ""
        if not content:
            continue
        evidence.append(_fake_evidence(relation, content))
    if not evidence:
        return {"answer": None, "abstained": True, "citations": []}

    generation = await DeterministicTapperModel().answer(
        question, tuple(evidence), _PROFILE_ID
    )

    citations: list[dict[str, Any]] = []
    cited_labels: set[str] = set()
    for claim in generation.claims:
        for label in claim.evidence_labels:
            if label in cited_labels:
                continue
            if _RELATION_CITATION_LABEL_PATTERN.fullmatch(label) is None:
                raise ValueError(
                    f"deterministic model emitted a malformed R label: {label!r}"
                )
            cited_relation = by_label.get(label)
            if cited_relation is None:
                continue
            cited_labels.add(label)
            citations.append(
                _edge_citation_dict(cited_relation, context.graph_version or "")
            )

    return {"answer": generation.text, "abstained": False, "citations": citations}


async def run_fake(*, golden: Path, corpus_dir: Path) -> dict[str, Any]:
    golden_set = validate_golden(json.loads(golden.read_text(encoding="utf-8")))

    documents = sorted(corpus_dir.glob("*.md"))
    if not documents:
        raise ValueError(
            f"fake candidate run found no *.md documents under {corpus_dir}"
        )

    extractor = DeterministicGraphExtraction()
    fragments: list[FragmentRecord] = []
    chunk_content: dict[str, str] = {}
    document_ids: dict[str, str] = {}
    for document_path in documents:
        stem = document_path.stem
        text = document_path.read_text(encoding="utf-8")
        chunks = _chunk_document(stem, text)
        if not chunks:
            raise ValueError(
                f"{document_path.name} has no nonblank paragraphs to chunk"
            )
        for chunk in chunks:
            chunk_content[chunk["chunkId"]] = chunk["content"]
        document_ids[stem] = stem
        request = GraphExtractionRequest(
            scope=VALIDATION_SCOPE,
            snapshot=GraphSnapshot.create(
                snapshot_id=f"{stem}-snapshot",
                project_id=VALIDATION_SCOPE.project_id,
                source_revision_ids=(stem,),
                document_revision_ids=(stem,),
            ),
            chunks=tuple(chunks),
            model_alias="fake",
            idempotency_key=f"graph-relations-candidate:{stem}",
            document_title=stem,
        )
        draft = await extractor.extract(request)
        if draft is None:
            raise ValueError(
                f"fake extraction grounded no facts for {document_path.name}"
            )
        fragments.append(
            FragmentRecord(
                snapshot_id=f"{stem}-snapshot",
                revision_id=stem,
                status="READY",
                content_digest=_digest(text),
                draft=draft,
            )
        )

    merged = ProjectGraphMerger().merge(VALIDATION_SCOPE, tuple(fragments))
    store = _InMemoryProjectGraphStore(
        merged.nodes, merged.edges, merged.node_sources, merged.edge_evidence
    )
    current = await store.get_current(VALIDATION_SCOPE)
    graph_version = str(current.version)
    snippets = _FakeSnippetReader(chunk_content, document_ids)
    agent = RelationAnalysisAgent(store, snippets, publication_authority=None)

    answers: list[dict[str, Any]] = []
    for question in golden_set.questions:
        # Scoped to this question's own `expectedSources` rather than every
        # loaded document: the merged graph puts all fixture documents'
        # facts within one small shared neighborhood, so an unrestricted run
        # also surfaces edges from unrelated documents that the golden set
        # never expects for this question (e.g. a REQUIRES edge from a
        # different process's document) -- scoring them "wrong" even though
        # the deterministic model faithfully echoed exactly the evidence it
        # was handed. Real mode (`run_real`, below) already scopes each
        # question this way via `expectedSources` -> `sourceId`; this mirrors
        # it, rather than literally authorizing every corpus revision for
        # every question.
        source_revision_ids = frozenset(question.expected_sources)
        context = AgentContext(
            scope=VALIDATION_SCOPE,
            source_revision_ids=source_revision_ids,
            graph_version=graph_version,
            turn_id=None,
        )
        value = RelationAnalysisInput(
            query=question.question,
            source_revision_ids=source_revision_ids,
            evidence=(),
            graph_version=graph_version,
        )
        relation_context = await agent.run(context, value)
        answer = await _fake_answer(relation_context, question.question)
        answers.append({"questionId": question.id, "group": "golden", **answer})

    return {
        "schemaVersion": OBSERVATIONS_SCHEMA_VERSION,
        "executionMode": "fake",
        "extractionMode": "fake",
        "model": {"alias": "fake", "actual": "fake/deterministic-tapper"},
        "graphVersion": graph_version,
        "graphReasoning": True,
        "answers": answers,
    }


def _source_ids_for(
    question: GoldenQuestion, corpus: Mapping[str, Any]
) -> tuple[str, ...]:
    by_id = {
        str(item["id"]): str(item["sourceId"]) for item in corpus.get("sources", ())
    }
    missing = [
        source_id for source_id in question.expected_sources if source_id not in by_id
    ]
    if missing:
        raise ValueError(f"corpus.json is missing sources for manifest ids {missing!r}")
    return tuple(by_id[source_id] for source_id in question.expected_sources)


async def run_real(
    *,
    golden: Path,
    regression: Path | None,
    corpus: Path,
    actor_id: str = "tapper-gate",
) -> dict[str, Any]:
    from tap.entrypoints.tapper_runtime import TapperSettings, create_api_runtime
    from tap.modules.knowledge.adapters.mysql_ready_sources import MysqlReadySources

    settings = TapperSettings.from_mapping(dict(os.environ))
    if settings.graph_extraction_mode != "model":
        raise ValueError("candidate run requires TAPPER_GRAPH_EXTRACTION_MODE=model")

    golden_set = validate_golden(
        json.loads(golden.read_text(encoding="utf-8")), require_human=True
    )
    corpus_value = json.loads(corpus.read_text(encoding="utf-8"))
    regression_questions: list[tuple[str, str]] = []
    if regression is not None:
        regression_set = validate_regression(
            json.loads(regression.read_text(encoding="utf-8"))
        )
        for group in regression_set.groups:
            for question_id in group.questions:
                regression_questions.append((question_id, group.id))

    from tap.entrypoints.prompt_suggestion_knowledge import ConversationGroundingCheck
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE as REAL_SCOPE
    from tap.platform.db.session import create_engine_and_session_factory

    runtime = await create_api_runtime(settings)
    engine, sessions = create_engine_and_session_factory(settings.database_url)
    try:
        knowledge = runtime.http_services.knowledge
        ready_sources = MysqlReadySources(sessions, REAL_SCOPE)
        check = ConversationGroundingCheck(
            knowledge,
            ready_sources=ready_sources,
            scope=REAL_SCOPE,
            model_alias=settings.default_chat_model,
        )

        project_graph = runtime.http_services.project_graph
        current = (
            await project_graph.get_current(REAL_SCOPE)
            if project_graph is not None
            else None
        )
        graph_version = str(current.version) if current is not None else None

        answers: list[dict[str, Any]] = []
        all_source_ids = tuple(
            str(item["sourceId"]) for item in corpus_value.get("sources", ())
        )
        for question in golden_set.questions:
            source_ids = _source_ids_for(question, corpus_value)
            answer = await check.dry_run_answer(actor_id, question.question, source_ids)
            answers.append(
                _real_answer_dict(question.id, "golden", answer, graph_version)
            )
        for question_id, group_id in regression_questions:
            answer = await check.dry_run_answer(actor_id, question_id, all_source_ids)
            answers.append(
                _real_answer_dict(question_id, group_id, answer, graph_version)
            )

        actual_model = next(
            (item.pop("modelActual") for item in answers if item.get("modelActual")),
            None,
        )
        for item in answers:
            item.pop("modelActual", None)
        actual_model = actual_model or f"litellm/{settings.default_chat_model}"

        return {
            "schemaVersion": OBSERVATIONS_SCHEMA_VERSION,
            "executionMode": "real",
            "extractionMode": settings.graph_extraction_mode,
            "model": {"alias": settings.default_chat_model, "actual": actual_model},
            "graphVersion": graph_version,
            "graphReasoning": settings.graph_reasoning,
            "answers": answers,
        }
    finally:
        await engine.dispose()
        await runtime.aclose()


def _real_answer_dict(
    question_id: str, group: str, answer: object | None, graph_version: str | None
) -> dict[str, Any]:
    from tap.modules.knowledge.api import answer_response_to_http

    if answer is None:
        return {
            "questionId": question_id,
            "group": group,
            "answer": None,
            "abstained": True,
            "citations": [],
        }
    http_answer = answer_response_to_http(answer)  # type: ignore[arg-type]
    model_actual = None
    provenance = getattr(answer, "model_call", None) or getattr(
        answer, "answer_provenance", None
    )
    if provenance is not None:
        model_actual = getattr(provenance, "actual_model", None) or getattr(
            provenance, "provider_model_id", None
        )
    return {
        "questionId": question_id,
        "group": group,
        "answer": http_answer.answer,
        "abstained": http_answer.abstained,
        "citations": [item.model_dump(by_alias=True) for item in http_answer.citations],
        "modelActual": model_actual,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fake", "real"), required=True)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--regression", type=Path, default=None)
    parser.add_argument("--corpus-dir", type=Path, default=None)
    parser.add_argument(
        "--corpus", type=Path, default=Path(".local/graph-real/corpus.json")
    )
    parser.add_argument("--actor-id", default="tapper-gate")
    arguments = parser.parse_args()

    if arguments.mode == "fake":
        if arguments.corpus_dir is None:
            parser.error("--mode fake requires --corpus-dir")
        observations = asyncio.run(
            run_fake(golden=arguments.golden, corpus_dir=arguments.corpus_dir)
        )
    else:
        observations = asyncio.run(
            run_real(
                golden=arguments.golden,
                regression=arguments.regression,
                corpus=arguments.corpus,
                actor_id=arguments.actor_id,
            )
        )

    arguments.observations.parent.mkdir(parents=True, exist_ok=True)
    arguments.observations.write_text(
        json.dumps(observations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
