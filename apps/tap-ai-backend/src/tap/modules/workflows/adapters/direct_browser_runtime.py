"""Native browser decisions through the journaled gateway and browser receipts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Any, Callable

from tap.modules.ai.domain.models import (
    ModelGatewayUnavailable,
    ModelMessage,
    ModelOperation,
    ModelRequest,
    ModelToolCall,
    ModelToolSpec,
    text_digest,
)
from tap.modules.workflows.adapters.browser_client import canonical_json_bytes
from tap.modules.workflows.adapters.opencode_runtime import (
    CodingConfig,
    CodingRuntime,
    _finish_cleanup,
)
from tap.modules.workflows.application.direct_browser import (
    SYSTEM,
    DirectBrowserLoop,
    compile_replay,
)
from tap.modules.workflows.domain.commands import WorkflowDispatchBlocked
from tap.modules.workflows.ports.coding import CodingResult


class DirectBrowserRuntime(CodingRuntime):
    async def _run_agent(self, loop: DirectBrowserLoop, context: dict[str, Any]) -> Any:
        return await loop.run(
            context["request"]["goal"], context["request"]["browser"]["start_url"]
        )

    async def _make_script(self, result: Any, context: dict[str, Any]) -> str:
        return compile_replay(result)

    """Reuse frozen replay/configuration, never the coding container for a live task."""

    def __init__(
        self,
        config: CodingConfig,
        *,
        store: Any,
        gateway_factory: Callable[..., Any],
        model_timeout_seconds: float = 15.0,
    ) -> None:
        super().__init__(config, store=store)
        self.gateway_factory = gateway_factory
        self.model_timeout_seconds = model_timeout_seconds

    async def run(
        self,
        task_id,
        request,
        snapshot,
        scope,
        lease_token,
        node_id,
        attempt,
        stop_requested=None,
        *,
        script_source: dict[str, Any] | None = None,
    ) -> CodingResult:
        if script_source is not None:
            return await super().run(
                task_id,
                request,
                snapshot,
                scope,
                lease_token,
                node_id,
                attempt,
                stop_requested,
                script_source=script_source,
            )
        browser = request.get("browser")
        if request.get("kind") != "browser" or not isinstance(browser, dict):
            raise ValueError("frozen browser task required")
        if browser.get("replay_execution_id"):
            return await super().run(
                task_id, request, snapshot, scope, lease_token, node_id, attempt, stop_requested
            )
        if request.get("model_alias") != self.config.model_alias:
            raise ValueError("browser model alias differs from frozen request")
        inputs, origins = browser.get("input_data"), browser.get("allowed_origins")
        if not isinstance(inputs, dict) or not isinstance(origins, list) or not origins:
            raise ValueError("frozen browser input/origins unavailable")
        input_sha = hashlib.sha256(canonical_json_bytes(inputs)).hexdigest()
        if browser.get("input_sha256") != input_sha:
            raise ValueError("frozen browser input digest mismatch")
        expiry = datetime.fromisoformat(request["deadline_at"])
        if expiry.tzinfo is None:
            raise ValueError("browser deadline must have timezone")
        deadline = time.monotonic() + min(600, (expiry - datetime.now(UTC)).total_seconds())
        limit = request.get("budget_limit", snapshot.get("budget_limit", {}))
        remaining_calls = int(limit.get("model_calls", 0)) - int(
            snapshot.get("budget_used", {}).get("model_calls", 0)
        )
        if not 0 < remaining_calls or int(limit.get("cost_micros", 0)) <= 0:
            raise ValueError("finite browser budget required")
        store: Any = self.store
        run_id, _ = await store.graph_lineage(scope, task_id, node_id, attempt)

        def seconds_left():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("browser task deadline exceeded")
            return remaining

        async def authorized():
            return not (stop_requested and await stop_requested())

        async def bounded(work, *, accept_stopped_result=False, drain_on_stop=False):
            pending = asyncio.create_task(work)
            try:
                while not pending.done():
                    if not await authorized():
                        raise asyncio.CancelledError("workflow cancel requested")
                    await asyncio.wait({pending}, timeout=min(0.1, seconds_left()))
                return await pending
            except BaseException:
                if not pending.done() and not drain_on_stop:
                    pending.cancel()
                settled = await asyncio.gather(pending, return_exceptions=True)
                if accept_stopped_result and isinstance(settled[0], dict):
                    return settled[0]
                raise
            finally:
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

        gateway = self.gateway_factory(scope, task_id, lease_token, node_id, attempt, authorized)
        model_round = 0

        async def decide(messages, tools):
            nonlocal model_round
            if not await authorized():
                raise asyncio.CancelledError("workflow cancel requested")
            model_round += 1
            # Use real assistant/tool messages so the provider sees completed calls.
            # Keep the latest page observation in full and summarize older receipts.
            transcript = []
            call_id = ""
            for index, message in enumerate(messages[2:], start=2):
                if message["role"] == "assistant":
                    call_id = f"browser-{index}"
                    transcript.append(
                        ModelMessage(
                            "assistant",
                            tool_calls=(
                                ModelToolCall(call_id, "browser_step", message["content"]),
                            ),
                        )
                    )
                else:
                    content = message["content"]
                    if index != len(messages) - 1 and '"sequence"' in content:
                        content = (
                            "Action completed; use the latest observation for current controls."
                        )
                    transcript.append(ModelMessage("tool", content, tool_call_id=call_id))
            definition = tools[0]["function"]
            response = await bounded(
                gateway.chat(
                    ModelRequest(
                        scope=scope,
                        alias=self.config.model_alias,
                        operation=ModelOperation.CHAT,
                        prompt=SYSTEM,
                        prompt_digest=text_digest(SYSTEM),
                        context=json.dumps(
                            {"task": json.loads(messages[1]["content"]), "frozen_inputs": inputs},
                            ensure_ascii=False,
                        ),
                        messages=tuple(transcript),
                        timeout_seconds=min(self.model_timeout_seconds, seconds_left()),
                        idempotency_key=f"{task_id}:{node_id}:graph:{run_id}:model:browser:{model_round}",
                        allow_retries=False,
                        max_output_tokens=self.config.max_output_tokens,
                        tools=(
                            ModelToolSpec(
                                "browser.step",
                                "browser_step",
                                definition["description"],
                                definition["parameters"],
                            ),
                        ),
                    )
                ),
                drain_on_stop=True,
            )
            if len(response.tool_calls) != 1 or response.tool_calls[0].name != "browser_step":
                raise ValueError("browser model must choose exactly one browser action")
            action = json.loads(response.tool_calls[0].arguments_json)
            if not isinstance(action, dict):
                raise ValueError("browser model action is not an object")
            return action

        event_sequence = 0
        durations: dict[str, int] = {}

        async def emit(kind, payload):
            nonlocal event_sequence
            event_sequence += 1
            if kind == "browser.action_finished":
                step = payload.get("step_id")
                durations[step] = durations.get(step, 0) + payload.get("duration_ms", 0)
            identity = hashlib.sha256(
                f"direct:{task_id}:{node_id}:{attempt}:{event_sequence}".encode()
            ).hexdigest()
            await store.append_event(
                scope,
                task_id,
                lease_token,
                identity,
                kind,
                {"node_id": node_id, "attempt": attempt, **payload},
            )

        session = await bounded(
            self.browser.session(
                task_id, origins, max(1, int(seconds_left())), f"live-session-{task_id}-{attempt}"
            ),
            accept_stopped_result=True,
        )
        if session.get("status") != "active":
            stopped = (session.get("stop_receipt") or {}).get("stopped") is True
            return CodingResult(
                "canceled" if stopped else "unknown",
                terminal_evidence={**session, "stop_confirmed": stopped},
            )
        session_id = session["session_id"]
        action_sequence = 0

        async def execute(action):
            nonlocal action_sequence
            if not await authorized():
                raise asyncio.CancelledError("workflow cancel requested")
            action_sequence += 1
            return await bounded(
                self.browser.action(
                    session_id,
                    action,
                    f"live-{task_id}-{attempt}-{action_sequence}",
                    deadline_seconds=min(105, max(1, int(seconds_left()))),
                )
            )

        outcome = CodingResult("failed")
        try:
            loop = DirectBrowserLoop(decide, execute, emit, max_rounds=min(remaining_calls, 60))
            context = {
                "request": request,
                "snapshot": snapshot,
                "session": session,
                "gateway": gateway,
                "bounded": bounded,
                "seconds_left": seconds_left,
                "scope": scope,
                "task_id": task_id,
                "node_id": node_id,
                "attempt": attempt,
                "lease_token": lease_token,
                "run_id": run_id,
                "execute": execute,
                "emit": emit,
                "remaining_calls": remaining_calls,
                "stop_requested": stop_requested,
            }
            result = await self._run_agent(loop, context)
            if result.status == "blocked":
                outcome = CodingResult("blocked", input_request=result.input_request)
            elif result.status != "passed":
                outcome = CodingResult("failed", terminal_evidence={"failure": result.error})
            else:
                finished = await execute({"type": "finish"})
                observation = finished.get("observation") or {}
                if (
                    finished.get("status") != "completed"
                    or observation.get("error")
                    or not isinstance(observation.get("result"), dict)
                ):
                    raise ValueError("browser result evidence did not finish")
                business = observation["result"]
                try:
                    script = await self._make_script(result, context)
                    bundle = await bounded(
                        self.browser.bundle(
                            task_id, script, inputs, origins, f"live-bundle-{task_id}-{attempt}"
                        )
                    )
                    receipt = await bounded(
                        self.browser.request(
                            "POST",
                            "/executions",
                            {
                                "task_id": task_id,
                                "bundle_id": bundle["bundle_id"],
                                "input_sha256": input_sha,
                                "live_session_id": session_id,
                                "finish_action_id": finished["action_id"],
                                "steps": [
                                    {
                                        "title": s["keyword"] + " " + s["title"],
                                        "status": "passed",
                                        "duration": durations.get(s["id"], 0),
                                    }
                                    for s in result.steps.values()
                                ],
                            },
                            f"live-receipt-{task_id}-{attempt}",
                        )
                    )
                    outcome = self._receipt_result(receipt, bundle_ref=bundle["bundle_id"])
                except Exception as error:
                    # Business has already happened: never retry it just to obtain code.
                    outcome = CodingResult(
                        "passed",
                        terminal_evidence={
                            "result": business,
                            "script_status": "failed",
                            "script_error": str(error)[:1000],
                        },
                    )
        except (asyncio.CancelledError, WorkflowDispatchBlocked, PermissionError):
            outcome = CodingResult("canceled", terminal_evidence={"stop_confirmed": True})
        except (ValueError, TimeoutError, ModelGatewayUnavailable) as error:
            outcome = CodingResult("failed", terminal_evidence={"failure": str(error)[:1000]})
        finally:
            try:
                stopped = await _finish_cleanup(self.browser.cancel_session(session_id))
                for evidence in stopped.get("stop_receipt", {}).get("evidence", []):
                    if evidence.get("kind") == "video":
                        await emit(
                            "browser.recording_saved", {"evidence_id": evidence["object_ref"]}
                        )
                if stopped.get("stop_receipt", {}).get("stopped") is not True:
                    outcome = CodingResult(
                        "unknown", terminal_evidence={"failure": "browser stop unconfirmed"}
                    )
            except Exception:
                outcome = CodingResult(
                    "unknown", terminal_evidence={"failure": "browser stop unconfirmed"}
                )
        return outcome
