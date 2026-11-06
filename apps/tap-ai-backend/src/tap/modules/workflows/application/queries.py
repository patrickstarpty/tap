"""Public workflow read models and lossless, safe event projection."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol, cast

from tap.modules.workflows.domain.models import WorkflowEvent


class SnapshotRequired(ValueError):
    """A replay cursor cannot be reconciled with the current task snapshot."""


def replay_from(*, snapshot_sequence: int, client_sequence: int | None) -> int:
    if type(snapshot_sequence) is not int or snapshot_sequence < 0:
        raise ValueError("invalid snapshot sequence")
    if client_sequence is None:
        return snapshot_sequence
    if type(client_sequence) is not int or client_sequence < 0:
        raise ValueError("invalid client sequence")
    if client_sequence > snapshot_sequence:
        raise SnapshotRequired("future workflow cursor requires a new snapshot")
    return client_sequence


_PUBLIC_FIELDS: dict[str, tuple[str, ...]] = {
    "task.created": ("status",),
    "task.claimed": ("status",),
    "task.status": ("status",),
    "task.yielded": ("status",),
    "task.completed": ("outcome",),
    "task.canceled": ("status",),
    "task.failed": ("status",),
    "task.dispatch_blocked": ("reason",),
    "plan.dependency_added": ("from_node_id", "to_node_id", "plan_revision"),
    "node.created": ("node_id", "role"),
    "node.delegated": ("node_id", "parent_node_id", "agent_id", "depends_on"),
    "node.started": ("node_id", "attempt"),
    "node.waiting": ("node_id", "reason"),
    "node.unblocked": ("node_ids",),
    "node.finished": ("node_id",),
    "node.failed": ("node_id",),
    "node.canceled": ("node_id",),
    "command.received": ("command_id", "status"),
    "command.receipted": ("command_id",),
}
_TEXT_FIELDS: dict[str, str] = {
    "node.finished": "summary",
    "task.awaiting_input": "question",
}


def _public_value(value: object) -> object | None:
    if value is None or type(value) in (str, int, bool):
        return value
    if isinstance(value, list) and len(value) <= 50 and all(type(item) is str for item in value):
        return list(value)
    return None


def project_event(
    event: WorkflowEvent,
    *,
    redact: Callable[[str], str] | None = None,
    project_id: str | None = None,
    source_runtime: str | None = None,
    parent_node_id: str | None = None,
) -> dict[str, Any]:
    """Emit one envelope per committed sequence, never arbitrary journal data."""
    raw = event.payload if isinstance(event.payload, Mapping) else {}
    payload: dict[str, object] = {}
    for field in _PUBLIC_FIELDS.get(event.kind, ()):
        if field in raw:
            value = _public_value(raw[field])
            if value is not None:
                payload[field] = value
    text_field = _TEXT_FIELDS.get(event.kind)
    if text_field is not None and redact is not None and type(raw.get(text_field)) is str:
        payload[text_field] = redact(cast(str, raw[text_field])[:400])[:400]
    if event.kind == "command.receipted" and isinstance(raw.get("receipt"), Mapping):
        receipt = cast(Mapping[str, object], raw["receipt"])
        payload["receipt"] = {
            key: receipt[key]
            for key in ("status", "reason", "applied_plan_revision")
            if key in receipt and type(receipt[key]) in (str, int)
        }
    status_by_kind = {
        "task.created": "queued",
        "task.completed": "succeeded",
        "task.failed": "failed",
        "task.canceled": "canceled",
        "task.awaiting_input": "awaiting_input",
        "node.started": "running",
        "node.finished": "succeeded",
        "node.failed": "failed",
        "node.canceled": "canceled",
        "node.waiting": "waiting_dependency",
    }
    status = status_by_kind.get(event.kind)
    if status is None and event.kind in ("task.status", "task.yielded"):
        status = cast(str, raw["status"]) if type(raw.get("status")) is str else None
    return {
        "eventId": event.event_id,
        "taskId": event.task_id,
        "sequence": event.sequence,
        "nodeId": event.node_id,
        "attempt": event.attempt,
        "kind": event.kind
        if event.kind in _PUBLIC_FIELDS or event.kind in _TEXT_FIELDS
        else "activity",
        "payload": payload,
        "occurredAt": event.occurred_at.isoformat(),
        "projectId": project_id,
        "sourceRuntime": source_runtime,
        "parentNodeId": parent_node_id,
        "status": status,
    }


class WorkflowReadStore(Protocol):
    """Structural read port; SQL and private request storage stay in adapters."""

    async def snapshot(self, scope: object, task_id: str) -> dict[str, Any]:
        raise NotImplementedError

    async def list_tasks(
        self, scope: object, limit: int, cursor: str | None, agent_id: str | None = None
    ) -> tuple[tuple[dict[str, Any], ...], str | None]:
        raise NotImplementedError

    async def events_after(
        self, scope: object, task_id: str, sequence: int, limit: int
    ) -> tuple[WorkflowEvent, ...]:
        raise NotImplementedError

    async def node_logs(
        self,
        scope: object,
        task_id: str,
        node_id: str,
        attempt: int,
        sequence: int,
        limit: int,
    ) -> tuple[tuple[WorkflowEvent, ...], int | None]:
        raise NotImplementedError


def _origin(request: Mapping[str, Any]) -> str:
    return "observed" if request.get("origin") == "observed" else "native"


def _actions(status: str, origin: str, nodes: tuple[dict[str, Any], ...] = ()) -> list[str]:
    if origin == "observed":
        return []
    if status in ("succeeded", "failed", "canceled", "skipped"):
        return []
    if status == "paused":
        return ["resume", "cancel"]
    if status in ("pausing", "canceling", "recovering"):
        return []
    values = ["steer", "pause", "cancel"]
    if any(node.get("status") == "paused" for node in nodes):
        values.append("resume")
    if any(node.get("status") == "failed" for node in nodes):
        values.append("retry")
    return values


def _task_view(
    row: Mapping[str, Any],
    nodes: tuple[dict[str, Any], ...] = (),
    matching_agent_id: str | None = None,
) -> dict[str, Any]:
    request = row["request_json"]
    origin = _origin(request)
    return {
        "taskId": row["task_id"],
        "projectId": row["project_id"],
        "conversationId": row["conversation_id"],
        "status": row["status"],
        "planRevision": row["plan_revision"],
        "lastSequence": row.get("last_sequence", row.get("next_sequence", 1) - 1),
        "origin": origin,
        "availableActions": _actions(row["status"], origin, nodes),
        "displayName": "Tapper"
        if origin == "native"
        else str(request.get("display_name") or "Agent"),
        "goalSummary": "",
        "recentEvent": None
        if row.get("recent_event") is None
        else {
            "kind": row["recent_event"]["kind"]
            if row["recent_event"]["kind"] in _PUBLIC_FIELDS
            or row["recent_event"]["kind"] in _TEXT_FIELDS
            else "activity",
            "occurredAt": row["recent_event"]["occurred_at"],
        },
        "createdAt": row["created_at"],
        "matchingRuns": [
            {
                "nodeId": node["node_id"],
                "agentId": node["role"],
                "agentRevisionId": node["agent_revision_id"],
                "attempt": node["attempt"],
                "status": node["status"],
                "createdAt": node["created_at"],
            }
            for node in nodes
            if node.get("kind") == "agent"
            and node.get("role") != "root"
            and (matching_agent_id is None or node.get("role") == matching_agent_id)
        ],
    }


def _artifact(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "artifactId": item["artifact_id"],
        "nodeId": item["node_id"],
        "attempt": item["attempt"],
        "contentDigest": item["content_digest"],
        "versionRef": item["version_ref"],
    }


def _node(
    item: Mapping[str, Any], request: Mapping[str, Any], attempts: tuple[dict[str, Any], ...]
) -> dict[str, Any]:
    role = item["role"]
    frozen = (
        request.get("root")
        if role == "root"
        else next(
            (agent for agent in request.get("candidates", ()) if agent.get("agent_id") == role),
            None,
        )
    ) or {}
    allowed_refs = {
        str(ref["source_id"]): ref
        for ref in request.get("resolved_resources", ())
        if isinstance(ref, dict) and ref.get("source_id") and ref.get("revision_id")
    }
    delegated = next(
        (
            event
            for event in request.get("_events", ())
            if event.kind == "node.delegated" and event.node_id == item["node_id"]
        ),
        None,
    )
    chosen = (
        tuple(allowed_refs)
        if role == "root"
        else tuple(delegated.payload.get("input_refs", ()))
        if delegated is not None
        else ()
    )
    return {
        "nodeId": item["node_id"],
        "parentNodeId": item["parent_node_id"],
        "agentId": frozen.get("agent_id")
        or (role if item["kind"] == "agent" and role != "root" else None),
        "agentRevisionId": item["agent_revision_id"],
        "displayName": "Tapper" if role == "root" else str(frozen.get("name") or role),
        "kind": item["kind"],
        "status": item["status"],
        "attempt": item["attempt"],
        "planRevision": item["plan_revision"],
        "sourceRuntime": item["runtime"],
        "inputRefs": [
            {
                "sourceId": ref["source_id"],
                "revisionId": ref["revision_id"],
                "sourceContentHash": ref["source_content_hash"],
            }
            for key in chosen
            if (ref := allowed_refs.get(key)) is not None
        ],
        "attempts": [
            {
                "attempt": value["attempt"],
                "status": value["status"],
                "createdAt": value["created_at"],
            }
            for value in attempts
            if value["node_id"] == item["node_id"]
        ],
    }


def attach_node_summaries(
    nodes: list[dict[str, Any]], events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Use only the matching attempt's already-redacted completion summary."""
    for node in nodes:
        node["publicSummary"] = None
        for event in events:
            if (
                event["kind"] == "node.finished"
                and event["nodeId"] == node["nodeId"]
                and event["attempt"] == node["attempt"]
                and type(event["payload"].get("summary")) is str
            ):
                node["publicSummary"] = event["payload"]["summary"]
    return nodes


class TextRedactor(Protocol):
    async def redact_text(self, text: str) -> str: ...


class WorkflowQueries:
    def __init__(self, store: WorkflowReadStore, *, redactor: TextRedactor | None = None) -> None:
        self._store = store
        self._redactor = redactor

    async def _public_event(
        self, event: WorkflowEvent, *, snapshot: Mapping[str, Any]
    ) -> dict[str, Any]:
        node = next((item for item in snapshot["nodes"] if item["node_id"] == event.node_id), None)
        metadata = {
            "project_id": snapshot["project_id"],
            "source_runtime": node["runtime"]
            if node is not None
            else _origin(snapshot["request_json"]),
            "parent_node_id": node["parent_node_id"] if node is not None else None,
        }
        field = _TEXT_FIELDS.get(event.kind)
        if field is None or type(event.payload.get(field)) is not str or self._redactor is None:
            return project_event(event, **metadata)
        try:
            safe = await self._redactor.redact_text(cast(str, event.payload[field])[:400])
        except Exception:
            return project_event(event, **metadata)
        return project_event(event, redact=lambda _text: safe, **metadata)

    async def _goal_summary(self, raw: object) -> str:
        if type(raw) is not str or self._redactor is None:
            return ""
        try:
            return (await self._redactor.redact_text(cast(str, raw)[:200]))[:200]
        except Exception:
            return ""

    async def list(
        self, scope: object, limit: int, cursor: str | None, agent_id: str | None = None
    ) -> dict[str, Any]:
        rows, next_cursor = await self._store.list_tasks(scope, limit, cursor, agent_id)
        items = []
        for row in rows:
            nodes = tuple(row.get("nodes", ()))
            item = _task_view(row, nodes, agent_id)
            item["goalSummary"] = await self._goal_summary(row["request_json"].get("goal"))
            items.append(item)
        return {"items": items, "nextCursor": next_cursor}

    async def snapshot(self, scope: object, task_id: str) -> dict[str, Any]:
        value = await self._store.snapshot(scope, task_id)
        request = {**value["request_json"], "_events": value["events"]}
        nodes = value["nodes"]
        attempts = value["attempts"]
        public_events = [
            await self._public_event(event, snapshot=value) for event in value["events"]
        ]
        return {
            **_task_view(value, nodes),
            "goalSummary": await self._goal_summary(value["request_json"].get("goal")),
            "nodes": attach_node_summaries(
                [_node(item, request, attempts) for item in nodes], public_events
            ),
            "edges": [
                {
                    "fromNodeId": item["from_node_id"],
                    "toNodeId": item["to_node_id"],
                    "planRevision": item["plan_revision"],
                }
                for item in value["edges"]
            ],
            "commands": [
                {
                    "commandId": item["command_id"],
                    "kind": item["kind"],
                    "targetNodeId": item["target_node_id"],
                    "expectedPlanRevision": item["expected_plan_revision"],
                    "status": item["receipt"]["status"],
                    "reason": item["receipt"].get("reason"),
                    "appliedPlanRevision": item["receipt"].get("applied_plan_revision"),
                }
                for item in value["commands"]
            ],
            "artifacts": [_artifact(item) for item in value["artifacts"]],
            "events": public_events,
            "budgetLimit": value["budget_limit"],
            "budgetUsed": value["budget_used"],
        }

    async def node(self, scope: object, task_id: str, node_id: str) -> dict[str, Any]:
        value = await self._store.snapshot(scope, task_id)
        node = next((item for item in value["nodes"] if item["node_id"] == node_id), None)
        if node is None:
            raise LookupError("workflow node not found")
        public_events = [
            await self._public_event(event, snapshot=value) for event in value["events"]
        ]
        details = attach_node_summaries(
            [_node(node, {**value["request_json"], "_events": value["events"]}, value["attempts"])],
            public_events,
        )[0]
        return {
            **details,
            "artifacts": [
                _artifact(item) for item in value["artifacts"] if item["node_id"] == node_id
            ],
        }

    async def logs(
        self,
        scope: object,
        task_id: str,
        node_id: str,
        attempt: int,
        sequence: int,
        limit: int,
    ) -> dict[str, Any]:
        snapshot = await self._store.snapshot(scope, task_id)
        if not any(node["node_id"] == node_id for node in snapshot["nodes"]):
            raise LookupError("workflow node not found")
        events, next_cursor = await self._store.node_logs(
            scope, task_id, node_id, attempt, sequence, limit
        )
        return {
            "items": [await self._public_event(event, snapshot=snapshot) for event in events],
            "nextCursor": next_cursor,
        }

    async def events(
        self, scope: object, task_id: str, sequence: int, limit: int = 100
    ) -> tuple[dict[str, Any], ...]:
        snapshot = await self._store.snapshot(scope, task_id)
        if sequence > snapshot["last_sequence"]:
            raise SnapshotRequired("future workflow cursor requires a new snapshot")
        fetched = await self._store.events_after(scope, task_id, sequence, limit)
        # Metadata and events are separate reads. A new node can commit between
        # them; defer its event until the next snapshot includes that node.
        events = tuple(event for event in fetched if event.sequence <= snapshot["last_sequence"])
        if sequence < snapshot["last_sequence"] and (
            not events or events[0].sequence != sequence + 1
        ):
            raise SnapshotRequired("workflow cursor expired; snapshot_required")
        return tuple([await self._public_event(event, snapshot=snapshot) for event in events])
