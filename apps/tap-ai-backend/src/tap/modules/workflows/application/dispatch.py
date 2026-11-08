"""One fenced task epoch supervises independent native graph contexts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from socket import gethostname
from typing import Any
from urllib.parse import urlsplit

from langgraph.checkpoint.base import BaseCheckpointSaver

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.domain.skill_activation import SkillCatalogEntry, catalog_entry
from tap.modules.chat.application.workflow_bridge import WorkflowChatPort
from tap.modules.chat.domain.conversations import FrozenBrowserIntent
from tap.modules.workflows.adapters.opencode_runtime import CodingRuntime
from tap.modules.workflows.application.native_graph import NativeChatGateway, NativeWorkflowGraph
from tap.modules.workflows.domain.browser_intent import effective_request
from tap.modules.workflows.domain.commands import WorkflowDispatchBlocked, WorkflowLimitExceeded
from tap.modules.workflows.domain.delegation import DelegationRequest, child_result_summary
from tap.modules.workflows.ports.runtime import (
    NativeStatePort,
    RecoveryEpisode,
    RuntimeInspection,
    WorkflowWakeups,
)
from tap.modules.workflows.ports.store import WorkflowStore

CheckpointerFactory = Callable[[ProjectScopeContext, str, str], BaseCheckpointSaver]
GatewayFactory = Callable[
    [ProjectScopeContext, str, str, str, int, Callable[[], Awaitable[bool]]], NativeChatGateway
]
OrdinaryTool = Callable[
    [
        ProjectScopeContext,
        str,
        str,
        str,
        int,
        str,
        dict[str, object],
        str,
        Callable[[], Awaitable[bool]],
    ],
    Awaitable[str],
]
SkillLoader = Callable[[ProjectScopeContext, str], Awaitable[str]]
StandaloneAuthorize = Callable[[ProjectScopeContext, str], Awaitable[bool]]

_CODING_FAILURE_SUMMARIES = {
    "assistant_error": "OpenCode assistant reported an error.",
    "missing_frozen_input": "Frozen browser inputs are unavailable in the coding container.",
    "permission_denied": "OpenCode tool was denied access.",
    "tool_timeout": "OpenCode tool timed out.",
    "tool_error": "OpenCode tool reported an error.",
    "unknown": "OpenCode failed without classified error.",
}


def _safe_coding_failure(receipt: dict[str, Any]) -> dict[str, str | None] | None:
    terminal = receipt.get("model_terminal")
    if not isinstance(terminal, dict) or terminal.get("terminal") != "failed":
        return None
    raw = terminal.get("failure")
    category = raw.get("category") if isinstance(raw, dict) else None
    if not isinstance(category, str) or category not in _CODING_FAILURE_SUMMARIES:
        category = "unknown"
    tool = raw.get("tool") if isinstance(raw, dict) else None
    if (
        category in {"unknown", "assistant_error"}
        or not isinstance(tool, str)
        or not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", tool)
    ):
        tool = None
    return {
        "category": category,
        "tool": tool,
        "summary": _CODING_FAILURE_SUMMARIES[category],
    }


class WorkflowStopped(Exception):
    """A persisted control fenced further external work at a graph boundary."""


class NativeWorkflowRuntime:
    def __init__(
        self,
        store: WorkflowStore,
        state: NativeStatePort,
        gateway: object,
        *,
        checkpointer_factory: CheckpointerFactory,
        action_gateway_factory: GatewayFactory,
        max_parallel_agents: int = 3,
        model_timeout_seconds: float = 15.0,
        owner: str,
        chat: WorkflowChatPort | None = None,
        ordinary_tool: OrdinaryTool | None = None,
        projection: WorkflowProjection | None = None,
        skill_loader: SkillLoader | None = None,
        wakeups: WorkflowWakeups | None = None,
        standalone_authorize: StandaloneAuthorize | None = None,
        coding: CodingRuntime | None = None,
    ) -> None:
        if not 1 <= max_parallel_agents <= 20:
            raise ValueError("native parallel agent limit is outside the bound")
        self._store = store
        self._state = state
        self._gateway = gateway
        self._checkpointer_factory = checkpointer_factory
        self._action_gateway_factory = action_gateway_factory
        self._max_parallel_agents = max_parallel_agents
        self._model_timeout_seconds = model_timeout_seconds
        self._owner = owner
        self._chat = chat
        self._ordinary_tool = ordinary_tool
        self._projection = projection
        self._skill_loader = skill_loader
        self._wakeups = wakeups
        self._standalone_authorize = standalone_authorize
        self._coding = coding
        self._control_lock = asyncio.Lock()

    async def inspect(
        self, scope: ProjectScopeContext, episode: RecoveryEpisode
    ) -> RuntimeInspection:
        facts = await self._store.episode_facts(scope, episode)
        owner = facts["owner"]
        stopped = False
        if isinstance(owner, str):
            parts = owner.split(":")
            if len(parts) == 4 and parts[0] == "workflow" and parts[1] == gethostname():
                try:
                    pid = int(parts[2])
                    if pid > 0:
                        os.kill(pid, 0)
                except ProcessLookupError:
                    stopped = True
                except (PermissionError, ValueError, OverflowError):
                    pass
        # Process termination is only execution-stop evidence. Unsettled external
        # actions remain unknown even after that process has exited.
        return RuntimeInspection(
            "stopped" if stopped else "unknown",
            bool(facts["checkpoint_compatible"]),
            "unknown" if facts["unsettled"] else "clear",
            execution_token=episode.execution_token,
        )

    async def start(self, scope: ProjectScopeContext, task_id: str) -> None:
        if (await self._store.task_request(scope, task_id)).get("origin", "native") != "native":
            raise ValueError("observed workflow task has no native runtime")
        if await self._store.expire_idle_task(scope, task_id):
            await self._project_task(scope, task_id)
            return
        control_token = await self._store.claim_control(scope, task_id, self._owner)
        if control_token is not None:
            await self._apply_controls(scope, task_id, control_token)
            await self._store.release_control(scope, task_id, control_token)
        token = await self._store.claim_task(scope, task_id, self._owner)
        if token is not None:
            await self.advance(scope, task_id, token)
        await self._project_task(scope, task_id)
        if self._wakeups is not None:
            snapshot = await self._store.snapshot(scope, task_id)
            await self._wakeups.notify(scope, task_id, int(snapshot["last_sequence"]))

    async def _apply_controls(
        self, scope: ProjectScopeContext, task_id: str, token: str, current_node: str | None = None
    ) -> bool:
        async with self._control_lock:
            if not await self._store.active_lease(scope, task_id, token):
                # An idle control claim has a non-running status; it is valid
                # solely when a pending command exists.
                if current_node is not None:
                    return False
            for command in await self._store.pending_commands(scope, task_id):
                if command.kind in ("cancel", "pause"):
                    if not await self._store.stop_evidence(
                        scope,
                        task_id,
                        token,
                        command.target_node_id,
                    ):
                        if command.target_node_id in (None, current_node):
                            return False
                        continue
                    receipt: dict[str, object] = {
                        "status": "applied",
                        "stopped": True,
                        "checkpoint_compatible": True,
                    }
                elif command.kind == "retry":
                    request = await self._store.task_request(scope, task_id)
                    snapshot = await self._store.snapshot(scope, task_id)
                    node = next(
                        (
                            item
                            for item in snapshot["nodes"]
                            if item["node_id"] == command.target_node_id
                        ),
                        None,
                    )
                    if (
                        node is not None
                        and int(node["attempt"]) < int(request.get("max_attempts", 3))
                        and await self._store.retry_evidence(
                            scope, task_id, str(command.target_node_id)
                        )
                    ):
                        receipt = {
                            "status": "applied",
                            "side_effects": "clear",
                            "checkpoint_compatible": True,
                        }
                    else:
                        receipt = {
                            "status": "rejected",
                            "reason": "retry evidence unavailable or attempt limit reached",
                        }
                else:
                    receipt = {"status": "applied"}
                result = await self._store.ack_command(scope, command.command_id, token, receipt)
                if result["status"] == "applied" and command.kind == "cancel":
                    return False
                if result["status"] == "applied" and command.kind == "pause":
                    if command.target_node_id is None or command.target_node_id == current_node:
                        return False
            return current_node is None or (
                await self._store.active_lease(scope, task_id, token)
                and await self._store.node_running(scope, task_id, current_node)
            )

    async def _reconcile_stopped(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        token: str,
        stopped_node: str | None = None,
    ) -> bool:
        proceed = await self._apply_controls(scope, task_id, token, stopped_node)
        active = await self._store.active_lease(scope, task_id, token)
        if not active:
            return False
        if stopped_node is not None:
            # A stopped coroutine must not leave its node running without an
            # executor. A proven node pause changes that node's durable status;
            # independent siblings have already completed in the TaskGroup.
            if await self._store.node_running(scope, task_id, stopped_node):
                await self._store.mark_dispatch_blocked(scope, task_id, token)
                return False
            return True
        if not proceed:
            # An interrupted graph with incomplete checkpoint/effect proof
            # cannot hold a silent live lease or claim to have safely paused.
            await self._store.mark_dispatch_blocked(scope, task_id, token)
        return proceed

    async def _project_task(self, scope: ProjectScopeContext, task_id: str) -> None:
        if self._projection is None:
            return
        request = await self._store.task_request(scope, task_id)
        conversation_id = request.get("conversation_id")
        if isinstance(conversation_id, str):
            await self._projection.project_task(scope, task_id)

    async def _heartbeat(self, scope: ProjectScopeContext, task_id: str, token: str) -> None:
        while True:
            await asyncio.sleep(60)
            await self._state.renew_task(scope, task_id, token)

    async def advance(self, scope: ProjectScopeContext, task_id: str, lease_token: str) -> None:
        if (await self._store.task_request(scope, task_id)).get("origin", "native") != "native":
            raise ValueError("observed workflow task has no native runtime")
        heartbeat = asyncio.create_task(self._heartbeat(scope, task_id, lease_token))
        try:
            if not await self._reconcile_stopped(scope, task_id, lease_token):
                return
            request = await self._store.task_request(scope, task_id)
            root_id = await self._state.ensure_root(scope, task_id, lease_token)
            unblocked = await self._state.unblock_parents(scope, task_id, lease_token)
            if unblocked:
                await self._state.release_task(scope, task_id, lease_token, "queued")
                return
            snapshot = await self._store.snapshot(scope, task_id)
            root_node = next(
                (node for node in snapshot["nodes"] if node["node_id"] == root_id), None
            )
            if root_node is not None and root_node["status"] == "succeeded":
                finished = next(
                    (
                        event
                        for event in reversed(snapshot["events"])
                        if event.kind == "node.finished" and event.payload.get("node_id") == root_id
                    ),
                    None,
                )
                if finished is None or "private_result" not in finished.payload:
                    raise ValueError("finished root has no durable result")
                await self._state.finish_task(
                    scope,
                    task_id,
                    lease_token,
                    str(finished.payload["private_result"]),
                    str(finished.payload.get("outcome") or "completed"),
                )
                return
            ready = await self._store.ready_nodes(scope, task_id)
            if root_id in ready:
                result = await self._run_node(
                    scope,
                    task_id,
                    lease_token,
                    root_id,
                    request,
                    snapshot,
                    root=True,
                )
                if result == "waiting":
                    await self._state.release_task(
                        scope, task_id, lease_token, "waiting_dependency"
                    )
                elif result.startswith("limit:"):
                    await self._state.fail_task(
                        scope, task_id, lease_token, result.removeprefix("limit:")
                    )
                elif result == "stopped":
                    if await self._reconcile_stopped(scope, task_id, lease_token, root_id):
                        await self._state.release_task(scope, task_id, lease_token, "queued")
                return
            if not ready:
                await self._state.release_task(scope, task_id, lease_token, "waiting_dependency")
                return
            # A single task token fences all children; each child gets a distinct
            # graph run, messages and checkpoint namespace. The root coroutine has
            # already exited at its durable wait boundary.
            frozen_parallel = int(request.get("max_parallel_agents", self._max_parallel_agents))
            if not 1 <= frozen_parallel <= 20:
                raise ValueError("frozen parallel Agent limit is invalid")
            selected = ready[: min(self._max_parallel_agents, frozen_parallel)]
            async with asyncio.TaskGroup() as group:
                running = [
                    group.create_task(
                        self._run_node(
                            scope,
                            task_id,
                            lease_token,
                            node_id,
                            request,
                            snapshot,
                            root=False,
                        )
                    )
                    for node_id in selected
                ]
            limits = [
                item.result().removeprefix("limit:")
                for item in running
                if item.result().startswith("limit:")
            ]
            if limits:
                await self._state.fail_task(scope, task_id, lease_token, limits[0])
                return
            for node_id, item in zip(selected, running, strict=True):
                if item.result() == "stopped" and not await self._reconcile_stopped(
                    scope, task_id, lease_token, node_id
                ):
                    return
            await self._state.unblock_parents(scope, task_id, lease_token)
            after = await self._store.ready_nodes(scope, task_id)
            await self._state.release_task(
                scope,
                task_id,
                lease_token,
                "queued" if root_id in after else "waiting_dependency",
            )
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

    async def _run_browser_node(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        token: str,
        node_id: str,
        attempt: int,
        request: dict[str, Any],
        snapshot: dict[str, Any],
        authorize: Callable[[], Awaitable[bool]],
    ) -> str:
        if self._coding is None:
            reason = "browser coding runtime unavailable"
            await self._state.fail_node(scope, task_id, token, node_id, reason)
            await self._state.fail_task(scope, task_id, token, reason)
            return "failed"
        if not await authorize():
            return "stopped"
        next_check = 0.0
        stopped = False

        async def stop_requested() -> bool:
            nonlocal next_check, stopped
            if stopped or time.monotonic() < next_check:
                return stopped
            next_check = time.monotonic() + 0.5
            stopped = (
                not await self._store.active_lease(scope, task_id, token)
                or not await authorize()
                or any(
                    command.kind in ("cancel", "pause")
                    and command.target_node_id in (None, node_id)
                    for command in await self._store.pending_commands(scope, task_id)
                )
            )
            return stopped

        try:
            result = await self._coding.run(
                task_id,
                request,
                snapshot,
                scope,
                token,
                node_id,
                attempt,
                stop_requested=stop_requested,
            )
        except (asyncio.CancelledError, WorkflowStopped, WorkflowDispatchBlocked):
            return "stopped"
        except WorkflowLimitExceeded as error:
            return f"limit:{error}"
        except RuntimeError:
            return "stopped"  # termination or side effects are not confirmed
        except (ValueError, TimeoutError) as error:
            reason = str(error)[:2000]
            await self._state.fail_node(scope, task_id, token, node_id, reason)
            await self._state.fail_task(scope, task_id, token, reason)
            return "failed"

        receipt = result.terminal_evidence
        outputs = (
            receipt.get("result", {}).get("outputs", [])
            if isinstance(receipt.get("result"), dict)
            else []
        )
        status = result.status
        output_error: str | None = None
        if status == "passed":
            try:
                if (
                    not isinstance(outputs, list)
                    or len(json.dumps(outputs, ensure_ascii=False, separators=(",", ":")).encode())
                    > 32768
                ):
                    raise ValueError("browser business output invalid")
            except (TypeError, ValueError):
                status = "failed"
                output_error = "browser business output invalid"
                outputs = []
        payload: dict[str, object] = {
            "node_id": node_id,
            "attempt": attempt,
            "status": status,
            "provider_session_id": result.provider_session_id,
            "bundle_ref": result.bundle_ref,
            "execution_ref": result.execution_ref,
            "usage": result.usage,
            "replay_model_calls": result.replay_model_calls,
        }
        coding_failure = _safe_coding_failure(receipt) if status == "failed" else None
        if coding_failure is not None:
            payload["coding_failure"] = coding_failure
        if result.input_request is not None:
            payload["input_request"] = result.input_request
        await self._store.append_event(
            scope,
            task_id,
            token,
            hashlib.sha256(f"browser-result:{task_id}:{node_id}:{attempt}".encode()).hexdigest(),
            "browser.coding_result",
            payload,
        )
        if status == "canceled" and receipt.get("stop_confirmed") is True:
            await self._store.append_event(
                scope,
                task_id,
                token,
                hashlib.sha256(f"browser-stop:{task_id}:{node_id}:{attempt}".encode()).hexdigest(),
                "browser.stop_confirmed",
                {
                    "node_id": node_id,
                    "attempt": attempt,
                    "execution_token": token,
                    "stop_confirmed": True,
                },
            )
        if status == "blocked":
            question = (result.input_request or {}).get("question", "")
            await self._state.await_input(scope, task_id, token, node_id, question)
            return "awaiting_input"
        if status in {"canceled", "unknown"}:
            return "stopped"
        if status == "failed":
            failure = (
                output_error
                or (
                    f"OpenCode failed: {coding_failure['summary']}"
                    if coding_failure is not None
                    else None
                )
                or receipt.get("failure_code")
                or "browser execution failed"
            )
            reason = str(failure)[:2000]
            await self._state.fail_node(scope, task_id, token, node_id, reason)
            await self._state.fail_task(scope, task_id, token, reason)
            return "failed"
        answer = json.dumps(
            {"status": "passed", "execution_ref": result.execution_ref, "outputs": outputs},
            ensure_ascii=True,
        )
        await self._state.finish_node(scope, task_id, token, node_id, answer, "completed")
        await self._state.finish_task(scope, task_id, token, answer, "completed")
        return "finished"

    async def _run_node(
        self,
        scope: ProjectScopeContext,
        task_id: str,
        token: str,
        node_id: str,
        request: dict[str, Any],
        snapshot: dict[str, Any],
        *,
        root: bool,
    ) -> str:
        attempt = await self._state.start_node(scope, task_id, token, node_id)
        conversation_id = request.get("conversation_id")
        origin_turn_id = request.get("origin_turn_id")

        async def authorize() -> bool:
            if origin_turn_id is None:
                return bool(
                    self._standalone_authorize is not None
                    and await self._standalone_authorize(scope, task_id)
                )
            return bool(
                self._chat is not None
                and isinstance(conversation_id, str)
                and await self._chat.authorize_dispatch(
                    conversation_id, str(origin_turn_id), task_id
                )
            )

        request = effective_request(request, snapshot["events"])
        if request.get("kind") == "browser":
            if not root:
                raise ValueError("browser coding must run in the root node")
            return await self._run_browser_node(
                scope, task_id, token, node_id, attempt, request, snapshot, authorize
            )
        graph_run_id, _ = await self._store.graph_lineage(scope, task_id, node_id, attempt)

        events = snapshot["events"]
        nodes = snapshot["nodes"]
        node = next(item for item in nodes if item["node_id"] == node_id)
        if root:
            prompt = str(request["goal"])
            child_results = self._child_results(events, node_id)
            selected = tuple(str(item) for item in request.get("requested_ids", ()))
            model_alias = str(
                (request.get("root") or {}).get("model_alias") or request.get("model_alias") or ""
            )
            agent = request.get("root") or {}
        else:
            delegated = next(
                event
                for event in events
                if event.kind == "node.delegated" and event.payload.get("node_id") == node_id
            )
            prompt = str(delegated.payload["objective"])
            expected = str(delegated.payload.get("expected_output") or "")
            refs = tuple(str(ref) for ref in delegated.payload.get("input_refs", ()))
            prompt += f"\nExpected output: {expected}\nAuthorized input refs: {list(refs)}"
            child_results = self._child_results(events, node_id)
            selected = ()
            candidate = next(
                (
                    item
                    for item in request.get("candidates", ())
                    if item.get("agent_id") == node["role"]
                ),
                None,
            )
            if candidate is None:
                raise ValueError("delegated specialist is not in frozen catalog")
            agent = candidate
            model_alias = str(candidate.get("model_alias") or request.get("model_alias") or "")
        if not model_alias:
            raise ValueError("native workflow has no frozen model alias")
        frozen_skill_ids = tuple(str(item) for item in request.get("skill_revision_ids", ()))
        frozen_skill_text = tuple(str(item) for item in request.get("skill_instructions", ()))
        if len(frozen_skill_ids) != len(frozen_skill_text):
            raise ValueError("frozen workflow Skill content is inconsistent")
        by_revision = dict(zip(frozen_skill_ids, frozen_skill_text, strict=True))
        skills = list(frozen_skill_text)
        for revision_id in agent.get("skill_revision_ids", ()):
            if revision_id not in by_revision:
                if self._skill_loader is None:
                    raise ValueError("frozen specialist Skill content is unavailable")
                skills.append(await self._skill_loader(scope, str(revision_id)))
        frozen_route = None
        if root:
            from tap.modules.workflows.application.routing import RouteDecision

            decided = next((event for event in events if event.kind == "task.route_decided"), None)
            if decided is not None:
                frozen_route = RouteDecision(**decided.payload["decision"])
        catalog = tuple(
            {
                "agentId": str(item["agent_id"]),
                "name": str(item.get("name") or item["agent_id"]),
                "description": str(item.get("description") or ""),
            }
            for item in request.get("candidates", ())
            if isinstance(item, dict)
        )
        skill_catalog = tuple(
            SkillCatalogEntry(**item) for item in request.get("skill_catalog", ())
        )
        loaded_skill_ids = tuple(str(item) for item in request.get("skill_revision_ids", ()))

        async def public_step(
            kind: str,
            call_id: str,
            identifier: str,
            outcome: str | None = None,
        ) -> None:
            payload: dict[str, object] = {
                "node_id": node_id,
                "attempt": attempt,
                "skill_id" if kind.startswith("node.skill_") else "tool_id": identifier,
            }
            if outcome is not None:
                payload["outcome"] = outcome
            event_id = hashlib.sha256(
                f"actual-step:{task_id}:{node_id}:{attempt}:{kind}:{call_id}".encode()
            ).hexdigest()
            await self._store.append_event(scope, task_id, token, event_id, kind, payload)

        async def activate_skill(
            name: str,
            prior: tuple[dict[str, object], ...],
            call_id: str,
        ) -> dict[str, object]:
            if not await self._apply_controls(scope, task_id, token, node_id):
                raise WorkflowStopped("workflow control stopped dispatch")
            if not await authorize():
                raise PermissionError("workflow Skill activation is no longer authorized")
            entry = catalog_entry(name, skill_catalog)
            if entry is None or self._skill_loader is None:
                return {"rejection": "not_in_catalog"}
            await public_step("node.skill_started", call_id, entry.skill_id)
            try:
                instruction = await self._skill_loader(scope, entry.revision_id)
            except ValueError:
                await public_step("node.skill_finished", call_id, entry.skill_id, "rejected")
                return {
                    "skillId": entry.skill_id,
                    "revisionId": entry.revision_id,
                    "rejection": "skill_disabled",
                }
            except Exception:
                await public_step("node.skill_finished", call_id, entry.skill_id, "failed")
                raise
            await public_step("node.skill_finished", call_id, entry.skill_id, "activated")
            return {
                "skillId": entry.skill_id,
                "revisionId": entry.revision_id,
                "name": name,
                "instruction": instruction,
            }

        gateway = self._action_gateway_factory(scope, task_id, token, node_id, attempt, authorize)

        async def before_model() -> str:
            if not await self._apply_controls(scope, task_id, token, node_id):
                raise WorkflowStopped("workflow control stopped dispatch")
            if origin_turn_id is None and not await authorize():
                raise PermissionError("workflow dispatch is no longer authorized")
            return "\n".join(await self._store.applied_steers(scope, task_id, node_id))

        async def delegate(child: DelegationRequest, call_id: str) -> str:
            if not await self._apply_controls(scope, task_id, token, node_id):
                raise WorkflowStopped("workflow control stopped dispatch")
            if not await authorize():
                raise PermissionError("workflow delegation is no longer authorized")
            return await self._state.delegate(scope, task_id, token, node_id, child, call_id)

        async def ordinary_tool(name: str, arguments: dict[str, object], call_id: str) -> str:
            if not await self._apply_controls(scope, task_id, token, node_id):
                raise WorkflowStopped("workflow control stopped dispatch")
            if not await authorize():
                raise PermissionError("workflow tool dispatch is no longer authorized")
            if self._ordinary_tool is None:
                raise ValueError("native ordinary tool unavailable")
            return await self._ordinary_tool(
                scope, task_id, token, node_id, attempt, name, arguments, call_id, authorize
            )

        async def commit_route(decision) -> None:
            payload: dict[str, object] = {
                "node_id": node_id,
                "decision": asdict(decision),
                "input_digest": request.get("input_digest"),
            }
            if decision.kind == "browser" and self._coding is not None:
                url = decision.start_url
                if not isinstance(url, str):
                    raise ValueError("browser destination unavailable")
                parsed = urlsplit(url)
                data = {"goal": request["goal"]}
                canonical = json.dumps(
                    data, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                )
                intent = FrozenBrowserIntent(
                    url,
                    str(request["goal"])[:4000],
                    (f"{parsed.scheme}://{parsed.netloc}",),
                    canonical,
                    hashlib.sha256(canonical.encode()).hexdigest(),
                )
                payload["browser_intent"] = {
                    "start_url": intent.start_url,
                    "expected_result": intent.expected_result,
                    "allowed_origins": list(intent.allowed_origins),
                    "input_data": data,
                    "input_sha256": intent.input_sha256,
                }
                payload["browser_model_alias"] = self._coding.config.model_alias
            event_id = hashlib.sha256(f"route:{task_id}".encode()).hexdigest()
            await self._store.append_event(
                scope,
                task_id,
                token,
                event_id,
                "task.route_decided",
                payload,
            )

        graph = NativeWorkflowGraph(
            gateway,
            checkpointer=self._checkpointer_factory(scope, task_id, token),
            delegate=delegate,
            ordinary_tool=ordinary_tool,
            route_commit=commit_route if root else None,
            before_model=before_model,
            is_root=root,
            browser_available=root and self._coding is not None,
            agent_instruction=str(agent.get("instruction") or ""),
            skill_instructions=tuple(skills),
            allowed_tool_ids=tuple(str(item) for item in agent.get("tool_ids", ())),
            delegate_catalog=catalog if root else (),
            governance_digests=(
                (str(request["input_digest"]),) if request.get("input_digest") else ()
            ),
            frozen_route=frozen_route,
            skill_catalog=skill_catalog,
            skill_activation=activate_skill if self._skill_loader is not None else None,
            loaded_skill_ids=loaded_skill_ids,
            model_timeout_seconds=self._model_timeout_seconds,
        )
        try:
            result = await graph.run(
                scope,
                task_id,
                node_id,
                prompt,
                model_alias,
                requested_ids=selected,
                child_results=child_results,
                graph_run_id=graph_run_id,
            )
        except (WorkflowStopped, WorkflowDispatchBlocked, PermissionError):
            return "stopped"
        except WorkflowLimitExceeded as error:
            return f"limit:{error}"
        except ValueError as error:
            await self._state.fail_node(scope, task_id, token, node_id, str(error))
            if root:
                await self._state.fail_task(scope, task_id, token, str(error))
            return "failed"
        if result.get("waiting_reason") == "clarification":
            await self._state.await_input(
                scope, task_id, token, node_id, str(result.get("answer") or "")
            )
            return "awaiting_input"
        if result.get("waiting_reason"):
            await self._state.wait_node(scope, task_id, token, node_id)
            return "waiting"
        final = result.get("result")
        if not isinstance(final, dict):
            raise ValueError("native graph did not finish or wait")
        if root and final.get("outcome") == "browser_handoff":
            current = await self._store.snapshot(scope, task_id)
            routed = effective_request(request, current["events"])
            return await self._run_browser_node(
                scope, task_id, token, node_id, attempt, routed, current, authorize
            )
        answer = str(final["answer"])
        await self._state.finish_node(
            scope,
            task_id,
            token,
            node_id,
            answer,
            str(final["outcome"]) if root else None,
        )
        if root:
            await self._state.finish_task(
                scope,
                task_id,
                token,
                answer,
                str(final["outcome"]),
            )
        return "finished"

    @staticmethod
    def _child_results(events: object, parent_id: str) -> tuple[dict[str, str], ...]:
        if not isinstance(events, tuple):
            return ()
        finished = {
            event.payload.get("node_id"): event.payload.get(
                "private_result", event.payload.get("summary")
            )
            for event in events
            if event.kind == "node.finished"
        }
        return tuple(
            {
                "call_id": str(event.payload["call_id"]),
                "summary": child_result_summary(
                    str(event.payload["node_id"]), str(finished[event.payload["node_id"]])
                ),
            }
            for event in events
            if event.kind == "node.delegated"
            and event.payload.get("parent_node_id") == parent_id
            and event.payload.get("node_id") in finished
        )


class WorkflowProjection:
    """Feed committed task facts through Chat's own fenced projection port."""

    def __init__(self, store: WorkflowStore, chat: WorkflowChatPort) -> None:
        self._store = store
        self._chat = chat

    async def project(
        self, scope: ProjectScopeContext, conversation_id: str, turn_id: str, task_id: str
    ) -> None:
        from tap.modules.chat.application.workflow_bridge import CommittedWorkflowFact

        claimed = await self._chat.claim_projection(conversation_id, turn_id, task_id)
        if claimed is None:
            return
        token, cursor = claimed
        try:
            from tap.modules.workflows.application.selection import WorkflowSelection

            request = await self._store.task_request(scope, task_id)
            is_origin = request.get("origin_turn_id") == turn_id
            command_id = (
                None
                if is_origin
                else WorkflowSelection.command_id(
                    scope,
                    conversation_id,
                    task_id,
                    await self._chat.client_request_id(conversation_id, turn_id, task_id),
                )
            )
            snapshot = await self._store.snapshot(scope, task_id)
            while cursor < int(snapshot["last_sequence"]):
                events = await self._store.events_after(scope, task_id, cursor, 100)
                if not events:
                    break
                for event in events:
                    kind = event.kind
                    payload = dict(event.payload)
                    if is_origin:
                        if (
                            event.sequence == snapshot["last_sequence"]
                            and snapshot["status"] in ("canceled", "failed")
                            and kind != "task.completed"
                        ):
                            kind = f"task.{snapshot['status']}"
                    else:
                        if kind == "command.received" and payload.get("command_id") == command_id:
                            status = payload.get("status")
                            if status in ("rejected", "stale"):
                                kind = "task.continuation_ack"
                                payload = {"answer": "追问未应用：任务状态或计划版本已变化。"}
                            else:
                                kind = "task.ignored"
                        elif (
                            kind == "command.receipted" and payload.get("command_id") == command_id
                        ):
                            receipt = payload.get("receipt") or {}
                            status = receipt.get("status") if isinstance(receipt, dict) else None
                            reason = receipt.get("reason") if isinstance(receipt, dict) else None
                            kind = "task.continuation_ack"
                            payload = {
                                "answer": "追问已应用。"
                                if status == "applied"
                                else f"追问未应用：{reason or status or '任务状态已变化'}。"
                            }
                        elif kind in ("task.completed", "task.canceled", "task.failed"):
                            kind = "task.continuation_ack"
                            payload = {"answer": "任务已结束，追问未应用。"}
                        else:
                            kind = "task.ignored"
                            payload = {}
                    await self._chat.project_fact(
                        conversation_id,
                        turn_id,
                        token,
                        CommittedWorkflowFact(
                            task_id, event.sequence, kind, payload, event.occurred_at
                        ),
                    )
                    cursor = event.sequence
                    if kind in (
                        "task.completed",
                        "task.canceled",
                        "task.failed",
                        "task.continuation_ack",
                    ):
                        return
        finally:
            await self._chat.release_projection(conversation_id, turn_id, token)

    async def repair(self, scope: ProjectScopeContext, limit: int = 100) -> None:
        for conversation_id, turn_id, task_id in await self._chat.projection_candidates(limit):
            await self.project(scope, conversation_id, turn_id, task_id)

    async def project_task(self, scope: ProjectScopeContext, task_id: str) -> None:
        for conversation_id, turn_id, _ in await self._chat.projection_candidates_for_task(task_id):
            await self.project(scope, conversation_id, turn_id, task_id)
