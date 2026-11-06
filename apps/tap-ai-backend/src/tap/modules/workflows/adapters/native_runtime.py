"""Fenced ModelGateway action journal for native workflow graph runs."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

from tap.modules.access.domain.policy import RetrievalPolicyContext
from tap.modules.ai.domain.models import (
    ModelCallAudit,
    ModelOperation,
    ModelRequest,
    ModelResult,
    ModelToolCall,
    ModelUsage,
    text_digest,
)
from tap.modules.ai.ports.gateway import ModelGateway
from tap.modules.knowledge.api import KnowledgeAPI
from tap.modules.workflows.adapters.mysql import MysqlWorkflowStore
from tap.modules.workflows.domain.commands import WorkflowDispatchBlocked, WorkflowLimitExceeded


def _remaining_seconds(request: dict[str, object]) -> float | None:
    deadline = request.get("deadline_at")
    if not isinstance(deadline, str):
        return None
    return (datetime.fromisoformat(deadline).astimezone(UTC) - datetime.now(UTC)).total_seconds()


class JournaledModelGateway:
    """Replay settled results before checkpoint replay can repeat a provider call.

    `action.settled.result` is private runtime replay data, never a public event
    projection. A new attempt reserves a fresh action only for an unsettled call.
    """

    def __init__(
        self,
        gateway: ModelGateway,
        store: MysqlWorkflowStore,
        task_id: str,
        token: str,
        node_id: str,
        attempt: int,
        authorize: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self._gateway = gateway
        self._store = store
        self._task_id = task_id
        self._token = token
        self._node_id = node_id
        self._attempt = attempt
        self._authorize = authorize

    @staticmethod
    def _action_id(request: ModelRequest, attempt: int) -> str:
        if request.image_bytes is not None:
            raise ValueError("native workflow model actions do not accept images")
        material = {
            "alias": request.alias,
            "operation": request.operation.value,
            "prompt_digest": request.prompt_digest,
            "context_digest": text_digest(request.context),
            "schema_digest": request.schema_digest,
            "tool_allowlist": sorted(request.tool_allowlist),
            "governance_digests": list(request.governance_digests),
            "tools": [asdict(tool) for tool in request.tools],
            "messages": [asdict(message) for message in request.messages],
            "max_output_tokens": request.max_output_tokens,
        }
        digest = hashlib.sha256(
            json.dumps(
                material,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        return hashlib.sha256(
            f"{request.idempotency_key}:input:{digest}:attempt:{attempt}".encode()
        ).hexdigest()

    @staticmethod
    def _serialize(result: ModelResult) -> dict[str, object]:
        return {
            "output": result.output,
            "actual_model": result.actual_model,
            "actual_provider": result.actual_provider,
            "usage": asdict(result.usage),
            "provider_request_id": result.provider_request_id,
            "gateway_call_id": result.gateway_call_id,
            "call_id": result.call_id,
            "cost_usd": str(result.cost_usd) if result.cost_usd is not None else None,
            "tool_calls": [asdict(call) for call in result.tool_calls],
        }

    @staticmethod
    def _restore(request: ModelRequest, data: dict[str, object]) -> ModelResult:
        raw_usage = data["usage"]
        assert isinstance(raw_usage, dict)
        usage = ModelUsage(raw_usage.get("input_tokens"), raw_usage.get("output_tokens"))
        provider = str(data["actual_provider"])
        model = str(data["actual_model"])
        audit = ModelCallAudit(
            scope=request.scope,
            alias=request.alias,
            operation=ModelOperation.CHAT,
            prompt_digest=request.prompt_digest,
            schema_digest=request.schema_digest,
            context_digest=text_digest(request.context),
            idempotency_key=request.idempotency_key,
            actual_provider=provider,
            actual_model=model,
            usage=usage,
            tool_allowlist=request.tool_allowlist,
            governance_digests=request.governance_digests,
        )
        calls = data.get("tool_calls") or []
        assert isinstance(calls, list)
        output = data["output"]
        if not isinstance(output, str):
            raise ValueError("saved native model output is invalid")
        for field in ("provider_request_id", "gateway_call_id", "call_id"):
            if data.get(field) is not None and not isinstance(data[field], str):
                raise ValueError("saved native model call identity is invalid")
        return ModelResult(
            output=output,
            actual_model=model,
            usage=usage,
            actual_provider=provider,
            audit=audit,
            provider_request_id=cast(str | None, data.get("provider_request_id")),
            gateway_call_id=cast(str | None, data.get("gateway_call_id")),
            call_id=cast(str | None, data.get("call_id")),
            cost_usd=Decimal(str(data["cost_usd"])) if data.get("cost_usd") is not None else None,
            tool_calls=tuple(ModelToolCall(**call) for call in calls),
        )

    async def _require_known_cost_within_limit(self, request: ModelRequest) -> None:
        snapshot = await self._store.snapshot(request.scope, self._task_id)
        limit = snapshot["budget_limit"].get("cost_micros")
        spent = snapshot["budget_used"].get("cost_micros", 0)
        if limit is not None and spent > limit:
            raise WorkflowLimitExceeded("workflow known cost budget exceeded")

    async def chat(self, request: ModelRequest) -> ModelResult:
        run_id, replay_floor = await self._store.graph_lineage(
            request.scope, self._task_id, self._node_id, self._attempt
        )
        prefix = f"{self._task_id}:{self._node_id}:graph:{run_id}:model:"
        if not request.idempotency_key.startswith(prefix):
            raise ValueError("model action identity does not match workflow graph lineage")
        for attempt in range(replay_floor, self._attempt + 1):
            previous = await self._store.action_result(
                request.scope, self._action_id(request, attempt)
            )
            if previous is not None:
                if "skipped" in previous:
                    raise WorkflowDispatchBlocked("model action settled without dispatch")
                await self._require_known_cost_within_limit(request)
                return self._restore(request, previous)
        if self._authorize is not None and not await self._authorize():
            raise PermissionError("workflow conversation dispatch is no longer authorized")
        remaining = _remaining_seconds(await self._store.task_request(request.scope, self._task_id))
        if remaining is not None and remaining <= 0:
            raise WorkflowLimitExceeded("workflow deadline exceeded")
        action_id = self._action_id(request, self._attempt)
        status = await self._store.reserve_action(
            request.scope,
            self._task_id,
            self._token,
            action_id,
            "model",
            node_id=self._node_id,
            attempt=self._attempt,
        )
        if status == "existing":
            raise RuntimeError("unsettled model action requires recovery inspection")
        if self._authorize is not None and not await self._authorize():
            await self._store.settle_action(
                request.scope,
                self._task_id,
                self._token,
                action_id,
                {"skipped": "conversation dispatch authorization revoked"},
                node_id=self._node_id,
                attempt=self._attempt,
            )
            raise PermissionError("workflow conversation dispatch is no longer authorized")
        remaining = _remaining_seconds(await self._store.task_request(request.scope, self._task_id))
        if remaining is not None and remaining <= 0:
            await self._store.settle_action(
                request.scope,
                self._task_id,
                self._token,
                action_id,
                {"skipped": "deadline exceeded before dispatch"},
                node_id=self._node_id,
                attempt=self._attempt,
            )
            raise WorkflowLimitExceeded("workflow deadline exceeded")
        timeout = (
            min(request.timeout_seconds, remaining)
            if remaining is not None
            else request.timeout_seconds
        )
        # Keep the full graph lineage key for the private action journal and
        # checkpoint replay. The provider gateway accepts at most 128 chars.
        provider_key = "workflow:" + hashlib.sha256(request.idempotency_key.encode()).hexdigest()
        bounded = replace(
            request,
            timeout_seconds=timeout,
            allow_retries=False,
            idempotency_key=provider_key,
        )
        response = await asyncio.wait_for(self._gateway.chat(bounded), timeout=timeout)
        data = await self._store.settle_action(
            request.scope,
            self._task_id,
            self._token,
            action_id,
            self._serialize(response),
            node_id=self._node_id,
            attempt=self._attempt,
        )
        await self._require_known_cost_within_limit(request)
        return self._restore(request, data)


class JournaledKnowledgeSearch:
    """One authorized public search, with conservative shared embedding reservation.

    The reservation is an upper-bound accounting unit, not proof that the
    Knowledge implementation actually invoked an embedding provider.
    """

    def __init__(
        self,
        store: MysqlWorkflowStore,
        knowledge: KnowledgeAPI,
        policy_for: Callable[[tuple[dict[str, object], ...]], Awaitable[RetrievalPolicyContext]],
        *,
        on_actual_step: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self._store = store
        self._knowledge = knowledge
        self._policy_for = policy_for
        self._on_actual_step = on_actual_step

    @staticmethod
    def _action_id(
        task_id: str,
        node_id: str,
        call_id: str,
        query: str,
        sources: tuple[dict[str, object], ...],
        attempt: int,
    ) -> str:
        material = json.dumps(
            (task_id, node_id, call_id, query, sources),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(f"knowledge.search:{material}:attempt:{attempt}".encode()).hexdigest()

    async def execute(
        self,
        scope,
        task_id: str,
        token: str,
        node_id: str,
        attempt: int,
        name: str,
        arguments: dict[str, object],
        call_id: str,
        authorize: Callable[[], Awaitable[bool]],
    ) -> str:
        from tap.modules.knowledge.domain.models import (
            AnswerMode,
            ResourceMode,
            ResourceRef,
            SearchRequest,
            SourceFamily,
        )

        if name not in ("knowledge.search", "knowledge_search"):
            raise ValueError("native ordinary tool is unavailable")
        query = arguments.get("query")
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
            raise ValueError("knowledge search query is invalid")
        request = await self._store.task_request(scope, task_id)
        raw_sources = request.get("resolved_resources") or []
        if not isinstance(raw_sources, list) or len(raw_sources) > 20:
            raise ValueError("frozen knowledge selection is invalid")
        allowed = {str(item["source_id"]): item for item in raw_sources if isinstance(item, dict)}
        requested = arguments.get("sourceIds")
        if requested is not None and (
            not isinstance(requested, list)
            or len(requested) > 20
            or any(not isinstance(item, str) or item not in allowed for item in requested)
        ):
            raise ValueError("knowledge source is outside frozen selection")
        selected = tuple(
            allowed[item] for item in (requested if requested is not None else allowed)
        )
        if not selected:
            return "未选择可检索的知识来源。"
        if not await authorize():
            raise PermissionError("workflow knowledge search is no longer authorized")
        _, replay_floor = await self._store.graph_lineage(scope, task_id, node_id, attempt)
        for prior in range(replay_floor, attempt + 1):
            saved = await self._store.action_result(
                scope, self._action_id(task_id, node_id, call_id, query, selected, prior)
            )
            if saved is not None:
                if "skipped" in saved:
                    raise WorkflowDispatchBlocked("knowledge action settled without dispatch")
                if self._on_actual_step is not None:
                    # The action result is durable before its public outcome event.
                    # Replay repairs that event for the original execution attempt;
                    # the event ID makes repeated repair harmless.
                    await self._on_actual_step(
                        scope,
                        task_id,
                        token,
                        node_id,
                        prior,
                        "node.tool_finished",
                        call_id,
                        "knowledge.search",
                        "succeeded",
                    )
                return str(saved["summary"])
        remaining = _remaining_seconds(request)
        if remaining is not None and remaining <= 0:
            raise WorkflowLimitExceeded("workflow deadline exceeded")
        policy = await self._policy_for(selected)
        search_request = SearchRequest(
            query=query,
            answer_mode=AnswerMode.QUICK,
            source_families=(SourceFamily.DOC,),
            resource_refs=tuple(
                ResourceRef(SourceFamily.DOC, str(item["source_id"]), ResourceMode.SCOPE)
                for item in selected
            ),
        )
        action_id = self._action_id(task_id, node_id, call_id, query, selected, attempt)
        status = await self._store.reserve_action(
            scope,
            task_id,
            token,
            action_id,
            "model",
            node_id=node_id,
            attempt=attempt,
        )
        if status == "existing":
            raise RuntimeError("unsettled knowledge search requires recovery inspection")
        if not await authorize():
            await self._store.settle_action(
                scope,
                task_id,
                token,
                action_id,
                {"skipped": "conversation dispatch authorization revoked"},
                node_id=node_id,
                attempt=attempt,
            )
            raise PermissionError("workflow knowledge search is no longer authorized")
        remaining = _remaining_seconds(request)
        if remaining is not None and remaining <= 0:
            await self._store.settle_action(
                scope,
                task_id,
                token,
                action_id,
                {"skipped": "deadline exceeded before dispatch"},
                node_id=node_id,
                attempt=attempt,
            )
            raise WorkflowLimitExceeded("workflow deadline exceeded")
        if self._on_actual_step is not None:
            await self._on_actual_step(
                scope,
                task_id,
                token,
                node_id,
                attempt,
                "node.tool_started",
                call_id,
                "knowledge.search",
                None,
            )
        try:
            search = self._knowledge.search(search_request, policy)
            response = (
                await asyncio.wait_for(search, timeout=remaining)
                if remaining is not None
                else await search
            )
            evidence = tuple(response.evidence[:10])
            summary = json.dumps(
                {
                    "evidence": [
                        {"citationId": str(item.citation_id), "excerpt": str(item.content)[:500]}
                        for item in evidence
                    ]
                },
                ensure_ascii=False,
            )
            saved = await self._store.settle_action(
                scope,
                task_id,
                token,
                action_id,
                {"summary": summary, "private_replay": True, "evidence_count": len(evidence)},
                node_id=node_id,
                attempt=attempt,
            )
        except Exception:
            if self._on_actual_step is not None:
                await self._on_actual_step(
                    scope,
                    task_id,
                    token,
                    node_id,
                    attempt,
                    "node.tool_finished",
                    call_id,
                    "knowledge.search",
                    "unknown",
                )
            raise
        if self._on_actual_step is not None:
            await self._on_actual_step(
                scope,
                task_id,
                token,
                node_id,
                attempt,
                "node.tool_finished",
                call_id,
                "knowledge.search",
                "succeeded",
            )
        return str(saved["summary"])
