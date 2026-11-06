"""HTTP input conversion for server-owned workflow admission and controls."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import cast

from tap.contracts.workflows import WorkflowCommandRequest, WorkflowCreateRequest
from tap.interfaces.http.conversation_selection import freeze_conversation_selection
from tap.interfaces.http.dependencies import HttpServices
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.models import ModelCapability, ModelNotSelectable
from tap.modules.knowledge.ports.errors import KnowledgeRuntimeUnavailable
from tap.modules.workflows.adapters.asset_catalog import AssetWorkflowCatalog
from tap.modules.workflows.application.admission import WorkflowAdmissionCoordinator
from tap.modules.workflows.application.queries import WorkflowQueries
from tap.modules.workflows.domain.commands import ControlCommand
from tap.modules.workflows.ports.runtime import WorkflowWakeups
from tap.modules.workflows.ports.store import WorkflowStore


class WorkflowControlUnavailable(ValueError):
    """An observed task has no native workflow controls."""


class WorkflowHttpService:
    def __init__(
        self,
        services: HttpServices,
        store: WorkflowStore,
        admission: WorkflowAdmissionCoordinator,
        queries: WorkflowQueries,
    ) -> None:
        self._services = services
        self._store = store
        self._admission = admission
        self.queries = queries

    async def create(
        self, scope: ProjectScopeContext, body: WorkflowCreateRequest, key: str
    ) -> dict[str, object]:
        public_request = body.model_dump(mode="json", by_alias=True)
        existing = await self._admission.existing_standalone(scope, public_request, key)
        if existing is not None:
            return await self.queries.snapshot(scope, existing.task_id)
        if self._services.asset_catalog is None or self._services.model_catalog is None:
            raise KnowledgeRuntimeUnavailable
        selectable = {
            item.alias
            for item in await self._services.model_catalog.list_models(scope)
            if ModelCapability.STRUCTURED in item.capabilities
        }
        if body.model_alias not in selectable:
            raise ModelNotSelectable
        catalog = await AssetWorkflowCatalog(self._services.asset_catalog).freeze_agents(
            scope, tuple(body.requested_agent_ids)
        )
        if self._services.knowledge is None:
            raise KnowledgeRuntimeUnavailable
        selection = await freeze_conversation_selection(
            self._services.knowledge,
            source_revision_ids=tuple(body.source_revision_ids),
            document_revision_ids=tuple(body.document_revision_ids),
        )
        digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    public_request, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                ).encode()
            ).hexdigest()
        )
        frozen: dict[str, object] = {
            "origin": "native",
            "goal": body.goal,
            "model_alias": body.model_alias,
            "root": asdict(catalog.root),
            "candidates": [asdict(item) for item in catalog.candidates],
            "requested_ids": list(catalog.requested_ids),
            "resolved_resources": [asdict(item) for item in selection.resolved_resources],
            "input_digest": digest,
            "public_request": public_request,
            "skill_revision_ids": [],
            "skill_instructions": [],
            "skill_instruction_digests": [],
            "skill_catalog": [],
        }
        task = await self._admission.create_standalone(scope, frozen, key)
        return await self.queries.snapshot(scope, task.task_id)

    async def command(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        body: WorkflowCommandRequest,
        key: str,
    ) -> dict[str, object]:
        snapshot = await self.queries.snapshot(scope, task_id)
        if snapshot["origin"] == "observed":
            raise WorkflowControlUnavailable("observed task has no native controls")
        material = json.dumps(
            (scope.enterprise_id, scope.project_id, task_id, key), separators=(",", ":")
        )
        command_id = hashlib.sha256(material.encode()).hexdigest()
        existing = await self._store.command(scope, task_id, command_id)
        if existing is not None:
            if (
                existing.kind != body.kind
                or existing.target_node_id != body.target_node_id
                or existing.expected_plan_revision != body.expected_plan_revision
                or existing.text != body.text
            ):
                raise ValueError("command id payload conflict")
            command = existing
        else:
            command = ControlCommand(
                command_id,
                task_id,
                body.target_node_id,
                body.kind,
                body.expected_plan_revision,
                body.text,
            )
        receipt = await self._store.submit_command(scope, command)
        if receipt["status"] == "stale":
            raise ValueError("stale plan revision")
        wakeups = self._services.workflow_events if self._services is not None else None
        if wakeups is not None and receipt["status"] in ("accepted", "pending"):
            committed = await self._store.snapshot(scope, task_id)
            await cast(WorkflowWakeups, wakeups).notify(scope, task_id, committed["last_sequence"])
        return {
            "commandId": command_id,
            "kind": command.kind,
            "targetNodeId": command.target_node_id,
            "expectedPlanRevision": command.expected_plan_revision,
            "status": receipt["status"],
            "reason": receipt.get("reason"),
            "appliedPlanRevision": receipt.get("applied_plan_revision"),
        }
