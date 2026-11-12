"""Public workflow read models and lossless, safe event projection."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.workflows.application.browser_artifacts import BrowserArtifactAdapter
from tap.modules.workflows.domain.coding_diagnostics import sanitize_diagnostics
from tap.modules.workflows.domain.models import WorkflowEvent
from tap.modules.workflows.ports.browser import BrowserReadError


class SnapshotRequired(ValueError):
    """A replay cursor cannot be reconciled with the current task snapshot."""


def _public_time(value: datetime) -> datetime:
    """MySQL returns UTC DATETIME values without tzinfo; emit explicit UTC."""
    return value.replace(tzinfo=UTC) if value.utcoffset() is None else value.astimezone(UTC)


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
    "browser.tool_diagnostics": (),
    "task.created": ("status",),
    "task.claimed": ("status",),
    "task.route_decided": (),
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
    "node.outcome_unknown": ("node_id",),
    "node.canceled": ("node_id",),
    "node.tool_started": ("node_id", "attempt", "tool_id"),
    "node.tool_finished": ("node_id", "attempt", "tool_id", "outcome"),
    "node.skill_started": ("node_id", "attempt", "skill_id"),
    "node.skill_finished": ("node_id", "attempt", "skill_id", "outcome"),
    "command.received": ("command_id", "status"),
    "command.receipted": ("command_id",),
    "browser.recording_saved": ("evidence_id",),
}
_TEXT_FIELDS: dict[str, str] = {
    "node.finished": "summary",
    "node.failed": "reason",
    "task.failed": "reason",
    "task.awaiting_input": "question",
}


_BROWSER_META = (
    "node_id",
    "attempt",
    "action_id",
    "operation",
    "duration_ms",
    "outcome",
    "step_id",
    "step_keyword",
    "revision",
    "observation_sequence",
)
_BROWSER_TEXT = (
    "url",
    "selector",
    "input",
    "step_title",
    "expected_result",
    "title",
    "summary",
    "error",
    "reason",
)
for _kind in (
    "browser.plan_created",
    "browser.action_started",
    "browser.action_finished",
    "browser.script_failed",
):
    _PUBLIC_FIELDS[_kind] = _BROWSER_META


def _event_texts(event: WorkflowEvent) -> tuple[str, ...]:
    if event.kind == "browser.tool_diagnostics":
        return ()
    raw = event.payload
    fields = _BROWSER_TEXT if event.kind.startswith("browser.") else (_TEXT_FIELDS.get(event.kind),)
    values = [
        cast(str, raw[field])[:2000]
        for field in fields
        if field and isinstance(raw.get(field), str)
    ]
    if event.kind == "browser.plan_created" and isinstance(raw.get("steps"), list):
        for step in cast(list[Any], raw["steps"])[:24]:
            if isinstance(step, dict):
                values.extend(
                    step[key][:400]
                    for key in ("title", "expected_result")
                    if isinstance(step.get(key), str)
                )
    return tuple(dict.fromkeys(values))


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
    if event.kind == "browser.tool_diagnostics":
        payload = {
            "schema_version": 1,
            "diagnostics_available": raw.get("diagnostics_available") is True,
            "diagnostics": sanitize_diagnostics(raw.get("diagnostics")),
        }
    text_field = _TEXT_FIELDS.get(event.kind)
    if text_field is not None and redact is not None and type(raw.get(text_field)) is str:
        payload[text_field] = redact(cast(str, raw[text_field])[:2000])[:2000]
    if (
        event.kind.startswith("browser.")
        and event.kind != "browser.tool_diagnostics"
        and event.kind in _PUBLIC_FIELDS
        and redact is not None
    ):
        for field in _BROWSER_TEXT:
            if isinstance(raw.get(field), str):
                payload[field] = redact(cast(str, raw[field])[:2000])[:2000]
        if event.kind == "browser.plan_created" and isinstance(raw.get("steps"), list):
            payload["steps"] = [
                {
                    "id": step["id"],
                    "keyword": step["keyword"],
                    "title": redact(step["title"][:400])[:400],
                    "expected_result": redact(str(step.get("expected_result", ""))[:400])[:400],
                }
                for step in cast(list[Any], raw["steps"])[:24]
                if isinstance(step, dict)
                and isinstance(step.get("id"), str)
                and isinstance(step.get("title"), str)
                and step.get("keyword") in {"Given", "When", "Then", "And"}
            ]
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
        "node.outcome_unknown": "unknown",
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
        "occurredAt": _public_time(event.occurred_at).isoformat(),
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


def _actions(
    status: str,
    origin: str,
    nodes: tuple[dict[str, Any], ...] = (),
    *,
    kind: str = "native",
    replay_ready: bool = False,
) -> list[str]:
    if origin == "observed":
        return []
    if kind == "browser":
        if status in ("succeeded", "failed", "canceled", "skipped"):
            return ["replay"] if replay_ready else []
        return ["cancel"] if status == "running" else []
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
    native_controls_enabled: bool = True,
    replay_ready: bool = False,
) -> dict[str, Any]:
    request = row["request_json"]
    origin = _origin(request)
    kind = "browser" if request.get("kind") == "browser" else origin
    return {
        "taskId": row["task_id"],
        "projectId": row["project_id"],
        "conversationId": row["conversation_id"],
        "status": row["status"],
        "planRevision": row["plan_revision"],
        "lastSequence": row.get("last_sequence", row.get("next_sequence", 1) - 1),
        "origin": origin,
        "taskKind": kind,
        "replayOfTaskId": (request.get("rerun_from_task_id") if kind == "browser" else None),
        "availableActions": _actions(
            row["status"], origin, nodes, kind=kind, replay_ready=replay_ready
        )
        if native_controls_enabled
        else [],
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
            "occurredAt": _public_time(row["recent_event"]["occurred_at"]),
        },
        "createdAt": _public_time(row["created_at"]),
        "matchingRuns": [
            {
                "nodeId": node["node_id"],
                "agentId": node["role"],
                "agentRevisionId": node["agent_revision_id"],
                "attempt": node["attempt"],
                "status": node["status"],
                "createdAt": _public_time(node["created_at"]),
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
                "createdAt": _public_time(value["created_at"]),
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
                # The journal summary may include an evidence suffix beyond its
                # 400-character excerpt. Keep that full event, but honor the DTO.
                node["publicSummary"] = event["payload"]["summary"][:400]
    return nodes


class TextRedactor(Protocol):
    async def redact_text(self, text: str) -> str: ...


class WorkflowQueries:
    def __init__(
        self,
        store: WorkflowReadStore,
        *,
        redactor: TextRedactor | None = None,
        native_controls_enabled: bool = True,
        browser_artifacts: BrowserArtifactAdapter | None = None,
    ) -> None:
        self._store = store
        self._redactor = redactor
        self._native_controls_enabled = native_controls_enabled
        self._browser_artifacts = browser_artifacts

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
        texts = _event_texts(event)
        if not texts or self._redactor is None:
            return project_event(event, **metadata)
        try:
            safe = {text: await self._redactor.redact_text(text) for text in texts}
        except Exception:
            return project_event(event, **metadata)
        return project_event(event, redact=lambda text: safe.get(text, ""), **metadata)

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
            item = _task_view(row, nodes, agent_id, self._native_controls_enabled)
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
        request_json = value["request_json"]
        browser_intent = None
        browser_run = None
        browser_unavailable = False
        browser_input_request = None
        if request_json.get("kind") == "browser":
            browser = request_json.get("browser", {})
            if isinstance(browser, dict):
                data = browser.get("input_data", {})
                browser_intent = {
                    "goal": str(request_json.get("goal", "")),
                    "startUrl": str(browser.get("start_url", "")),
                    "expectedResult": str(browser.get("expected_result", "")),
                    "inputNotes": data.get("notes") if isinstance(data, dict) else None,
                    "acceptanceContract": data.get("acceptance_contract")
                    if isinstance(data, dict)
                    else None,
                }
            input_event = next(
                (
                    event
                    for event in reversed(public_events)
                    if event["kind"] == "task.awaiting_input"
                ),
                None,
            )
            raw_input = next(
                (
                    event.payload.get("input_request")
                    for event in reversed(value["events"])
                    if event.kind == "browser.coding_result"
                    and isinstance(event.payload.get("input_request"), dict)
                ),
                None,
            )
            if (
                input_event is not None
                and isinstance(raw_input, dict)
                and raw_input.get("reason") in {"missing_input", "external_block"}
            ):
                browser_input_request = {
                    "question": input_event["payload"].get(
                        "question", "Additional input is required."
                    ),
                    "reason": raw_input["reason"],
                }
            if self._browser_artifacts is not None:
                try:
                    browser_run = await self._browser_artifacts.project(
                        cast(ProjectScopeContext, scope), task_id, value
                    )
                except (LookupError, BrowserReadError, TimeoutError, ValueError, OSError):
                    browser_unavailable = True
            elif any(event.kind == "browser.coding_result" for event in value["events"]):
                browser_unavailable = True
        return {
            **_task_view(
                value,
                nodes,
                native_controls_enabled=self._native_controls_enabled,
                replay_ready=any(
                    event.kind == "browser.coding_result"
                    and isinstance(event.payload.get("bundle_ref"), str)
                    and isinstance(event.payload.get("execution_ref"), str)
                    for event in value["events"]
                ),
            ),
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
            "browserRun": browser_run,
            "browserRunUnavailable": browser_unavailable,
            "browserIntent": browser_intent,
            "browserInputRequest": browser_input_request,
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
