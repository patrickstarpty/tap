#!/usr/bin/env python3
"""Run QUALITY-KB-01 through the production Knowledge path and capture ModelGateway audit."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import sys
import time
from collections.abc import Callable, MutableMapping
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast
from uuid import uuid4

from tap.entrypoints.tapper_runtime import TapperApiRuntime
from tap.interfaces.http.knowledge_service import KnowledgeHttpService
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.models import ModelDescriptor, ModelRequest, ModelResult
from tap.modules.ai.ports.gateway import ModelGateway


def _load_evaluator() -> ModuleType:
    path = Path(__file__).with_name("evaluate-quality-kb.py")
    spec = importlib.util.spec_from_file_location("quality_kb_evaluator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("quality evaluator cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_EVALUATOR = _load_evaluator()


class QualityKnowledgePath(Protocol):
    async def run_case(self, case: dict[str, Any]) -> dict[str, Any]: ...
    async def resolve_citation(self, citation: dict[str, Any]) -> dict[str, Any]: ...


class CapturingModelGateway:
    """Delegate to ModelGateway while deriving immutable identity from ModelResult.audit."""

    def __init__(self, delegate: ModelGateway) -> None:
        self._delegate = delegate
        self._active: list[dict[str, object]] | None = None

    def begin_attempt(self) -> None:
        if self._active is not None:
            raise RuntimeError("a gateway capture attempt is already active")
        self._active = []

    def end_attempt(self) -> list[dict[str, object]]:
        if self._active is None:
            raise RuntimeError("no gateway capture attempt is active")
        result = self._active
        self._active = None
        return result

    async def catalog(self, scope: ProjectScopeContext) -> tuple[ModelDescriptor, ...]:
        return await self._delegate.catalog(scope)

    async def chat(self, request: ModelRequest) -> ModelResult:
        return await self._capture("chat", request)

    async def embed(self, request: ModelRequest) -> ModelResult:
        return await self._capture("embed", request)

    async def generate_structured(self, request: ModelRequest) -> ModelResult:
        return await self._capture("generate_structured", request)

    async def _capture(self, method: str, request: ModelRequest) -> ModelResult:
        if self._active is None:
            raise RuntimeError("ModelGateway call occurred outside a quality attempt")
        started = time.monotonic_ns()
        result: ModelResult = await getattr(self._delegate, method)(request)
        elapsed_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
        audit = getattr(result, "audit", None)
        if audit is None or (
            getattr(result, "actual_provider", None)
            != getattr(audit, "actual_provider", None)
            or getattr(result, "actual_model", None)
            != getattr(audit, "actual_model", None)
        ):
            raise ValueError("ModelGateway result/audit identity mismatch")
        usage = getattr(audit, "usage", None)
        self._active.append(
            {
                "operation": str(getattr(audit, "operation", "")),
                "alias": getattr(audit, "alias", None),
                "actualProvider": audit.actual_provider,
                "actualModel": audit.actual_model,
                "promptDigest": audit.prompt_digest,
                "schemaDigest": audit.schema_digest,
                "contextDigest": audit.context_digest,
                "governanceDigests": list(audit.governance_digests),
                "providerRequestId": getattr(result, "provider_request_id", None),
                "gatewayCallId": getattr(result, "gateway_call_id", None),
                "inputTokens": getattr(usage, "input_tokens", None),
                "outputTokens": getattr(usage, "output_tokens", None),
                "durationMs": elapsed_ms,
            }
        )
        return result


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


async def run_dataset(
    dataset: dict[str, Any],
    *,
    gateway: ModelGateway,
    path_factory: Callable[[CapturingModelGateway], QualityKnowledgePath],
    timeout_ms: int,
    provider_call_budget: int,
    cache: MutableMapping[str, dict[str, Any]],
    max_retries_per_case: int = 2,
) -> dict[str, object]:
    """Run each cache miss once through Knowledge; no observation fields come from labels."""
    cases = _EVALUATOR.validate_dataset(dataset, min_cases=1)
    if type(timeout_ms) is not int or not 1 <= timeout_ms <= 120_000:
        raise ValueError("timeout_ms must be between 1 and 120000")
    if type(provider_call_budget) is not int or provider_call_budget < 1:
        raise ValueError("provider_call_budget must be positive")
    if type(max_retries_per_case) is not int or not 0 <= max_retries_per_case <= 2:
        raise ValueError("max_retries_per_case must be between 0 and 2")
    capture = CapturingModelGateway(gateway)
    path = path_factory(capture)
    output_cases: list[dict[str, Any]] = []
    provider_calls = cache_hits = retries = 0
    for case in cases:
        case_id = str(case["caseId"])
        cache_key = _EVALUATOR.expected_cache_key(dataset, case_id)
        started_at = _now()
        case_started = time.monotonic_ns()
        cached = cache.get(cache_key)
        if cached is not None:
            if (
                cached.get("datasetDigest") != _EVALUATOR.dataset_digest(dataset)
                or cached.get("caseId") != case_id
            ):
                raise ValueError(
                    f"cache entry for {case_id} is not bound to this dataset"
                )
            body = deepcopy(cached["body"])
            attempts: list[dict[str, object]] = []
            cache_hit = True
            cache_hits += 1
        else:
            cache_hit = False
            attempts = []
            retry_reason: str | None = None
            for attempt_number in range(1, max_retries_per_case + 2):
                attempt_started_at = _now()
                attempt_started = time.monotonic_ns()
                capture.begin_attempt()
                try:
                    async with asyncio.timeout(timeout_ms / 1000):
                        body = await path.run_case(case)
                        resolved = []
                        for citation in body.get("citations", []):
                            resolved.append(await path.resolve_citation(citation))
                        body["citations"] = resolved
                except Exception as error:
                    calls = capture.end_attempt()
                    provider_calls += len(calls)
                    attempts.append(
                        {
                            "attempt": attempt_number,
                            "retryReason": retry_reason,
                            "startedAtUtc": attempt_started_at,
                            "durationMs": max(
                                0, (time.monotonic_ns() - attempt_started) // 1_000_000
                            ),
                            "providerCalls": calls,
                        }
                    )
                    if attempt_number > max_retries_per_case:
                        raise
                    retry_reason = type(error).__name__
                    retries += 1
                else:
                    calls = capture.end_attempt()
                    if not calls:
                        raise RuntimeError(
                            f"case {case_id} cache miss made no ModelGateway call"
                        )
                    provider_calls += len(calls)
                    attempts.append(
                        {
                            "attempt": attempt_number,
                            "retryReason": retry_reason,
                            "startedAtUtc": attempt_started_at,
                            "durationMs": max(
                                0, (time.monotonic_ns() - attempt_started) // 1_000_000
                            ),
                            "providerCalls": calls,
                        }
                    )
                    break
            cache[cache_key] = {
                "datasetDigest": _EVALUATOR.dataset_digest(dataset),
                "caseId": case_id,
                "body": deepcopy(body),
            }
        duration_ms = max(0, (time.monotonic_ns() - case_started) // 1_000_000)
        if duration_ms > timeout_ms:
            raise TimeoutError(f"case {case_id} exceeded its total timeout")
        output_cases.append(
            {
                "caseId": case_id,
                **body,
                "execution": {
                    "timeoutMs": timeout_ms,
                    "startedAtUtc": started_at,
                    "durationMs": duration_ms,
                    "cacheKey": cache_key,
                    "cacheHit": cache_hit,
                    "attempts": attempts,
                },
            }
        )
    if provider_calls > provider_call_budget:
        raise RuntimeError("provider call count exceeds configured budget")
    return {
        "schemaVersion": "quality-kb-observations-v1",
        "datasetDigest": _EVALUATOR.dataset_digest(dataset),
        "runId": str(uuid4()),
        "execution": {
            "providerCallBudget": provider_call_budget,
            "providerCalls": provider_calls,
            "cacheHits": cache_hits,
            "retryCount": retries,
            "maxRetriesPerCase": 2,
        },
        "cases": output_cases,
    }


class RuntimeKnowledgePath:
    """Adapter over the production in-process HTTP Knowledge service."""

    def __init__(self, runtime: TapperApiRuntime) -> None:
        self._runtime = runtime
        if runtime.http_services.knowledge is None:
            raise RuntimeError("production Knowledge service is unavailable")
        self._knowledge = cast(KnowledgeHttpService, runtime.http_services.knowledge)

    async def run_case(self, case: dict[str, Any]) -> dict[str, Any]:
        from tap.contracts.http import (
            ResourceMode,
            ResourceRef,
            RetrievalAnswerRequest,
            RetrievalSearchRequest,
            SourceFamily,
        )
        from tap.modules.ai.domain.models import text_digest

        refs = [
            ResourceRef(
                family=SourceFamily.DOC,
                source_id=source_id,
                mode=ResourceMode.SCOPE,
            )
            for source_id in case["selectedSourceIds"]
        ]
        request = RetrievalSearchRequest(query=case["question"], resource_refs=refs)
        search = await self._knowledge.search(request)
        answer = await self._knowledge.answer(
            RetrievalAnswerRequest.model_validate(request.model_dump(by_alias=True))
        )
        return {
            "retrieved": [
                {
                    "rank": rank,
                    "projectId": case["projectId"],
                    "sourceId": hit.source.source_id,
                    "documentRevisionId": hit.source.revision,
                    "chunkId": hit.chunk_id,
                }
                for rank, hit in enumerate(search.hits, 1)
            ],
            "abstained": answer.abstained,
            "claims": [
                {
                    "claimId": claim.claim_id,
                    "textDigest": text_digest(claim.text),
                    "citationIds": claim.citation_ids,
                }
                for claim in answer.claims
            ],
            "citations": [
                {
                    "citationId": citation.citation_id,
                    "projectId": case["projectId"],
                    "sourceId": citation.source.source_id,
                    "documentRevisionId": citation.source.revision,
                    "chunkId": citation.chunk_id,
                }
                for citation in answer.citations
            ],
        }

    async def resolve_citation(self, citation: dict[str, Any]) -> dict[str, Any]:
        preview = await self._knowledge.citation(citation["citationId"])
        result = dict(citation)
        result["documentRevisionId"] = preview.revision_id
        result["locator"] = preview.anchor.root.model_dump(
            by_alias=True, exclude_none=True
        )
        result["resolvedEvidenceDigest"] = preview.chunk_content_hash
        return result


def _approved_mapping_from_env() -> tuple[str, str, str]:
    provider = os.environ.get("TAP_QUALITY_KB_APPROVED_PROVIDER", "")
    model = os.environ.get("TAP_QUALITY_KB_APPROVED_MODEL", "")
    digest = os.environ.get("TAP_QUALITY_KB_MODEL_APPROVAL_DIGEST", "")
    if (
        not provider
        or not model
        or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
    ):
        raise ValueError("approved actual provider/model mapping")
    return provider, model, digest


async def _main_async(args: argparse.Namespace) -> int:
    if os.environ.get("TAP_RUN_QUALITY_KB_01") != "1":
        print(
            "quality-kb-real requires TAP_RUN_QUALITY_KB_01=1; provider I/O was not started",
            file=sys.stderr,
        )
        return 2
    try:
        approved = _approved_mapping_from_env()
    except ValueError:
        print(
            "quality-kb-real requires an approved actual provider/model mapping; provider I/O was not started",
            file=sys.stderr,
        )
        return 2
    try:
        dataset = _EVALUATOR._load_json(args.dataset)
        cases = _EVALUATOR.validate_dataset(dataset, min_cases=100)
        if len(cases) < 100:
            raise ValueError("at least 100 human-labeled cases are required")
        if dataset["dataset"].get("labelingMethod") != "human" or not dataset[
            "dataset"
        ].get("provenance"):
            raise ValueError("traceable human labels are required")
    except (OSError, TypeError, ValueError) as error:
        print(
            f"quality-kb-real dataset invalid: {error}; provider I/O was not started",
            file=sys.stderr,
        )
        return 2
    try:
        from tap.entrypoints.tapper_runtime import TapperSettings, create_api_runtime

        settings = TapperSettings.from_mapping(os.environ)
    except (KeyError, TypeError, ValueError) as error:
        print(
            f"quality-kb-real provider configuration invalid: {error}; provider I/O was not started",
            file=sys.stderr,
        )
        return 2
    runtime = await create_api_runtime(settings)
    try:
        original_gateway = runtime.quality_models.gateway

        def path_factory(capture: CapturingModelGateway) -> RuntimeKnowledgePath:
            runtime.quality_models.gateway = capture
            return RuntimeKnowledgePath(runtime)

        observations = await run_dataset(
            dataset,
            gateway=original_gateway,
            path_factory=path_factory,
            timeout_ms=args.timeout_ms,
            provider_call_budget=args.provider_call_budget,
            cache={},
        )
        report = _EVALUATOR.evaluate_run(
            dataset,
            observations,
            min_cases=100,
            require_real=True,
            approved_mapping=approved,
            provider_call_budget=args.provider_call_budget,
        )
    finally:
        await runtime.aclose()
    args.observations.parent.mkdir(parents=True, exist_ok=True)
    args.observations.write_text(
        json.dumps(observations, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    if report["status"] != "pass":
        print("QUALITY-KB-01 real gate failed", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument(
        "--report", type=Path, default=Path(".local/quality-kb/report.json")
    )
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument("--provider-call-budget", type=int, default=500)
    return asyncio.run(_main_async(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
