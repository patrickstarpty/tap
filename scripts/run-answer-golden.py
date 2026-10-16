#!/usr/bin/env python3
"""Produce `answer-golden-observations-v1` observations for the capability-1
answer golden set, in two modes:

- `fake` (default CI path, no external services): chunk each `*.md` document
  under `--corpus-dir` into paragraphs, scope the candidate paragraphs to each
  question's own `expectedSources`, rank them by character-bigram overlap
  with the question text, take the top 6, wrap them as `Evidence` and call
  `KnowledgeModelGateway.answer` backed by `tap.testing.deterministic_model_
  gateway.DeterministicModelGateway`. Fake mode exists to check wiring,
  citation shape and evaluator plumbing end to end in CI -- it says nothing
  about real-model answer quality; this commit is the run point for a later
  real-model baseline of the current JSON answer format (see
  `evaluate-answer-golden.py`'s `--real` gate).
- `real` (opt-in, requires `TAP_RUN_QUALITY_ANSWERS=1` and a running Tapper
  API stack): dry-runs each question through the real chat answer pipeline
  via `ConversationGroundingCheck.dry_run_answer`, mirroring
  `scripts/run-graph-relations-candidate.py`'s real-mode refusal shape.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

OBSERVATIONS_SCHEMA_VERSION = "answer-golden-observations-v1"
_PROFILE_ID = "quick-hybrid-v1"
_KINDS = ("answerable", "abstain", "false_premise")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_MAX_EVIDENCE = 6


# --- Golden set schema (duplicated in evaluate-answer-golden.py; each script
# stays a self-contained thin CLI over its own pure functions) -------------


@dataclass(frozen=True)
class GoldenCase:
    id: str
    question: str
    locale: str
    kind: str
    expected_sources: tuple[str, ...]
    expected_facts: tuple[str, ...]
    forbidden_facts: tuple[str, ...]


@dataclass(frozen=True)
class GoldenSet:
    schema_version: str
    labeled_by: str
    labeled_at: str
    cases: tuple[GoldenCase, ...]


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _array(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _nonblank_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value


def _string_array(value: object, name: str) -> tuple[str, ...]:
    return tuple(_nonblank_string(item, f"{name} item") for item in _array(value, name))


def _case(value: object) -> GoldenCase:
    case = _mapping(value, "golden case")
    case_id = _nonblank_string(case.get("id"), "case id")
    question = _nonblank_string(case.get("question"), f"case {case_id} question")
    locale = case.get("locale")
    if locale not in ("zh", "en"):
        raise ValueError(f"case {case_id} locale must be 'zh' or 'en'")
    kind = case.get("kind")
    if kind not in _KINDS:
        raise ValueError(f"case {case_id} kind must be one of {_KINDS}")
    expected_sources = _string_array(
        case.get("expectedSources"), f"case {case_id} expectedSources"
    )
    if not expected_sources:
        raise ValueError(f"case {case_id} requires at least one expected source")
    expected_facts = (
        _string_array(case.get("expectedFacts"), f"case {case_id} expectedFacts")
        if "expectedFacts" in case
        else ()
    )
    forbidden_facts = (
        _string_array(case.get("forbiddenFacts"), f"case {case_id} forbiddenFacts")
        if "forbiddenFacts" in case
        else ()
    )
    return GoldenCase(
        id=case_id,
        question=question,
        locale=locale,
        kind=kind,
        expected_sources=expected_sources,
        expected_facts=expected_facts,
        forbidden_facts=forbidden_facts,
    )


def validate_golden(
    value: object, *, known_sources: frozenset[str] | None = None
) -> GoldenSet:
    """Validate an `answer-golden-v1` document; pure, no I/O."""
    document = _mapping(value, "golden set")
    if document.get("schemaVersion") != "answer-golden-v1":
        raise ValueError("unsupported answer golden set schema")
    labeled_by = _nonblank_string(document.get("labeledBy"), "labeledBy")
    labeled_at = document.get("labeledAt")
    if not isinstance(labeled_at, str) or _DATE.fullmatch(labeled_at) is None:
        raise ValueError("labeledAt must match YYYY-MM-DD")
    cases_raw = _array(document.get("cases"), "cases")
    if not cases_raw:
        raise ValueError("golden set requires at least one case")
    seen_ids: set[str] = set()
    cases: list[GoldenCase] = []
    for raw in cases_raw:
        case = _case(raw)
        if case.id in seen_ids:
            raise ValueError(f"case id {case.id} is not unique")
        seen_ids.add(case.id)
        if known_sources is not None:
            unknown = [
                source
                for source in case.expected_sources
                if source not in known_sources
            ]
            if unknown:
                raise ValueError(
                    f"case {case.id} expectedSources must be corpus document names: {unknown}"
                )
        cases.append(case)
    return GoldenSet(
        schema_version="answer-golden-v1",
        labeled_by=labeled_by,
        labeled_at=labeled_at,
        cases=tuple(cases),
    )


# --- Fake-mode chunking and candidate selection (pure) ---------------------


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def chunk_document(text: str) -> tuple[str, ...]:
    """Split on blank lines into nonblank paragraphs; pure, no I/O."""
    return tuple(
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", text)
        if paragraph.strip()
    )


def char_bigrams(text: str) -> frozenset[str]:
    """Character bigrams of `text`, ignoring whitespace; pure, no I/O."""
    compact = "".join(text.split())
    return frozenset(compact[index : index + 2] for index in range(len(compact) - 1))


def rank_paragraphs(
    question: str, paragraphs: Sequence[tuple[str, str]], *, limit: int = _MAX_EVIDENCE
) -> tuple[tuple[str, str], ...]:
    """Rank `(source, paragraph)` pairs by character-bigram overlap with `question`,
    descending, stable on ties; pure, no I/O. Returns the top `limit`."""
    question_bigrams = char_bigrams(question)
    scored = [
        (len(question_bigrams & char_bigrams(paragraph)), index, source, paragraph)
        for index, (source, paragraph) in enumerate(paragraphs)
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return tuple(
        (source, paragraph) for _score, _index, source, paragraph in scored[:limit]
    )


def cited_sources_from_claims(
    claims: Sequence[Mapping[str, Any]], label_sources: Mapping[str, str]
) -> tuple[str, ...]:
    """Unique source document names cited by `claims`' evidence labels, in first-seen
    order; pure, no I/O."""
    seen: list[str] = []
    for claim in claims:
        labels = claim.get("evidenceLabels", ())
        if not isinstance(labels, Sequence) or isinstance(labels, (str, bytes)):
            continue
        for label in labels:
            source = label_sources.get(str(label))
            if source is not None and source not in seen:
                seen.append(source)
    return tuple(seen)


async def _answer_scoped(
    gateway: Any, case: GoldenCase, documents: Mapping[str, str]
) -> dict[str, Any]:
    from tap.modules.knowledge.domain.models import (
        ContentRole,
        DocumentAnchor,
        Evidence,
        IndexRevision,
        RevisionKind,
        SourceFamily,
        SourceRevisionRef,
    )

    paragraphs: list[tuple[str, str]] = []
    for source in case.expected_sources:
        text = documents[source]
        for paragraph in chunk_document(text):
            paragraphs.append((source, paragraph))
    ranked = rank_paragraphs(case.question, paragraphs)

    evidence: list[Evidence] = []
    label_sources: dict[str, str] = {}
    for ordinal, (source, paragraph) in enumerate(ranked, start=1):
        label = f"S{ordinal}"
        label_sources[label] = source
        evidence.append(
            Evidence(
                family=SourceFamily.DOC,
                chunk_id=f"{source}-{ordinal}",
                logical_chunk_id=f"{source}-{ordinal}",
                title=source,
                content=paragraph,
                source=SourceRevisionRef(
                    source_id=f"source-{source}",
                    source_type="doc",
                    revision_kind=RevisionKind.BLOB_VERSION,
                    revision=f"rev-{source}",
                    source_content_hash=_digest(f"fake-source-{source}"),
                    anchor=DocumentAnchor(start_offset=0, end_offset=len(paragraph)),
                ),
                chunk_content_hash=_digest(paragraph),
                content_role=ContentRole.SOURCE,
                citation_id=f"c-{source}-{ordinal}",
                evidence_label=label,
                index_revision=IndexRevision(
                    physical_index="fake", schema_version="v1", corpus_version="fake"
                ),
                embedding_model_version="fake",
                acl_decision_id="fake",
                score=1.0,
            )
        )

    if not evidence:
        return {
            "id": case.id,
            "abstained": True,
            "answer": "",
            "citedSources": [],
            "claimCount": 0,
        }

    generation = await gateway.answer(case.question, tuple(evidence), _PROFILE_ID)
    claims = [
        {"text": claim.text, "evidenceLabels": list(claim.evidence_labels)}
        for claim in generation.claims
    ]
    cited_sources = cited_sources_from_claims(claims, label_sources)
    return {
        "id": case.id,
        "abstained": len(claims) == 0,
        "answer": generation.text,
        "citedSources": list(cited_sources),
        "claimCount": len(claims),
    }


async def run_fake(*, golden: Path, corpus_dir: Path) -> dict[str, Any]:
    """Run the deterministic candidate pipeline end to end against `--corpus-dir`.

    Checks wiring, citation shape and evaluator plumbing in CI -- not real-model
    answer quality (the deterministic model just echoes the first sentence of
    each retrieved paragraph, so a passing fake run says nothing about real
    grounding, abstention or fact recall; that is the real run's job)."""
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.ai.adapters.litellm import LiteLLMModelGatewayConfig
    from tap.modules.ai.adapters.litellm_catalog import ModelRoles
    from tap.modules.knowledge.adapters.litellm import KnowledgeModelGateway
    from tap.testing.deterministic_model_gateway import DeterministicModelGateway

    started_at = _now_iso()
    golden_json = json.loads(golden.read_text(encoding="utf-8"))

    documents_paths = sorted(corpus_dir.glob("*.md"))
    if not documents_paths:
        raise ValueError(f"fake run found no *.md documents under {corpus_dir}")
    documents = {
        path.stem: path.read_text(encoding="utf-8") for path in documents_paths
    }

    golden_set = validate_golden(golden_json, known_sources=frozenset(documents))

    async def _redact(text: str) -> str:
        return text

    config = LiteLLMModelGatewayConfig(
        base_url="https://litellm.example",
        api_key="private-fake-key",
        roles=ModelRoles("qwen-plus", "text-embedding-v4", None),
        embedding_dimension=2,
    )
    deterministic_gateway = DeterministicModelGateway(
        config, scope=VALIDATION_SCOPE, redact=_redact
    )
    gateway = KnowledgeModelGateway(
        deterministic_gateway,
        scope=VALIDATION_SCOPE,
        redact=_redact,
        embedding_alias="text-embedding-v4",
        chat_alias="qwen-plus",
        embedding_dimension=2,
        timeout_seconds=5,
    )

    observations = [
        await _answer_scoped(gateway, case, documents) for case in golden_set.cases
    ]

    return {
        "schemaVersion": OBSERVATIONS_SCHEMA_VERSION,
        "mode": "fake",
        "build": "fake",
        "observations": observations,
        "startedAt": started_at,
        "finishedAt": _now_iso(),
    }


def _git_short_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


async def run_real(*, golden: Path, actor_id: str) -> dict[str, Any]:
    from tap.entrypoints.prompt_suggestion_knowledge import ConversationGroundingCheck
    from tap.entrypoints.tapper_runtime import TapperSettings, create_api_runtime
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.knowledge.adapters.mysql_ready_sources import MysqlReadySources
    from tap.modules.knowledge.api import answer_response_to_http
    from tap.platform.db.session import create_engine_and_session_factory

    started_at = _now_iso()
    settings = TapperSettings.from_mapping(dict(os.environ))
    golden_json = json.loads(golden.read_text(encoding="utf-8"))
    golden_set = validate_golden(golden_json)

    from contextlib import AsyncExitStack

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

        observations: list[dict[str, Any]] = []
        for case in golden_set.cases:
            answer = await check.dry_run_answer(
                actor_id, case.question, tuple(case.expected_sources)
            )
            if answer is None:
                observations.append(
                    {
                        "id": case.id,
                        "abstained": True,
                        "answer": "",
                        "citedSources": [],
                        "claimCount": 0,
                    }
                )
                continue
            from tap.modules.knowledge.domain.models import AnswerResponse

            http_answer = answer_response_to_http(cast(AnswerResponse, answer))
            cited_sources = sorted(
                {citation.source.source_id for citation in http_answer.citations}
            )
            observations.append(
                {
                    "id": case.id,
                    "abstained": bool(http_answer.abstained),
                    "answer": http_answer.answer,
                    "citedSources": cited_sources,
                    "claimCount": len(http_answer.claims),
                }
            )

        return {
            "schemaVersion": OBSERVATIONS_SCHEMA_VERSION,
            "mode": "real",
            "build": _git_short_sha(),
            "observations": observations,
            "startedAt": started_at,
            "finishedAt": _now_iso(),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fake", "real"), required=True)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--corpus-dir", type=Path, default=None)
    parser.add_argument("--actor-id", default="validation-actor")
    arguments = parser.parse_args()

    try:
        if arguments.mode == "fake":
            if arguments.corpus_dir is None:
                parser.error("--mode fake requires --corpus-dir")
            observations = asyncio.run(
                run_fake(golden=arguments.golden, corpus_dir=arguments.corpus_dir)
            )
        else:
            if os.environ.get("TAP_RUN_QUALITY_ANSWERS") != "1":
                raise ValueError("--mode real requires TAP_RUN_QUALITY_ANSWERS=1")
            observations = asyncio.run(
                run_real(golden=arguments.golden, actor_id=arguments.actor_id)
            )
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    arguments.observations.parent.mkdir(parents=True, exist_ok=True)
    arguments.observations.write_text(
        json.dumps(observations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
