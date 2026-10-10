#!/usr/bin/env python3
"""Produce `graph-relation-observations-v1` observations for the Graph
relation-answer golden set, in two modes:

- `fake` (default CI path, no external services): rule-based extraction
  (`DeterministicGraphExtraction`) over `*.md` documents under `--corpus-dir`,
  merged with PR 2's pure `ProjectGraphMerger`, published into the reference
  `InMemoryProjectGraphStore`, queried by the registered `"relation-analysis"`
  agent subgraph (`RelationAnalysisAgent`), and answered by the deterministic
  `DeterministicTapperModel` so every answer carries real R-labeled edge
  citations. Fake mode exists to check wiring, citation shape and evaluator
  plumbing end to end in CI -- it says nothing about real extraction or
  real-model relation precision; that is what Task 8's `--mode real` run
  against a real corpus and model is for.
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
import sys
from collections.abc import Mapping
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.application.agents.protocol import AgentContext
from tap.modules.ai.application.agents.relation_analysis import RelationAnalysisAgent
from tap.modules.graph.adapters.fake_extraction import DeterministicGraphExtraction
from tap.modules.graph.application.merger import ProjectGraphMerger
from tap.modules.graph.application.project_queries import InMemoryProjectGraphStore
from tap.modules.graph.domain.extraction import GraphExtractionRequest
from tap.modules.graph.domain.models import GraphSnapshot
from tap.modules.graph.domain.project import FragmentRecord
from tap.modules.knowledge.application.relation_analysis import (
    RelationAnalysisInput,
    RelationContext,
    RelationContextStatus,
    RelationEvidence,
)
from tap.modules.knowledge.domain.models import (
    AnswerResponse,
    ContentRole,
    DocumentAnchor,
    Evidence,
    IndexRevision,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
)
from tap.modules.knowledge.ports.answers import RELATION_CITATION_LABEL_PATTERN
from tap.quality.evidence import canonical_digest
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


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


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
    `ports/answers.py`'s public `RELATION_CITATION_LABEL_PATTERN`)."""
    graph_context_status = context.status.value
    empty: dict[str, Any] = {
        "abstained": True,
        "graphContextStatus": graph_context_status,
        "claims": [],
        "citations": [],
    }
    if context.status is not RelationContextStatus.APPLIED or not context.relations:
        return empty

    by_label = {relation.label: relation for relation in context.relations}
    evidence: list[Evidence] = []
    for relation in context.relations:
        content = relation.support[0].snippet or ""
        if not content:
            continue
        evidence.append(_fake_evidence(relation, content))
    if not evidence:
        return empty

    generation = await DeterministicTapperModel().answer(
        question, tuple(evidence), _PROFILE_ID
    )

    citations: list[dict[str, Any]] = []
    citation_id_by_label: dict[str, str] = {}
    claims: list[dict[str, Any]] = []
    for claim in generation.claims:
        claim_citation_ids: list[str] = []
        for label in claim.evidence_labels:
            if RELATION_CITATION_LABEL_PATTERN.fullmatch(label) is None:
                raise ValueError(
                    f"deterministic model emitted a malformed R label: {label!r}"
                )
            cited_relation = by_label.get(label)
            if cited_relation is None:
                continue
            if label not in citation_id_by_label:
                citation = _edge_citation_dict(
                    cited_relation, context.graph_version or ""
                )
                citation_id_by_label[label] = citation["citationId"]
                citations.append(citation)
            claim_citation_ids.append(citation_id_by_label[label])
        claims.append({"text": claim.text, "citationIds": claim_citation_ids})

    return {
        "abstained": False,
        "graphContextStatus": graph_context_status,
        "claims": claims,
        "citations": citations,
    }


async def run_fake(*, golden: Path, corpus_dir: Path) -> dict[str, Any]:
    """Run the deterministic candidate pipeline end to end against `--corpus-dir`.

    This checks wiring, citation shape and evaluator plumbing in CI -- not
    relation precision (rule-based extraction plus a deterministic model
    stand in for the real extractor and model, so a passing fake run says
    nothing about real-model relation accuracy; that is Task 8's job)."""
    started_at = _now_iso()
    golden_json = json.loads(golden.read_text(encoding="utf-8"))
    golden_set = validate_golden(golden_json)

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
    store = InMemoryProjectGraphStore()
    published = await store.publish(
        VALIDATION_SCOPE, merged, now=datetime.now(timezone.utc)
    )
    graph_version = str(published.version)
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
        "goldenDigest": canonical_digest(golden_json),
        # No real corpus manifest backs the fixture documents in fake mode
        # (unlike `run_real`, which reads `corpus.json`'s own `manifestDigest`).
        "corpusDigest": None,
        "graphVersion": graph_version,
        "graphReasoning": True,
        "startedAt": started_at,
        "finishedAt": _now_iso(),
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


def _flatten_http_citation(citation: Mapping[str, Any]) -> dict[str, Any]:
    """`HttpCitation.model_dump` nests edge facts under an `"edge"` key
    (`{"kind": "edge", "edge": {"edgeId": ..., "subject": ..., ...}}`), but the
    plan's observation shape -- and `tap.quality.graph_relations`'s matcher,
    which reads `citation["subject"]`/`citation["relationType"]` directly off
    the citation -- expect them flat, the same shape `_edge_citation_dict`
    above already produces for fake mode. Flatten here so both modes agree."""
    flattened = dict(citation)
    edge = flattened.pop("edge", None)
    if isinstance(edge, Mapping):
        flattened.update(edge)
    return flattened


def _real_answer_dict(
    question_id: str, group: str, answer: object | None, graph_version: str | None
) -> dict[str, Any]:
    from tap.modules.knowledge.api import answer_response_to_http

    if answer is None:
        return {
            "questionId": question_id,
            "group": group,
            "abstained": True,
            "graphContextStatus": "UNAVAILABLE",
            "claims": [],
            "citations": [],
        }
    http_answer = answer_response_to_http(cast(AnswerResponse, answer))
    return {
        "questionId": question_id,
        "group": group,
        "abstained": http_answer.abstained,
        "graphContextStatus": http_answer.graph_context_status,
        "claims": [
            {"text": claim.text, "citationIds": list(claim.citation_ids)}
            for claim in http_answer.claims
        ],
        "citations": [
            _flatten_http_citation(item.model_dump(by_alias=True))
            for item in http_answer.citations
        ],
    }


def _assemble_real_observations(
    *,
    golden_json: Mapping[str, Any],
    corpus_value: Mapping[str, Any],
    answers: list[dict[str, Any]],
    graph_version: str | None,
    actual_model: str | None,
    settings_default_chat_model: str,
    graph_extraction_mode: str,
    graph_reasoning: bool,
    started_at: str,
    finished_at: str,
) -> dict[str, Any]:
    """Pure (no I/O, no live service) assembly of the real-mode observation
    document, factored out of `run_real` so its digest/field wiring (C2) is
    unit-testable without `create_api_runtime`."""
    return {
        "schemaVersion": OBSERVATIONS_SCHEMA_VERSION,
        "executionMode": "real",
        "extractionMode": graph_extraction_mode,
        "model": {"alias": settings_default_chat_model, "actual": actual_model},
        "goldenDigest": canonical_digest(dict(golden_json)),
        "corpusDigest": corpus_value.get("manifestDigest"),
        "graphVersion": graph_version,
        "graphReasoning": graph_reasoning,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "answers": answers,
    }


async def run_real(
    *,
    golden: Path,
    regression: Path | None,
    corpus: Path,
    actor_id: str = VALIDATION_SCOPE.actor_id,
) -> dict[str, Any]:
    from tap.entrypoints.prompt_suggestion_knowledge import ConversationGroundingCheck
    from tap.entrypoints.tapper_runtime import TapperSettings, create_api_runtime
    from tap.modules.knowledge.adapters.mysql_ready_sources import MysqlReadySources
    from tap.platform.db.session import create_engine_and_session_factory

    started_at = _now_iso()
    settings = TapperSettings.from_mapping(dict(os.environ))
    if settings.graph_extraction_mode != "model":
        raise ValueError("candidate run requires TAPPER_GRAPH_EXTRACTION_MODE=model")

    golden_json = json.loads(golden.read_text(encoding="utf-8"))
    golden_set = validate_golden(golden_json, require_human=True)
    corpus_value = json.loads(corpus.read_text(encoding="utf-8"))
    regression_questions: list[tuple[str, str]] = []
    if regression is not None:
        regression_set = validate_regression(
            json.loads(regression.read_text(encoding="utf-8"))
        )
        for group in regression_set.groups:
            # The regression document's `questions` are the full question
            # text to ask (there is no separate opaque id alongside it), so
            # the loop variable below is itself the text passed to
            # `dry_run_answer` *and* the `questionId` recorded in the answer.
            for question_text in group.questions:
                regression_questions.append((question_text, group.id))

    async with AsyncExitStack() as stack:
        runtime = await create_api_runtime(settings)
        stack.push_async_callback(runtime.aclose)
        engine, sessions = create_engine_and_session_factory(settings.database_url)
        stack.push_async_callback(engine.dispose)

        knowledge = runtime.http_services.knowledge
        ready_sources = MysqlReadySources(sessions, VALIDATION_SCOPE)
        check = ConversationGroundingCheck(
            knowledge,
            ready_sources=ready_sources,
            scope=VALIDATION_SCOPE,
            model_alias=settings.default_chat_model,
        )

        project_graph = runtime.http_services.project_graph
        current = (
            await project_graph.get_current(VALIDATION_SCOPE)
            if project_graph is not None
            else None
        )
        graph_version = str(current.version) if current is not None else None

        answers: list[dict[str, Any]] = []
        all_source_ids = tuple(
            str(item["sourceId"]) for item in corpus_value.get("sources", ())
        )
        first_real_answer: object | None = None
        for question in golden_set.questions:
            source_ids = _source_ids_for(question, corpus_value)
            answer = await check.dry_run_answer(actor_id, question.question, source_ids)
            if first_real_answer is None and answer is not None:
                first_real_answer = answer
            answers.append(
                _real_answer_dict(question.id, "golden", answer, graph_version)
            )
        for question_text, group_id in regression_questions:
            answer = await check.dry_run_answer(actor_id, question_text, all_source_ids)
            if first_real_answer is None and answer is not None:
                first_real_answer = answer
            answers.append(
                _real_answer_dict(question_text, group_id, answer, graph_version)
            )

        provenance = getattr(first_real_answer, "answer_provenance", None)
        actual_model = (
            getattr(provenance, "provider_model_id", None)
            if provenance is not None
            else None
        )

        return _assemble_real_observations(
            golden_json=golden_json,
            corpus_value=corpus_value,
            answers=answers,
            graph_version=graph_version,
            actual_model=actual_model,
            settings_default_chat_model=settings.default_chat_model,
            graph_extraction_mode=settings.graph_extraction_mode,
            graph_reasoning=settings.graph_reasoning,
            started_at=started_at,
            finished_at=_now_iso(),
        )


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
    parser.add_argument("--actor-id", default=VALIDATION_SCOPE.actor_id)
    arguments = parser.parse_args()

    try:
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
    except (ValueError, OSError) as error:
        # M3: a real-mode refusal (TAPPER_GRAPH_EXTRACTION_MODE != "model", a missing
        # corpus.json, an unreadable golden/regression file, ...) must print a clean,
        # actionable message and exit 2 -- not an uncaught traceback.
        print(f"error: {error}", file=sys.stderr)
        return 2

    arguments.observations.parent.mkdir(parents=True, exist_ok=True)
    arguments.observations.write_text(
        json.dumps(observations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
