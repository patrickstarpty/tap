#!/usr/bin/env python3
"""Run QUALITY-KB-01 through the production Knowledge path and capture ModelGateway audit."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from collections.abc import Awaitable, Callable, MutableMapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast
from uuid import uuid4

from tap.entrypoints.tapper_runtime import TapperApiRuntime
from tap.interfaces.http.knowledge_service import KnowledgeHttpService
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.models import (
    ModelDescriptor,
    ModelGatewayUnavailable,
    ModelRequest,
    ModelResult,
)
from tap.modules.ai.ports.gateway import ModelGateway
from tap.modules.knowledge.application.answers import (
    AnswerSelectionRejected,
    DocumentStateChanged,
)
from tap.modules.knowledge.domain.sources import SourceUnavailable
from tap.modules.knowledge.ports.errors import AnswerUnavailable, ModelUnavailable


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

_MAX_APPROVAL_VALIDITY = timedelta(days=30)


class QualityKnowledgePath(Protocol):
    async def resolve_authority(self, case: dict[str, Any]) -> dict[str, Any]: ...
    async def run_case(self, case: dict[str, Any]) -> dict[str, Any]: ...
    async def resolve_citation(self, citation: dict[str, Any]) -> dict[str, Any]: ...


class PolicyDigestMismatch(ValueError):
    """The server-rebuilt retrieval policy differs from the dataset binding."""


class ApprovalExpired(RuntimeError):
    """The immutable approval expired before the next provider attempt."""


def _system_clock() -> datetime:
    return datetime.now(UTC)


async def _bounded_retry_backoff(attempt_number: int) -> None:
    await asyncio.sleep(min(2**attempt_number, 8))


class ProviderCallBudget:
    """Reserve each provider call atomically before delegate I/O."""

    def __init__(
        self,
        maximum: int,
        *,
        expires_at: datetime | None = None,
        clock: Callable[[], datetime] = _system_clock,
    ) -> None:
        self.maximum = maximum
        self.count = 0
        self._lock = asyncio.Lock()
        self.expires_at = expires_at or datetime.max.replace(tzinfo=UTC)
        self._clock = clock

    async def reserve(self) -> datetime:
        async with self._lock:
            started_at = self._clock()
            if started_at.tzinfo != UTC:
                raise ValueError("quality clock must return UTC")
            if started_at >= self.expires_at:
                raise ApprovalExpired("quality approval expired before provider I/O")
            if self.count >= self.maximum:
                raise RuntimeError("provider call budget exhausted before provider I/O")
            self.count += 1
            return started_at


class CapturingModelGateway:
    """Delegate to ModelGateway while deriving immutable identity from ModelResult.audit."""

    def __init__(
        self,
        delegate: ModelGateway,
        budget: ProviderCallBudget,
        production_routes: dict[str, dict[str, Any]] | None = None,
        *,
        clock: Callable[[], datetime] = _system_clock,
        expires_at: datetime | None = None,
    ) -> None:
        self._delegate = delegate
        self._budget = budget
        self._routes = production_routes or {}
        self._active: list[dict[str, object]] | None = None
        self._clock = clock
        self._expires_at = expires_at or datetime.max.replace(tzinfo=UTC)

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
        route = self._routes.get(request.operation.value)
        if route is None or route.get("logicalAlias") != request.alias:
            raise ValueError("production ModelGateway route is not captured")
        call_started_at = await self._budget.reserve()
        receipt: dict[str, object] = {
            "runnerCallId": str(uuid4()),
            "requestedAlias": request.alias,
            "requestedOperation": request.operation.value,
            "requestedProvider": route["actualProvider"],
            "requestedModel": route["actualModel"],
            "startedAtUtc": _utc_text(call_started_at),
            "status": "started",
            "operation": request.operation.value,
            "alias": request.alias,
            "promptDigest": request.prompt_digest,
            "schemaDigest": request.schema_digest,
            "governanceDigests": list(request.governance_digests),
        }
        self._active.append(receipt)
        started = time.monotonic_ns()
        result: ModelResult | None = None
        try:
            result = await getattr(self._delegate, method)(request)
            audit = getattr(result, "audit", None)
            if audit is None or (
                getattr(result, "actual_provider", None)
                != getattr(audit, "actual_provider", None)
                or getattr(result, "actual_model", None)
                != getattr(audit, "actual_model", None)
            ):
                raise ValueError("ModelGateway result/audit identity mismatch")
            usage = getattr(audit, "usage", None)
            receipt.update(
                {
                    "status": "success",
                    "retryable": False,
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
                }
            )
            return result
        except BaseException as error:
            receipt.update(
                {
                    "status": "error",
                    "retryable": isinstance(
                        error,
                        (ModelGatewayUnavailable, TimeoutError, ConnectionError),
                    ),
                    "errorCode": type(error).__name__[:64],
                }
            )
            if result is not None:
                receipt.update(
                    {
                        "actualProvider": getattr(result, "actual_provider", None),
                        "actualModel": getattr(result, "actual_model", None),
                        "providerRequestId": getattr(
                            result, "provider_request_id", None
                        ),
                        "gatewayCallId": getattr(result, "gateway_call_id", None),
                    }
                )
            raise
        finally:
            receipt["durationMs"] = max(0, (time.monotonic_ns() - started) // 1_000_000)
            ended_at = self._clock()
            receipt["endedAtUtc"] = _utc_text(ended_at)
            if ended_at > self._expires_at:
                raise ApprovalExpired("quality approval expired during provider I/O")


def _utc_text(value: datetime) -> str:
    if value.tzinfo != UTC:
        raise ValueError("quality clock must return UTC")
    return value.isoformat().replace("+00:00", "Z")


def _now(clock: Callable[[], datetime] = _system_clock) -> str:
    return _utc_text(clock())


async def run_dataset(
    dataset: dict[str, Any],
    *,
    gateway: ModelGateway,
    path_factory: Callable[[CapturingModelGateway], QualityKnowledgePath],
    timeout_ms: int,
    provider_call_budget: int,
    cache: MutableMapping[str, dict[str, Any]],
    max_retries_per_case: int = 2,
    approved_mapping: dict[str, Any] | None = None,
    production_routes: dict[str, dict[str, Any]] | None = None,
    clock: Callable[[], datetime] = _system_clock,
    retry_backoff: Callable[[int], Awaitable[None]] | None = None,
) -> dict[str, object]:
    """Run each cache miss once through Knowledge; no observation fields come from labels."""
    cases = _EVALUATOR.validate_dataset(dataset, min_cases=1)
    if type(timeout_ms) is not int or not 1 <= timeout_ms <= 120_000:
        raise ValueError("timeout_ms must be between 1 and 120000")
    if type(provider_call_budget) is not int or provider_call_budget < 1:
        raise ValueError("provider_call_budget must be positive")
    if type(max_retries_per_case) is not int or not 0 <= max_retries_per_case <= 2:
        raise ValueError("max_retries_per_case must be between 0 and 2")
    if cache:
        raise ValueError("pre-populated cache is forbidden for the V1 real gate")
    if approved_mapping is not None:
        if production_routes is None:
            raise ValueError("production ModelGateway routes are required")
        _require_approved_production_routes(approved_mapping, production_routes)
    minimum_provider_calls = sum(
        case["authorizationExpected"] == "allow" for case in cases
    )
    if provider_call_budget < minimum_provider_calls:
        raise ValueError(
            "provider call budget is below the minimum eligible-case call bound"
        )
    expires_at = (
        _EVALUATOR._utc(approved_mapping["expiresAtUtc"], "approval expiry")
        if approved_mapping is not None
        else datetime.max.replace(tzinfo=UTC)
    )
    run_started_at = clock()
    if run_started_at >= expires_at:
        raise ApprovalExpired("quality approval expired before run")
    budget = ProviderCallBudget(
        provider_call_budget, expires_at=expires_at, clock=clock
    )
    capture = CapturingModelGateway(
        gateway,
        budget,
        production_routes,
        clock=clock,
        expires_at=expires_at,
    )
    path = path_factory(capture)
    output_cases: list[dict[str, Any]] = []
    provider_calls = cache_hits = retries = 0
    for case in cases:
        case_id = str(case["caseId"])
        case_started_at = clock()
        if case_started_at >= expires_at:
            raise ApprovalExpired(f"case {case_id} started after approval expiry")
        started_at = _utc_text(case_started_at)
        case_started = time.monotonic_ns()
        authority = await path.resolve_authority(case)
        actual_project = authority.get("actualProjectId")
        actual_enterprise = authority.get("actualEnterpriseId")
        authorized_sources = authority.get("authorizedSourceIds")
        if (
            not isinstance(actual_enterprise, str)
            or not isinstance(actual_project, str)
            or not isinstance(authorized_sources, list)
        ):
            raise ValueError(f"case {case_id} server authority evidence is invalid")
        selected = set(case["selectedSourceIds"])
        actual_authorized = set(authorized_sources)
        project_mismatch = case["projectId"] != actual_project
        source_mismatch = not selected <= actual_authorized
        denied_reason = (
            "project-mismatch"
            if project_mismatch
            else "source-not-authorized"
            if source_mismatch
            else None
        )
        authority_observation = {
            "actualEnterpriseId": actual_enterprise,
            "actualProjectId": actual_project,
            "actualPolicyDigest": None,
            "authorizedSourceIds": sorted(actual_authorized),
            "decision": "deny" if denied_reason else "allow",
            "reason": denied_reason,
        }
        if authority_observation["decision"] != case["authorizationExpected"]:
            raise ValueError(
                f"case {case_id} authorization label contradicts server authority"
            )
        cache_key = _EVALUATOR.expected_cache_key(dataset, case_id)
        cached = cache.get(cache_key)
        if cached is not None:
            if (
                cached.get("datasetDigest") != _EVALUATOR.dataset_digest(dataset)
                or cached.get("caseId") != case_id
                or cached.get("authority") != authority_observation
            ):
                raise ValueError(
                    f"cache entry for {case_id} is not bound to current dataset/authority"
                )
            body = deepcopy(cached["body"])
            attempts: list[dict[str, object]] = []
            cache_hit = True
            cache_hits += 1
        else:
            cache_hit = False
            attempts = []
            retry_reason: str | None = None
            if project_mismatch:
                body = {
                    "retrieved": [],
                    "abstained": True,
                    "claims": [],
                    "citations": [],
                }
                attempts.append(
                    {
                        "attempt": 1,
                        "retryReason": None,
                        "startedAtUtc": _now(clock),
                        "durationMs": 0,
                        "outcome": "authorization-denied",
                        "providerCalls": [],
                    }
                )
            else:
                body = {}
            for attempt_number in (
                () if project_mismatch else range(1, max_retries_per_case + 2)
            ):
                attempt_started_at = _now(clock)
                attempt_started = time.monotonic_ns()
                capture.begin_attempt()
                try:
                    async with asyncio.timeout(timeout_ms / 1000):
                        body = await path.run_case(case)
                        actual_policy_digest = body.pop("actualPolicyDigest", None)
                        if actual_policy_digest != dataset["bindings"]["policyDigest"]:
                            raise PolicyDigestMismatch(
                                f"case {case_id} actual policy digest mismatch"
                            )
                        authority_observation["actualPolicyDigest"] = (
                            actual_policy_digest
                        )
                        resolved = []
                        for citation in body.get("citations", []):
                            resolved.append(await path.resolve_citation(citation))
                        body["citations"] = resolved
                except PolicyDigestMismatch:
                    calls = capture.end_attempt()
                    provider_calls += len(calls)
                    attempts.append(
                        {
                            "attempt": attempt_number,
                            "retryReason": retry_reason,
                            "startedAtUtc": attempt_started_at,
                            "durationMs": max(
                                0,
                                (time.monotonic_ns() - attempt_started) // 1_000_000,
                            ),
                            "providerCalls": calls,
                        }
                    )
                    raise
                except (
                    AuthorizationDenied,
                    AnswerSelectionRejected,
                    DocumentStateChanged,
                ):
                    calls = capture.end_attempt()
                    if calls or not source_mismatch:
                        raise RuntimeError(
                            f"case {case_id} invalid authorization rejection evidence"
                        )
                    body = {
                        "retrieved": [],
                        "abstained": True,
                        "claims": [],
                        "citations": [],
                    }
                    attempts.append(
                        {
                            "attempt": attempt_number,
                            "retryReason": retry_reason,
                            "startedAtUtc": attempt_started_at,
                            "durationMs": max(
                                0, (time.monotonic_ns() - attempt_started) // 1_000_000
                            ),
                            "outcome": "authorization-denied",
                            "providerCalls": [],
                        }
                    )
                    break
                except Exception as error:
                    calls = capture.end_attempt()
                    if (
                        isinstance(error, (AnswerUnavailable, ModelUnavailable))
                        and calls
                    ):
                        provider_error_code = calls[-1].get("errorCode")
                        error.add_note(f"provider error: {provider_error_code}")
                        calls[-1].update(
                            status="error",
                            retryable=True,
                            errorCode=type(error).__name__,
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
                    retryable_provider_failure = bool(
                        calls
                        and calls[-1].get("status") == "error"
                        and calls[-1].get("retryable") is True
                    )
                    if not retryable_provider_failure:
                        error.add_note(f"quality case: {case_id}")
                        raise
                    if attempt_number > max_retries_per_case:
                        error.add_note(f"quality case: {case_id}")
                        raise
                    retry_reason = str(calls[-1]["errorCode"])
                    retries += 1
                    if retry_backoff is not None:
                        await retry_backoff(attempt_number)
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
        observation = {
            "caseId": case_id,
            "authority": authority_observation,
            **body,
        }
        evidence_digest = _EVALUATOR.runtime_evidence_digest(observation)
        if cache_hit:
            assert cached is not None
            if cached.get("evidenceDigest") != evidence_digest:
                raise ValueError(f"cache evidence for {case_id} is invalid")
        else:
            cache[cache_key] = {
                "datasetDigest": _EVALUATOR.dataset_digest(dataset),
                "caseId": case_id,
                "authority": deepcopy(authority_observation),
                "evidenceDigest": evidence_digest,
                "body": deepcopy(body),
            }
        case_finished_at = clock()
        if case_finished_at > expires_at:
            raise ApprovalExpired(f"case {case_id} finished after approval expiry")
        duration_ms = max(0, (time.monotonic_ns() - case_started) // 1_000_000)
        if duration_ms > timeout_ms:
            raise TimeoutError(f"case {case_id} exceeded its total timeout")
        observation["execution"] = {
            "timeoutMs": timeout_ms,
            "startedAtUtc": started_at,
            "finishedAtUtc": _utc_text(case_finished_at),
            "durationMs": duration_ms,
            "cacheKey": cache_key,
            "cacheEvidenceDigest": evidence_digest,
            "cacheHit": cache_hit,
            "maxRetriesPerCase": max_retries_per_case,
            "attempts": attempts,
        }
        output_cases.append(observation)
    if provider_calls != budget.count:
        raise RuntimeError("captured provider calls do not match reserved budget calls")
    run_finished_at = clock()
    if run_finished_at > expires_at:
        raise ApprovalExpired("quality run finished after approval expiry")
    return {
        "schemaVersion": "quality-kb-observations-v1",
        "datasetDigest": _EVALUATOR.dataset_digest(dataset),
        "runId": str(uuid4()),
        "execution": {
            "startedAtUtc": _utc_text(run_started_at),
            "finishedAtUtc": _utc_text(run_finished_at),
            "providerCallBudget": provider_call_budget,
            "providerCalls": provider_calls,
            "cacheHits": cache_hits,
            "retryCount": retries,
            "maxRetriesPerCase": max_retries_per_case,
        },
        "cases": output_cases,
    }


def _quality_policy_digest(policy: object) -> str:
    from tap.modules.chat.domain.conversations import content_digest

    return content_digest(
        {
            "policyVersion": getattr(policy, "policy_version"),
            "corpusVersion": getattr(policy, "active_corpus_version"),
        }
    )


class RuntimeKnowledgePath:
    """Adapter over the production in-process HTTP Knowledge service."""

    def __init__(self, runtime: TapperApiRuntime, dataset: dict[str, Any]) -> None:
        self._runtime = runtime
        if runtime.http_services.knowledge is None:
            raise RuntimeError("production Knowledge service is unavailable")
        if runtime.http_services.asset_catalog is None:
            raise RuntimeError("production Agent/Skill catalog is unavailable")
        self._knowledge = cast(KnowledgeHttpService, runtime.http_services.knowledge)
        self._assets = runtime.http_services.asset_catalog
        self._bindings = cast(dict[str, Any], dataset["bindings"])

    async def resolve_authority(self, case: dict[str, Any]) -> dict[str, Any]:
        del case
        source_ids: list[str] = []
        cursor: str | None = None
        while True:
            page = await self._knowledge.list_sources(cursor, 50)
            source_ids.extend(item.source_id for item in page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
            if len(source_ids) > 10_000:
                raise RuntimeError(
                    "server Source authority exceeds quality-runner bound"
                )
        return {
            "actualEnterpriseId": self._knowledge.scope.enterprise_id,
            "actualProjectId": self._knowledge.scope.project_id,
            "authorizedSourceIds": sorted(source_ids),
        }

    async def run_case(self, case: dict[str, Any]) -> dict[str, Any]:
        from tap.contracts.http import (
            ResourceMode,
            ResourceRef,
            RetrievalAnswerRequest,
            SourceFamily,
        )
        from tap.modules.ai.domain.models import text_digest
        from tap.modules.ai.application.assets import (
            resolve_agent_selection,
            resolve_skill_selection,
        )
        from tap.modules.chat.domain.conversations import (
            FrozenResource,
            TurnInput,
            content_digest,
        )

        actual_project = self._knowledge.scope.project_id

        refs = [
            ResourceRef(
                family=SourceFamily.DOC,
                source_id=source_id,
                mode=ResourceMode.SCOPE,
            )
            for source_id in case["selectedSourceIds"]
        ]
        request = RetrievalAnswerRequest(query=case["question"], resource_refs=refs)
        revision_ids: list[str] = []
        for source_id in case["selectedSourceIds"]:
            cursor: str | None = None
            while True:
                try:
                    detail = await self._knowledge.get_source(source_id, cursor, 50)
                except SourceUnavailable:
                    raise AuthorizationDenied("source-not-authorized") from None
                revision_ids.extend(
                    item.revision_id
                    for item in detail.documents.items
                    if item.status.value == "ready"
                )
                cursor = detail.documents.next_cursor
                if cursor is None:
                    break
                if len(revision_ids) > 20:
                    raise ValueError("quality case revision selection exceeds bound")
        if not 1 <= len(revision_ids) <= 20 or len(revision_ids) != len(
            set(revision_ids)
        ):
            raise ValueError(
                "quality case has no unique bounded ready revision selection"
            )
        revisions, policy = await self._knowledge.resolve_conversation_selection(
            tuple(revision_ids)
        )
        retrieval_policy_digest = _quality_policy_digest(policy)
        if retrieval_policy_digest != self._bindings["policyDigest"]:
            raise PolicyDigestMismatch("actual retrieval policy digest mismatch")
        conversation_policy_digest = content_digest(
            {
                "decisionId": policy.decision_id,
                "policyVersion": policy.policy_version,
                "corpusVersion": policy.active_corpus_version,
            }
        )
        scope = self._knowledge.scope
        agents = [
            item
            for item in await self._assets.list_agents(scope)
            if item.content_digest == self._bindings["agentRevisionDigest"]
        ]
        if len(agents) != 1:
            raise ValueError(
                "dataset Agent digest does not resolve uniquely in server catalog"
            )
        agent = resolve_agent_selection(
            agents[0],
            tools=frozenset({"knowledge.answer"}),
            output_schema_digest=self._bindings["schemaDigest"],
        )
        available_skills = await self._assets.list_skills(scope)
        skills = []
        for digest in self._bindings["skillRevisionDigests"]:
            matches = [
                item for item in available_skills if item.content_digest == digest
            ]
            if len(matches) != 1:
                raise ValueError(
                    "dataset Skill digest does not resolve uniquely in server catalog"
                )
            skills.append(resolve_skill_selection(matches[0], task="knowledge.answer"))
        frozen_input = TurnInput(
            message=case["question"],
            actor_id=scope.actor_id,
            identity_mode=scope.identity_mode.value,
            model_alias=self._bindings["modelAlias"],
            document_revision_ids=tuple(revision_ids),
            resolved_resources=tuple(
                FrozenResource(
                    source_id=item.source_id or item.document_id,
                    document_id=item.document_id,
                    revision_id=item.revision_id,
                    source_content_hash=item.source_content_hash,
                    document_revision_id=item.revision_id,
                )
                for item in revisions
            ),
            agent_revision_id=agent.revision_id,
            agent_revision_digest=agent.content_digest,
            agent_system_instruction=agent.system_instruction,
            agent_system_instruction_digest=agent.system_instruction_digest,
            agent_tool_allowlist=tuple(sorted(agent.tool_allowlist)),
            agent_output_schema_json=agent.output_schema_json,
            agent_output_schema_digest=agent.output_schema_digest,
            skill_revision_ids=tuple(item.revision_id for item in skills),
            skill_revision_digests=tuple(item.content_digest for item in skills),
            skill_instruction_templates=tuple(
                cast(str, item.instruction_template) for item in skills
            ),
            skill_instruction_template_digests=tuple(
                item.instruction_template_digest for item in skills
            ),
            acl_digest=policy.acl_digest,
            retrieval_policy_digest=conversation_policy_digest,
        )
        answer = await self._knowledge.answer_conversation(
            request,
            frozen_input,
        )
        return {
            "actualPolicyDigest": retrieval_policy_digest,
            "retrieved": [
                {
                    "rank": rank,
                    "projectId": actual_project,
                    "sourceId": hit.source.source_id,
                    "documentRevisionId": hit.source.revision,
                    "chunkId": hit.chunk_id,
                }
                for rank, hit in enumerate(answer.citations, 1)
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
                    "projectId": actual_project,
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


def _load_approval_artifact(
    path: Path, expected_digest: str, *, now: datetime | None = None
) -> dict[str, Any]:
    if re.fullmatch(r"sha256:[0-9a-f]{64}", expected_digest) is None:
        raise ValueError("approval digest is invalid")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ValueError("approval artifact cannot be read") from error
    if not raw or len(raw) > 65_536:
        raise ValueError("approval artifact size is invalid")
    actual_digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    if actual_digest != expected_digest:
        raise ValueError("approval artifact digest mismatch")

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("approval artifact has duplicate fields")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("approval artifact is not valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("approval artifact must be an object")
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    if raw != canonical:
        raise ValueError("approval artifact bytes are not canonical JSON")
    allowed = {
        "schemaVersion",
        "approvalId",
        "routes",
        "expiresAtUtc",
    }
    required = allowed
    if "expiresAtUtc" not in value:
        raise ValueError("approval artifact expiry is required")
    if set(value) - allowed or not required <= set(value):
        raise ValueError("approval artifact fields are invalid")
    if value.get("schemaVersion") != "quality-kb-model-approval-v2":
        raise ValueError("approval artifact schema is invalid")
    for name in ("approvalId",):
        item = value.get(name)
        if (
            not isinstance(item, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", item) is None
        ):
            raise ValueError(f"approval artifact {name} is invalid")
    routes = value.get("routes")
    if not isinstance(routes, list) or len(routes) != 3:
        raise ValueError("approval artifact routes are invalid")
    expected_operations = ("embed", "chat", "structured")
    for expected_operation, route in zip(expected_operations, routes, strict=True):
        if not isinstance(route, dict) or set(route) != {
            "logicalAlias",
            "actualProvider",
            "actualModel",
            "operation",
            "scope",
        }:
            raise ValueError("approval artifact routes are invalid")
        if route.get("operation") != expected_operation:
            raise ValueError("approval artifact routes are invalid")
        for name in ("logicalAlias", "actualProvider", "actualModel"):
            item = route.get(name)
            if (
                not isinstance(item, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", item) is None
            ):
                raise ValueError("approval artifact routes are invalid")
        scope = route.get("scope")
        if not isinstance(scope, dict) or set(scope) != {
            "enterpriseId",
            "projectId",
        }:
            raise ValueError("approval artifact route scope is invalid")
        for item in scope.values():
            if not isinstance(item, str) or not item.strip() or len(item) > 128:
                raise ValueError("approval artifact route scope is invalid")
    expires = value.get("expiresAtUtc")
    if not isinstance(expires, str):
        raise ValueError("approval artifact expiry is required")
    expiry = _EVALUATOR._utc(expires, "approval expiry")
    run_started = now or datetime.now(UTC)
    if expiry <= run_started:
        raise ValueError("approval artifact is expired")
    if expiry > run_started + _MAX_APPROVAL_VALIDITY:
        raise ValueError("approval artifact validity window exceeds 30 days")
    return {**value, "digest": actual_digest, "artifact": value["approvalId"]}


def _require_approval_scope(
    approved: dict[str, Any], enterprise_id: str, project_id: str
) -> None:
    expected = {"enterpriseId": enterprise_id, "projectId": project_id}
    routes = approved.get("routes")
    if not isinstance(routes, list) or any(
        not isinstance(route, dict) or route.get("scope") != expected
        for route in routes
    ):
        raise ValueError("approval artifact scope does not match actual runtime scope")


def _require_single_http_attempt_gateway(gateway: ModelGateway) -> ModelGateway:
    from tap.modules.ai.adapters.litellm import LiteLLMModelGateway

    if type(gateway) is not LiteLLMModelGateway or gateway._config.max_retries != 0:
        raise ValueError("quality ModelGateway internal retries must equal zero")
    return gateway


def _production_routes_from_gateway(
    gateway: ModelGateway,
) -> dict[str, dict[str, Any]]:
    from tap.modules.ai.adapters.litellm import LiteLLMModelGateway

    if type(gateway) is not LiteLLMModelGateway:
        raise ValueError("quality ModelGateway must be production LiteLLM")
    config = gateway._config
    return {
        "embed": {
            "logicalAlias": config.embedding_alias,
            "actualProvider": config.embedding_model.provider,
            "actualModel": config.embedding_model.route,
            "operation": "embed",
            "scope": {
                "enterpriseId": gateway.scope.enterprise_id,
                "projectId": gateway.scope.project_id,
            },
        },
        "chat": {
            "logicalAlias": config.chat_alias,
            "actualProvider": config.chat_model.provider,
            "actualModel": config.chat_model.route,
            "operation": "chat",
            "scope": {
                "enterpriseId": gateway.scope.enterprise_id,
                "projectId": gateway.scope.project_id,
            },
        },
        "structured": {
            "logicalAlias": config.chat_alias,
            "actualProvider": config.chat_model.provider,
            "actualModel": config.chat_model.route,
            "operation": "structured",
            "scope": {
                "enterpriseId": gateway.scope.enterprise_id,
                "projectId": gateway.scope.project_id,
            },
        },
    }


def _require_approved_production_routes(
    approved: dict[str, Any], production_routes: dict[str, dict[str, Any]]
) -> None:
    routes = approved.get("routes")
    approved_routes = (
        {str(route.get("operation")): route for route in routes}
        if isinstance(routes, list) and all(isinstance(route, dict) for route in routes)
        else {}
    )
    if approved_routes != production_routes:
        raise ValueError("approval routes do not match production ModelGateway routes")


def _approved_mapping_from_env(*, now: datetime | None = None) -> dict[str, Any]:
    digest = os.environ.get("TAP_QUALITY_KB_MODEL_APPROVAL_DIGEST", "")
    artifact = os.environ.get("TAP_QUALITY_KB_MODEL_APPROVAL_ARTIFACT", "")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None or not artifact:
        raise ValueError("approved actual provider/model/operation mapping")
    return _load_approval_artifact(Path(artifact), digest, now=now)


def _require_governance_bindings(dataset: dict[str, Any]) -> None:
    bindings = dataset.get("bindings")
    if not isinstance(bindings, dict):
        raise ValueError("governance bindings are required")
    for name in (
        "policyDigest",
        "approvalDigest",
        "promptDigest",
        "agentRevisionDigest",
        "schemaDigest",
    ):
        if (
            not isinstance(bindings.get(name), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", bindings[name]) is None
        ):
            raise ValueError(f"{name} governance binding is required")
    for name in ("skillRevisionDigests", "governanceDigests"):
        values = bindings.get(name)
        if (
            not isinstance(values, list)
            or not values
            or any(
                not isinstance(item, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", item) is None
                for item in values
            )
        ):
            raise ValueError(f"{name} governance binding is required")
    alias = bindings.get("modelAlias")
    if (
        not isinstance(alias, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", alias) is None
    ):
        raise ValueError("modelAlias governance binding is required")


async def _main_async(args: argparse.Namespace) -> int:
    run_started_at = datetime.now(UTC)
    if os.environ.get("TAP_RUN_QUALITY_KB_01") != "1":
        print(
            "quality-kb-real requires TAP_RUN_QUALITY_KB_01=1; provider I/O was not started",
            file=sys.stderr,
        )
        return 2
    try:
        approved = _approved_mapping_from_env(now=run_started_at)
    except ValueError:
        print(
            "quality-kb-real requires an approved actual provider/model/operation mapping; provider I/O was not started",
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
        _require_governance_bindings(dataset)
        if dataset["bindings"]["approvalDigest"] != approved["digest"]:
            raise ValueError("dataset approval digest does not match approval artifact")
        if dataset["bindings"]["modelAlias"] != approved["routes"][2]["logicalAlias"]:
            raise ValueError("dataset model alias does not match approval artifact")
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
    runtime = await create_api_runtime(settings, model_gateway_max_retries=0)
    try:
        knowledge = runtime.http_services.knowledge
        if knowledge is None:
            raise RuntimeError("production Knowledge service is unavailable")
        _require_approval_scope(
            approved, knowledge.scope.enterprise_id, knowledge.scope.project_id
        )
        original_gateway = _require_single_http_attempt_gateway(
            runtime.quality_models.gateway
        )
        production_routes = _production_routes_from_gateway(original_gateway)
        _require_approved_production_routes(approved, production_routes)

        def path_factory(capture: CapturingModelGateway) -> RuntimeKnowledgePath:
            runtime.quality_models.gateway = capture
            return RuntimeKnowledgePath(runtime, dataset)

        observations = await run_dataset(
            dataset,
            gateway=original_gateway,
            path_factory=path_factory,
            timeout_ms=args.timeout_ms,
            provider_call_budget=args.provider_call_budget,
            cache={},
            max_retries_per_case=args.max_retries_per_case,
            approved_mapping=approved,
            production_routes=production_routes,
            retry_backoff=_bounded_retry_backoff,
        )
        report = _EVALUATOR.evaluate_run(
            dataset,
            observations,
            min_cases=100,
            require_real=True,
            approved_mapping=approved,
            provider_call_budget=args.provider_call_budget,
            max_retries_per_case=args.max_retries_per_case,
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
    parser.add_argument("--max-retries-per-case", type=int, default=2)
    return asyncio.run(_main_async(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
