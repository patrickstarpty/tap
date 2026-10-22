#!/usr/bin/env python3
"""Produce `answer-golden-observations-v1` observations for the capability-1
answer golden set, in two modes, plus a `corpus`/`validate-corpus` pair of
real-mode setup commands.

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
- `corpus` (opt-in, requires a running Tapper API stack): uploads the fixture
  `*.md` documents as knowledge sources and writes a `{stem: sourceId}`
  manifest to `--output` (default `.local/quality-answers/corpus.json`),
  mirroring `scripts/load-graph-real-corpus.py upload`. `expectedSources` in
  the golden set are corpus document stems, not server-assigned source IDs,
  so `real` mode needs this manifest to translate between the two.
- `validate-corpus` checks a previously produced corpus manifest is
  structurally valid and covers every golden case's `expectedSources`,
  without touching any running service.
- `real` (opt-in, requires `TAP_RUN_QUALITY_ANSWERS=1`, a running Tapper API
  stack and `--corpus` from a prior `corpus` run): dry-runs each question
  through the real chat answer pipeline via
  `ConversationGroundingCheck.dry_run_answer`, mirroring
  `scripts/run-graph-relations-candidate.py`'s real-mode refusal shape.
  `dry_run_answer` returns `None` when a cited source is not ready or the
  pipeline produced an unusable answer -- never a legitimate abstention --
  so that case is recorded `invalid: true`, not `abstained: true`.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import mimetypes
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tap.quality.answer_golden import (
    OBSERVATIONS_SCHEMA_VERSION,
    GoldenCase,
    chunk_document,
    cited_sources_from_claims,
    rank_paragraphs,
    validate_golden,
)

_PROFILE_ID = "quick-hybrid-v1"
_MAX_UPLOAD_BYTES = 1 * 1024 * 1024
_POLL_INTERVAL_SECONDS = 2


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _load_documents(corpus_dir: Path) -> dict[str, str]:
    documents_paths = sorted(corpus_dir.glob("*.md"))
    if not documents_paths:
        raise ValueError(f"found no *.md documents under {corpus_dir}")
    return {path.stem: path.read_text(encoding="utf-8") for path in documents_paths}


def corpus_manifest_digest(documents: Mapping[str, str]) -> str:
    """Bind a corpus manifest to the exact fixture document content it was
    uploaded from; pure, no I/O."""
    material = json.dumps(
        {stem: documents[stem] for stem in sorted(documents)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _digest(material)


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


def _git_dirty() -> bool:
    """Whether the working tree has uncommitted changes, recorded next to
    `build` so a candidate observation can be told apart from a clean-tree
    run even when `build`'s short SHA matches a prior one."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
        )
        return bool(result.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return True


# --- fake mode ---------------------------------------------------------------


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

    # Every production retrieval turn carries a plan-bound `answer_input`
    # (`tap.modules.knowledge.application.retrieve` always runs the answer
    # planner when `answer_execution` is set) -- so the fake gate must drive
    # `KnowledgeModelGateway.answer` the same way, or it never exercises the
    # streamed inline-marker path real turns actually take (LangGraph PR 3
    # review ruling, 2026-10-16).
    from tap.modules.knowledge.application.answer_templates import (
        assemble_answer,
        get_template,
        latest_template_version,
    )

    version = latest_template_version("general")
    template = get_template("general", version)
    answer_input = assemble_answer(
        template_id="general",
        template_version=version,
        template_digest=template.digest,
        original_question=case.question,
        standalone_question=case.question,
        evidence_map={"q1": tuple(label_sources)},
    )
    generation = await gateway.answer(
        case.question, tuple(evidence), _PROFILE_ID, answer_input=answer_input
    )
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
    documents = _load_documents(corpus_dir)
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
        "dirty": False,
        "observations": observations,
        "startedAt": started_at,
        "finishedAt": _now_iso(),
    }


# --- corpus upload (real-mode setup) ----------------------------------------


def _multipart_body(
    field_name: str, filename: str, content: bytes
) -> tuple[bytes, str]:
    boundary = f"answer-golden-{uuid.uuid4().hex}"
    content_type = mimetypes.guess_type(filename)[0] or "text/markdown"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    body += content
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


def _runtime_project_id(api: str) -> str:
    request = urllib.request.Request(f"{api}/api/v1/runtime-mode")  # noqa: S310
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        body = json.loads(response.read().decode("utf-8"))
    return str(body["projectId"])


def _web_origin() -> str:
    """The web app's exact loopback Origin; the API rejects mutations without it."""
    host = os.environ.get("TAPPER_WEB_HOST", "127.0.0.1")
    port = os.environ.get("TAPPER_WEB_PORT", "5173")
    return f"http://{host}:{port}"


def _upload_source(api: str, project_id: str, stem: str, content: bytes) -> str:
    filename = f"{stem}.md"
    body, content_type = _multipart_body("upload", filename, content)
    idempotency_key = f"answer-golden-{stem}-{_digest(content.decode('utf-8'))[:12]}"
    url = f"{api}/api/v1/projects/{project_id}/knowledge/sources"
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": content_type,
            "Idempotency-Key": idempotency_key,
            "Origin": _web_origin(),
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        accepted = json.loads(response.read().decode("utf-8"))
    return str(accepted["source"]["sourceId"])


def _source_documents(
    api: str, project_id: str, source_id: str
) -> list[dict[str, Any]]:
    url = f"{api}/api/v1/projects/{project_id}/knowledge/sources/{source_id}?limit=50"
    request = urllib.request.Request(url)  # noqa: S310
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        body = json.loads(response.read().decode("utf-8"))
    documents = body.get("documents", {})
    items = documents.get("items", []) if isinstance(documents, dict) else []
    return [item for item in items if isinstance(item, dict)]


def _poll_ready(
    api: str, project_id: str, source_ids: list[str], deadline: float
) -> None:
    pending = set(source_ids)
    while pending:
        for source_id in sorted(pending):
            items = _source_documents(api, project_id, source_id)
            # Fail fast and clearly on a failed document (Task 1 deferred
            # item): without this check, a document that reaches "failed"
            # never becomes "ready", so the loop below would otherwise just
            # keep polling it until the deadline -- which can be minutes --
            # instead of reporting the real problem immediately.
            failed = [
                item
                for item in items
                if str(item.get("status", "")).lower() == "failed"
            ]
            if failed:
                details = "; ".join(
                    f"{item.get('documentId', '?')} ({item.get('errorCode') or item.get('error_code') or 'unknown error'})"
                    for item in failed
                )
                raise RuntimeError(
                    f"knowledge source {source_id} has a failed document: {details}"
                )
            ready = any(
                str(item.get("status", "")).lower() == "ready" for item in items
            )
            if ready:
                pending.discard(source_id)
        if not pending:
            return
        if time.monotonic() > deadline:
            raise TimeoutError(
                "knowledge documents did not become ready before the deadline"
            )
        time.sleep(_POLL_INTERVAL_SECONDS)


def run_corpus_upload(
    *, corpus_dir: Path, api: str, project_id: str | None, timeout_seconds: float
) -> dict[str, Any]:
    """Upload every `*.md` fixture document as a knowledge source and return the
    `{stem: sourceId}` manifest `real` mode needs to translate the golden set's
    `expectedSources` (document stems) into server-assigned source IDs."""
    documents = _load_documents(corpus_dir)
    resolved_project_id = project_id or _runtime_project_id(api)
    deadline = time.monotonic() + timeout_seconds

    sources: dict[str, str] = {}
    for stem, text in documents.items():
        content = text.encode("utf-8")
        if len(content) > _MAX_UPLOAD_BYTES:
            raise ValueError(f"{stem}.md exceeds {_MAX_UPLOAD_BYTES} bytes")
        sources[stem] = _upload_source(api, resolved_project_id, stem, content)

    _poll_ready(api, resolved_project_id, list(sources.values()), deadline)

    return {
        "schemaVersion": "answer-golden-corpus-v1",
        "manifestDigest": corpus_manifest_digest(documents),
        "projectId": resolved_project_id,
        "sources": sources,
    }


def validate_corpus_file(
    corpus: Mapping[str, Any], *, known_sources: frozenset[str]
) -> dict[str, str]:
    """Validate a previously produced corpus manifest; pure, no I/O. Returns the
    `{stem: sourceId}` mapping."""
    if corpus.get("schemaVersion") != "answer-golden-corpus-v1":
        raise ValueError("unsupported answer golden corpus schema")
    sources_raw = corpus.get("sources")
    if not isinstance(sources_raw, dict) or not sources_raw:
        raise ValueError("corpus manifest requires a nonempty sources object")
    sources: dict[str, str] = {}
    for stem, source_id in sources_raw.items():
        if not isinstance(stem, str) or not isinstance(source_id, str) or not source_id:
            raise ValueError(
                "corpus manifest sources must map document stems to source IDs"
            )
        sources[stem] = source_id
    missing = known_sources - set(sources)
    if missing:
        raise ValueError(
            f"corpus manifest is missing sources for document stems: {sorted(missing)}"
        )
    return sources


# --- real mode ----------------------------------------------------------------


def _real_observation(
    case_id: str, answer: Any, stems_by_source_id: Mapping[str, str]
) -> dict[str, Any]:
    """One observation from the HTTP `RetrievalAnswerResponse` that
    `ConversationGroundingCheck.dry_run_answer` returns (already HTTP-shaped)."""
    cited_sources = sorted(
        {
            stems_by_source_id[citation.source.source_id]
            for citation in answer.citations
            if citation.source.source_id in stems_by_source_id
        }
    )
    return {
        "id": case_id,
        "invalid": False,
        "abstained": bool(answer.abstained),
        "answer": answer.answer,
        "citedSources": cited_sources,
        "claimCount": len(answer.claims),
    }


async def run_real(
    *, golden: Path, corpus: Mapping[str, Any], actor_id: str, corpus_dir: Path
) -> dict[str, Any]:
    from tap.entrypoints.prompt_suggestion_knowledge import ConversationGroundingCheck
    from tap.entrypoints.tapper_runtime import TapperSettings, create_api_runtime
    from tap.modules.access.adapters.validation import VALIDATION_SCOPE
    from tap.modules.knowledge.adapters.mysql_ready_sources import MysqlReadySources
    from tap.platform.db.session import create_engine_and_session_factory

    from contextlib import AsyncExitStack

    started_at = _now_iso()
    settings = TapperSettings.from_mapping(dict(os.environ))
    golden_json = json.loads(golden.read_text(encoding="utf-8"))
    documents = _load_documents(corpus_dir)
    golden_set = validate_golden(golden_json, known_sources=frozenset(documents))
    source_ids = validate_corpus_file(corpus, known_sources=frozenset(documents))
    if corpus.get("manifestDigest") != corpus_manifest_digest(documents):
        raise ValueError("corpus manifest does not match the current fixture documents")
    stems_by_source_id = {value: key for key, value in source_ids.items()}

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
            case_source_ids = tuple(source_ids[stem] for stem in case.expected_sources)
            answer = await check.dry_run_answer(
                actor_id, case.question, case_source_ids
            )
            if answer is None:
                # `dry_run_answer` returns `None` when a cited source is not ready, or
                # the pipeline produced an unusable answer -- never a legitimate
                # abstention. Recording this as `abstained: true` would let a broken
                # pipeline masquerade as a model that correctly declines to answer.
                observations.append(
                    {
                        "id": case.id,
                        "invalid": True,
                        "abstained": False,
                        "answer": "",
                        "citedSources": [],
                        "claimCount": 0,
                    }
                )
                continue
            observations.append(_real_observation(case.id, answer, stems_by_source_id))

        return {
            "schemaVersion": OBSERVATIONS_SCHEMA_VERSION,
            "mode": "real",
            "build": _git_short_sha(),
            "dirty": _git_dirty(),
            "observations": observations,
            "startedAt": started_at,
            "finishedAt": _now_iso(),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("fake", "real", "corpus", "validate-corpus"), required=True
    )
    parser.add_argument("--golden", type=Path)
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--corpus-dir", type=Path, default=None)
    parser.add_argument(
        "--corpus", type=Path, default=Path(".local/quality-answers/corpus.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path(".local/quality-answers/corpus.json")
    )
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--actor-id", default="validation-actor")
    arguments = parser.parse_args()

    try:
        if arguments.mode == "fake":
            if (
                arguments.golden is None
                or arguments.observations is None
                or arguments.corpus_dir is None
            ):
                parser.error(
                    "--mode fake requires --golden, --observations and --corpus-dir"
                )
            result: dict[str, Any] = asyncio.run(
                run_fake(golden=arguments.golden, corpus_dir=arguments.corpus_dir)
            )
        elif arguments.mode == "corpus":
            if arguments.corpus_dir is None:
                parser.error("--mode corpus requires --corpus-dir")
            result = run_corpus_upload(
                corpus_dir=arguments.corpus_dir,
                api=arguments.api,
                project_id=arguments.project_id,
                timeout_seconds=arguments.timeout_seconds,
            )
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return 0
        elif arguments.mode == "validate-corpus":
            if arguments.corpus_dir is None:
                parser.error("--mode validate-corpus requires --corpus-dir")
            documents = _load_documents(arguments.corpus_dir)
            corpus_value = json.loads(arguments.corpus.read_text(encoding="utf-8"))
            sources = validate_corpus_file(
                corpus_value, known_sources=frozenset(documents)
            )
            if corpus_value.get("manifestDigest") != corpus_manifest_digest(documents):
                raise ValueError(
                    "corpus manifest does not match the current fixture documents"
                )
            print(f"corpus manifest valid: {len(sources)} sources")
            return 0
        else:
            if os.environ.get("TAP_RUN_QUALITY_ANSWERS") != "1":
                raise ValueError("--mode real requires TAP_RUN_QUALITY_ANSWERS=1")
            if (
                arguments.golden is None
                or arguments.observations is None
                or arguments.corpus_dir is None
            ):
                parser.error(
                    "--mode real requires --golden, --observations and --corpus-dir"
                )
            corpus_value = json.loads(arguments.corpus.read_text(encoding="utf-8"))
            result = asyncio.run(
                run_real(
                    golden=arguments.golden,
                    corpus=corpus_value,
                    actor_id=arguments.actor_id,
                    corpus_dir=arguments.corpus_dir,
                )
            )
    except (ValueError, OSError, TimeoutError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    arguments.observations.parent.mkdir(parents=True, exist_ok=True)
    arguments.observations.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
