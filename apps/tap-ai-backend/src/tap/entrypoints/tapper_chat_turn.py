"""Node glue binding one claimed chat Turn to the `tapper-chat-v2` `ChatGraph`.

`ChatTurnNodes` adapts the durable, version-agnostic `ChatGraph` machinery to a
single claimed Turn: each public method is one `ChatGraphNodes` entry, reading
only what the persisted `ChatGraphState` carries (never instance state set by a
sibling node), so a node is safely re-invoked after a worker restart resumes a
checkpoint mid-run. `answer_steps` adapts a `knowledge` object that only
implements `answer`/`answer_conversation` into the resumable
`ConversationAnswerSteps` protocol, producing its whole answer in one
`prepare` call.
"""

from __future__ import annotations

import dataclasses
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast

from opentelemetry.trace import get_current_span

from tap.contracts.http import (
    InsightsExplanationResult,
    ResourceMode,
    ResourceRef,
    RetrievalAnswerRequest,
    SourceFamily,
)
from tap.contracts.problems import build_problem
from tap.entrypoints.tapper_draft_batcher import DraftBatcher
from tap.modules.ai.application.chat_graph import ChatGraphNodes, ChatGraphState
from tap.modules.ai.domain.persona import persona_for
from tap.modules.ai.domain.tool_calls import (
    MAX_REQUESTED_TOOL_CALLS_PER_ROUND,
    AdmittedToolCall,
    RejectedToolCall,
    RequestedToolCall,
    admit_tool_calls,
)
from tap.modules.ai.domain.tool_calls import step_id as _tool_step_id
from tap.modules.ai.domain.tools import tool_for_wire_name, visible_tool_ids
from tap.modules.ai.ports.insights import InsightsAuthorizationChanged, InsightsQueryUnavailable
from tap.modules.chat.application.plan_answer import planning_input
from tap.modules.chat.application.ports import ConversationAnswerStep, ConversationAnswerSteps
from tap.modules.chat.application.process_turn import ProviderResult, TurnProcessor
from tap.modules.chat.domain.answer_plan import AnswerPlan
from tap.modules.chat.domain.conversations import (
    AnswerEvidence,
    CitationEvidence,
    GraphContextStatus,
    RetrievalSummary,
)
from tap.modules.knowledge.ports.redaction import RedactionUnavailable
from tap.platform.budget import TurnBudgetExhausted
from tap.platform.telemetry import current_binding, span


async def _identity_redact(text: str) -> str:
    """Fallback `DraftBatcher` redactor when `ChatTurnNodes` is constructed
    with neither `drafts` nor `redact` (every existing non-worker caller):
    never used in production, where the generation worker always passes the
    real v2 chat-egress redactor. Never used when `drafts` is given -- see
    `ChatTurnNodes.__init__`'s fail-closed check -- so this can never
    silently stream unredacted drafts to Redis."""
    return text


def _event_key(event_type: str, payload: Mapping[str, object] | None) -> str:
    """`committed_stream_events`'s dedup key for `event_type`/`payload`
    (M-4): keyed by `type:stage` when `payload` carries a `stage` (e.g.
    `stage.started` for `"verify"` vs. some other future stage), else the
    bare `event_type` -- so a re-run of `verify` after a transient failure
    recognizes its own earlier `stage.started` as already committed instead
    of emitting it again, without conflating it with an unrelated event
    type that happens to share the same bare name.

    PR 5 (Task 9): when `payload` carries a `stepId` (a `task.step`, or a
    `tools`-node `retrieval.hits_ready` correlated to one), the key is
    `f"{type}:{stepId}:{status}"` (the `status` suffix omitted when absent)
    -- so a resumed `tools` node that re-emits the same step's `running`
    event is never conflated with its own later `completed`/`rejected`/
    `failed` terminal event for the same `stepId`."""
    if isinstance(payload, Mapping) and "stepId" in payload:
        status = payload.get("status")
        step_id = payload["stepId"]
        if status is not None:
            return f"{event_type}:{step_id}:{status}"
        return f"{event_type}:{step_id}"
    stage = payload.get("stage") if isinstance(payload, Mapping) else None
    return f"{event_type}:{stage}" if stage is not None else event_type


def _evidence_checkpoint(
    evidence: AnswerEvidence, *, graph_context: dict[str, object] | None = None
) -> dict[str, object]:
    result: dict[str, object] = {
        "answer": evidence.answer,
        "outcome": evidence.outcome,
        "retrievalSummary": {
            "status": evidence.retrieval_summary.status,
            "traceId": evidence.retrieval_summary.trace_id,
            "authorizedHitCount": evidence.retrieval_summary.authorized_hit_count,
        },
        "graphContextStatus": evidence.graph_context_status.value,
        "graphSnapshotId": evidence.graph_snapshot_id,
        # Write-only: `_evidence_from_checkpoint` does not read this key
        # back. On resume, replay uses the `streamEvents` list already
        # baked into the LangGraph checkpoint's `result` (which already
        # contains the fully-formed `graph.context_ready` event from the
        # original run), not a reconstructed `AnswerEvidence`. This key
        # exists only so the richer payload is not silently dropped from
        # the persisted checkpoint record.
        "graphContext": graph_context,
        "citations": [
            {
                "citationSnapshotId": item.citation_snapshot_id,
                "citationDigest": item.citation_digest,
            }
            for item in evidence.citations
        ],
        "diagnostics": list(evidence.diagnostics),
    }
    if evidence.insights_explanation is not None:
        result["insightsExplanation"] = evidence.insights_explanation
    return result


_STEP_KINDS = frozenset({"prepared", "generated", "verified", "final", "tool_calls"})


def _require_step(state: ChatGraphState) -> ConversationAnswerStep:
    """Narrows `state["step"]` (checkpoint-typed `dict[str, object] | None`)
    to the `ConversationAnswerStep` TypedDict a resumed `model`/`verify`/
    `finalize` node expects -- `seed_search` is the only node that produces
    it, so its absence or malformed `kind` means the checkpoint was resumed
    out of order."""
    step = state.get("step")
    if not isinstance(step, dict) or step.get("kind") not in _STEP_KINDS:
        raise ValueError("chat graph state is missing a valid answer step")
    return cast(ConversationAnswerStep, step)


def _evidence_from_checkpoint(raw: object) -> AnswerEvidence:
    if not isinstance(raw, dict) or not isinstance(raw.get("retrievalSummary"), dict):
        raise ValueError("chat graph checkpoint lacks answer evidence")
    summary = raw["retrievalSummary"]
    citations = raw.get("citations", [])
    if not isinstance(citations, list):
        raise ValueError("chat graph checkpoint citations are invalid")
    return AnswerEvidence(
        str(raw.get("answer", "")),
        str(raw["outcome"]),
        RetrievalSummary(
            str(summary["status"]),
            trace_id=summary.get("traceId"),
            authorized_hit_count=int(summary.get("authorizedHitCount", 0)),
        ),
        GraphContextStatus(str(raw["graphContextStatus"])),
        graph_snapshot_id=raw.get("graphSnapshotId"),
        citations=tuple(
            CitationEvidence(str(item["citationSnapshotId"]), str(item["citationDigest"]))
            for item in citations
            if isinstance(item, dict)
        ),
        diagnostics=tuple(str(item) for item in raw.get("diagnostics", [])),
        insights_explanation=raw.get("insightsExplanation"),
    )


def stream_events(
    evidence: AnswerEvidence,
    duration_ms: int,
    *,
    turn: Any,
    answer_response: Any | None,
    graph_context_payload: dict[str, object] | None,
    retrieval_performed: bool = True,
    already_committed: frozenset[str] = frozenset(),
) -> list[dict[str, object]]:
    if evidence.outcome == "failed":
        return []
    events: list[dict[str, object]] = [
        {
            "type": "context.assembled",
            "payload": {
                "contextSnapshotId": turn.input_snapshot.snapshot_id,
                "tokenCount": current_binding().usage.input_tokens,
            },
        },
        {
            "type": "stage.completed",
            "payload": {"stage": "knowledge.answer", "durationMs": duration_ms},
        },
    ]
    if (
        "retrieval.hits_ready" not in already_committed
        and retrieval_performed
        and evidence.retrieval_summary.trace_id is not None
    ):
        events.append(
            {
                "type": "retrieval.hits_ready",
                "payload": {
                    "traceId": evidence.retrieval_summary.trace_id,
                    "authorizedHitCount": evidence.retrieval_summary.authorized_hit_count,
                },
            }
        )
    events.append(
        {
            "type": "graph.context_ready",
            "payload": (
                dict(graph_context_payload)
                if graph_context_payload is not None
                else {
                    "status": evidence.graph_context_status.value,
                    "graphVersion": None,
                    "seedCount": 0,
                    "querySeedCount": 0,
                    "paths": [],
                    "relationCount": 0,
                }
            ),
        }
    )
    # PR 3: the model's full redacted answer is already streamed through
    # `DraftBatcher`/`answer.draft` frames as it is generated (Task 6); it is
    # never re-sent as a single `answer.delta` event here.
    if answer_response is not None:
        events.extend(
            {
                "type": "citation.resolved",
                "payload": {"citation": citation.model_dump(mode="json", by_alias=True)},
            }
            for citation in answer_response.citations
        )
    return events


def answer_steps(knowledge: object) -> ConversationAnswerSteps:
    """`knowledge` already implementing every resumable phase is used directly;
    otherwise it is adapted through `SingleShotAnswerSteps`, which produces the
    whole answer in `prepare` (via `answer_conversation`, or `answer` if that is
    missing too) so existing single-call Knowledge fakes need no changes."""
    if all(
        callable(getattr(knowledge, name, None))
        for name in (
            "prepare_conversation_answer",
            "generate_conversation_answer",
            "verify_conversation_answer",
            "finalize_conversation_answer",
        )
    ):
        return cast(ConversationAnswerSteps, knowledge)
    return SingleShotAnswerSteps(knowledge)


class SingleShotAnswerSteps:
    """Adapts a `knowledge` object exposing only `answer_conversation` (or
    `answer`) to `ConversationAnswerSteps`: `prepare` produces the complete
    answer in one call, so `generate`/`verify` are no-ops and `finalize` just
    returns the stored answer unchanged."""

    def __init__(self, knowledge: Any) -> None:
        self._knowledge = knowledge

    async def prepare_conversation_answer(
        self, request, frozen_input, *, answer_plan=None, authorize=None, include_relations=True
    ) -> ConversationAnswerStep:
        # `include_relations` is accepted and ignored: a single-shot
        # `answer()`/`answer_conversation()` double always produces its
        # complete answer (including any relation analysis it ever does)
        # in this one call -- there is no separate inline-vs-tool relation
        # phase to skip here.
        answer_boundary = getattr(self._knowledge, "answer_conversation", None)
        answer = (
            await answer_boundary(
                request,
                frozen_input,
                **(
                    {"answer_plan": answer_plan, "authorize": authorize}
                    if answer_plan is not None
                    else {}
                ),
            )
            if answer_boundary is not None
            else await self._knowledge.answer(request)
        )
        return cast(ConversationAnswerStep, {"kind": "final", "response": answer})

    async def generate_conversation_answer(
        self,
        step,
        frozen_input,
        *,
        answer_plan=None,
        authorize=None,
        on_text=None,
        tools: tuple[str, ...] = (),
    ) -> ConversationAnswerStep:
        # `prepare` already produced the complete answer; there is nothing
        # left to stream, so `on_text` (if given) is simply never called.
        # `tools` is likewise accepted and ignored: a single-shot `answer()`
        # double has no tool-calling round to offer them to.
        return step

    async def verify_conversation_answer(
        self, step, frozen_input, *, answer_plan=None, authorize=None
    ) -> ConversationAnswerStep:
        return step

    async def finalize_conversation_answer(
        self, step, frozen_input, *, answer_plan=None, authorize=None
    ):
        return step["response"]

    async def refuse_out_of_scope(self, frozen_input, *, answer_plan):
        from tap.contracts.http import AbstentionReason, RetrievalAnswerResponse

        return RetrievalAnswerResponse(
            trace_id=answer_plan.plan_id,
            query_plan_id=answer_plan.plan_id,
            context_snapshot_id=answer_plan.plan_id,
            corpus_version="unversioned",
            retrieval_profile_id="direct-chat-v1",
            degraded_mode=False,
            answer="",
            abstained=True,
            abstention_reason=AbstentionReason.OUT_OF_SCOPE,
            claims=[],
            citations=[],
            graph_context_status="NOT_SELECTED",
        )

    async def run_conversation_tool(
        self, step, frozen_input, call, *, answer_plan=None, authorize=None
    ):
        # `prepare` already produced the complete answer in one call (no
        # `EvidenceBundle` is ever checkpointed here), so this single-shot
        # adapter never reaches the bounded tool-calling loop that admits a
        # `knowledge.search`/`graph.relations` call in the first place.
        raise ValueError("single-shot answer boundary does not support conversation tool calls")

    async def add_insights_records(self, step, records):
        # Same reasoning as `run_conversation_tool`: a single-shot `answer()`
        # double never checkpoints an `EvidenceBundle` and its generation
        # never produces `tool_calls`, so this is unreachable in practice.
        raise NotImplementedError(
            "single-shot answer boundary does not support insights.query records"
        )


class ChatTurnNodes:
    """Binds one claimed chat Turn's resumable answer phases, Insights
    explanation and finalization to `ChatGraphNodes` for `ChatGraph`."""

    def __init__(
        self,
        *,
        conversation_id: str,
        turn: Any,
        conversations: Any,
        steps: ConversationAnswerSteps,
        planner: Any | None,
        knowledge: Any,
        insights_explanation: Any | None,
        require_authorized: Callable[[], Awaitable[None]],
        turn_span: Any | None = None,
        attempt_started_at: float | None = None,
        retryable: Callable[[BaseException], bool] = lambda _error: False,
        drafts: Any | None = None,
        redact: Callable[[str], Awaitable[str]] | None = None,
    ) -> None:
        self._conversation_id = conversation_id
        self._turn = turn
        self._conversations = conversations
        self._steps = steps
        self._planner = planner
        self._knowledge = knowledge
        self._insights_explanation = insights_explanation
        self._require_authorized = require_authorized
        self._attempt_started_at = attempt_started_at
        # PR 3: the Redis `DraftStream` (`None` when Redis is not configured)
        # and the conversation-path personal-data (v2) egress redactor the
        # `model` node's `DraftBatcher` streams through; both optional so
        # every existing caller that constructs `ChatTurnNodes` directly
        # (tests, other entrypoints) is unaffected.
        if drafts is not None and redact is None:
            # Fail-closed (M-7): silently falling back to `_identity_redact`
            # here would stream every draft to Redis completely unredacted.
            # A caller that wires a real `DraftStream` must also wire the
            # real redactor -- there is no safe default.
            raise ValueError("ChatTurnNodes given drafts without a redact callable")
        self._drafts = drafts
        self._redact = redact or _identity_redact
        # Same predicate the worker passes to `ChatGraph(retryable=...)`
        # (injected here rather than imported, so this `tap.entrypoints`
        # module does not reach back into `tapper_turn_failures` itself --
        # keeps the predicate a single worker-owned policy). `finalize` uses
        # it to let a transient fault from the knowledge finalize step (or
        # the post-graph reauthorization recheck) escape `TurnProcessor`
        # instead of being swallowed into a permanent `turn.failed`.
        self._retryable = retryable
        # `ChatGraph` wraps every node body in its own `graph.node.<name>` span
        # (see `chat_graph.py`'s `_wrap`), so `get_current_span()` would no
        # longer resolve to the outer `turn.execute` span the old single
        # `execute` node ran in. Audit attributes must land on that same
        # `turn.execute` span as before, so the worker passes it through
        # explicitly rather than relying on the ambient current span.
        self._turn_span = turn_span
        self._insights_query_id = getattr(turn.input_snapshot.value, "insights_query_id", None)

    @property
    def nodes(self) -> ChatGraphNodes:
        return ChatGraphNodes(
            guard_in=self.guard_in,
            insights_explain=self.insights_explain,
            seed_search=self.seed_search,
            model=self.model,
            tools=self.tools,
            verify=self.verify,
            finalize=self.finalize,
            ingress=self.ingress,
        )

    async def ingress(self, state: ChatGraphState) -> dict[str, object]:
        budget = dict(state.get("budget") or {})
        result: dict[str, object] = {}
        is_fresh_run = "startedAtMs" not in budget
        if is_fresh_run:
            budget["startedAtMs"] = int(time.time() * 1000)
            result["budget"] = budget
            result["budget_used"] = {"modelCalls": 0, "costMicros": 0, "toolRounds": 0}
        audit_span = self._turn_span if self._turn_span is not None else get_current_span()
        audit_span.set_attribute("tap.budget.max_model_calls", int(budget.get("maxModelCalls", 0)))
        audit_span.set_attribute("tap.budget.max_seconds", float(budget.get("maxSeconds", 0.0)))
        audit_span.set_attribute("tap.budget.max_tool_rounds", int(budget.get("maxToolRounds", 0)))
        audit_span.set_attribute("tap.budget.max_cost_micros", int(budget.get("maxCostMicros", 0)))
        if is_fresh_run:
            value = self._turn.input_snapshot.value
            persona_version = persona_for(getattr(value, "persona_version", None)).version
            result["persona_version"] = persona_version
            audit_span.set_attribute("tap.persona.version", persona_version)
            agent_revision_id = getattr(value, "agent_revision_id", None)
            agent_tool_allowlist = tuple(getattr(value, "agent_tool_allowlist", ()) or ())
            agent: dict[str, object] | None = None
            if agent_revision_id is not None:
                agent = {
                    "revisionId": agent_revision_id,
                    "toolAllowlist": list(agent_tool_allowlist),
                }
                audit_span.set_attribute("tap.agent.revision_id", agent_revision_id)
            relations_available = bool(getattr(self._knowledge, "relations_available", False))
            insights_available = (
                self._insights_explanation is not None
                and bool(getattr(value, "insights_report_refs", ()))
                and getattr(value, "insights_query_id", None) is None
            )
            tools = list(
                visible_tool_ids(
                    agent_allowlist=None if agent is None else frozenset(agent_tool_allowlist),
                    relations_available=relations_available,
                    insights_available=insights_available,
                )
            )
            result["agent"] = agent
            result["tools"] = tools
            audit_span.set_attribute("tap.agent.tools", ",".join(tools))
        return result

    def _active_plan(self, state: ChatGraphState) -> AnswerPlan | None:
        if self._planner is None:
            return None
        raw = state.get("answer_plan")
        if raw is None:
            return None
        plan = AnswerPlan.from_dict(raw)
        budget = state.get("budget")
        if budget and float(budget.get("maxSeconds", 0.0)) > 0:
            # Capability-4 fix round 1 (D3 end to end): inside a budgeted
            # Turn the plan window *is* the Turn's wall-clock window (spec
            # 4.3: one 3-minute Turn budget). The plan's own pre-graph
            # `remaining_seconds` (30 s) is shared by retrieval, tool rounds
            # and the whole streamed generation, so keeping it would cut a
            # long, steadily streaming answer long before the Turn budget,
            # the gateway's idle timeout or its overall stream cap. Never
            # past the Turn's end either (I2), so a fresh attempt cannot get
            # a new window once the Turn budget is nearly spent.
            turn_end = budget.get("startedAtMs", 0) / 1000 + float(budget["maxSeconds"])
            return dataclasses.replace(plan, deadline_at=turn_end)
        if state.get("plan_attempt") != self._turn.attempt and self._attempt_started_at is not None:
            deadline_at = max(plan.deadline_at, self._attempt_started_at + plan.remaining_seconds)
            return dataclasses.replace(plan, deadline_at=deadline_at)
        return plan

    async def guard_in(self, _state: ChatGraphState) -> dict[str, object]:
        # Trace-only: the message saved on the Turn stays as-is (existing
        # rules), and the retrieval/generation paths already redact what
        # they send the model (`retrieve.py`); this just records whether the
        # incoming message itself carried redactable content. Best-effort: a
        # message the redactor cannot parse (over its bound, an unterminated
        # credential-looking value, ...) must never fail the turn over a
        # trace attribute, so any exception here is swallowed and recorded
        # as unavailable instead of raised.
        audit_span = self._turn_span if self._turn_span is not None else get_current_span()
        redaction_report = getattr(self._knowledge, "redaction_report", None)
        if redaction_report is not None:
            try:
                input_hits = await redaction_report(self._turn.input_snapshot.value.message)
            except Exception:
                audit_span.set_attribute("tap.guard.redaction.input_hits_unavailable", True)
            else:
                if input_hits:
                    audit_span.set_attribute(
                        "tap.guard.redaction.input_hits", json.dumps(dict(input_hits))
                    )
        if self._insights_query_id is not None:
            return {"route": "insights", "reasoning_mode": "workflow"}
        if self._planner is None:
            return {"route": "chat", "reasoning_mode": "direct"}
        authorize_planning = getattr(self._knowledge, "authorize_planning", None)
        if authorize_planning is not None:
            await authorize_planning(self._turn.input_snapshot)
        load = getattr(self._conversations, "load", None)
        history = (
            ()
            if load is None
            else tuple(item.input_snapshot for item in (await load(self._conversation_id)).turns)
        )
        plan = await self._planner.plan(planning_input(self._turn.input_snapshot, history=history))
        audit_span.set_attribute("tap.plan.intent", plan.agent_intent)
        if plan.intent_reason:
            audit_span.set_attribute("tap.plan.intent_reason", plan.intent_reason)
        audit_span.set_attribute("tap.plan.answer_intent", plan.intent)
        route = "out_of_scope" if plan.intent == "out_of_scope" else "chat"
        result: dict[str, object] = {
            "route": route,
            "reasoning_mode": "direct",
            "answer_plan": plan.to_dict(),
            "plan_attempt": self._turn.attempt,
        }
        if route == "out_of_scope":
            # `seed_search` never runs on this route, so it never stamps
            # `started_at_ms` the way it does for the chat route; without this,
            # `finalize`'s `duration_ms` would be measured against the epoch
            # (`state.get("started_at_ms") or 0`), producing a huge bogus
            # `stage.completed` duration.
            result["started_at_ms"] = int(time.time() * 1000)
        return result

    async def seed_search(self, state: ChatGraphState) -> dict[str, object]:
        active_plan = self._active_plan(state)
        if active_plan is not None:
            active_plan.validate_binding(planning_input(self._turn.input_snapshot))
        value = self._turn.input_snapshot.value
        request = RetrievalAnswerRequest(
            query=value.message,
            sources=[SourceFamily.DOC],
            resource_refs=[
                ResourceRef(
                    family=SourceFamily.DOC, source_id=item.source_id, mode=ResourceMode.SCOPE
                )
                for item in value.resolved_resources
            ],
        )
        # Wall-clock epoch milliseconds, not `time.monotonic()`: this value is
        # checkpointed and `finalize` may resume it on a different worker
        # process or host, where a monotonic clock's origin is not
        # comparable. Captured before `prepare_conversation_answer` so a slow
        # retrieval call is still reflected in `stage.completed`'s
        # `durationMs` (matching the old code, which timed the whole
        # `TurnProcessor.process` call).
        started_at_ms = int(time.time() * 1000)
        step = await self._steps.prepare_conversation_answer(
            request,
            value,
            answer_plan=active_plan,
            authorize=self._require_authorized,
            include_relations=False,
        )
        result: dict[str, object] = {"step": step, "started_at_ms": started_at_ms}
        retrieval_hits = step.get("retrieval_hits") if isinstance(step, dict) else None
        committed = await self._emit_committed(
            state, "retrieval.hits_ready", await self._redact_labels(retrieval_hits)
        )
        if committed is not None:
            result["committed_stream_events"] = committed
        return result

    async def _redact_labels(
        self, payload: Mapping[str, object] | None
    ) -> dict[str, object] | None:
        """M-6: `retrieval.hits_ready`'s `labels` carry a source `title` and
        `location` (a heading path joined with `" / "`, or a page number)
        taken straight from document structure -- never authored or
        screened by the model -- so, unlike the rest of the payload, they
        must still pass through the v2 chat-egress redactor before this
        event is ever emitted.

        `_redact` can raise `RedactionUnavailable` for a field whose
        document-sourced text looks like a half-written credential or
        private-key block (e.g. a heading literally titled "Account /
        Forgot password:") -- these are *display* labels, not the
        authoritative answer, so that must never fail the Turn. A field
        that fails to redact is caught per-field and dropped (set to
        `None`); `title` is the one field callers always render, so a label
        whose `title` cannot be redacted is dropped from the list entirely
        rather than emitted with a missing title."""
        labels = payload.get("labels") if payload else None
        if not payload or not isinstance(labels, list):
            return None if payload is None else dict(payload)
        redacted_labels: list[dict[str, object]] = []
        for raw_label in labels:
            label = cast(Mapping[str, object], raw_label)
            title_ok, title = await self._try_redact(label.get("title"))
            if not title_ok:
                # Callers always render `title`; a label whose title cannot
                # be safely redacted has nothing safe to show, so the whole
                # label is dropped rather than emitted with a blank title.
                continue
            _location_ok, location = await self._try_redact(label.get("location"))
            redacted_labels.append({**label, "title": title, "location": location})
        return {**payload, "labels": redacted_labels}

    async def _try_redact(self, value: object) -> tuple[bool, object]:
        """Redacts one `retrieval.hits_ready` label field, never letting a
        `RedactionUnavailable` for this field fail the Turn. Returns
        `(True, redacted_text)` on success, or `(False, None)` if `_redact`
        raised -- the field is set to `None` either way the caller uses it."""
        if value is None:
            return True, None
        try:
            return True, await self._redact(str(value))
        except RedactionUnavailable:
            return False, None

    async def _redact_summary(self, text: str) -> str:
        """PR 5 (Task 9 fix round 1): every `task.step` `summary` can carry
        model-controlled text (the running query, a rejected call's own
        wire-format name, `run_conversation_tool`'s echoed query) -- it must
        pass through the same v2 conversation-egress redactor as the final
        answer before it is ever emitted, truncated to `TaskStepPayload`'s
        200-char bound *after* redaction (so a redaction placeholder is
        never itself cut off). `RedactionUnavailable` (an unterminated
        credential-looking value) must never fail the Turn over a status
        summary -- it degrades to a safe, content-free placeholder."""
        try:
            redacted = await self._redact(text)
        except RedactionUnavailable:
            return "内容已隐去（无法安全脱敏）"
        return redacted[:200]

    async def _emit_committed(
        self, state: ChatGraphState, event_type: str, payload: Mapping[str, object] | None
    ) -> list[str] | None:
        """Commits `event_type`/`payload` as a stream event the instant it is
        ready (ahead of the terminal `complete_evidence` batch), through
        `conversations.emit`. Skips re-emitting (and returns `None`) when
        this exact `event_type`/stage combination (M-4's `_event_key`) is
        already in `committed_stream_events`. The actual guarantee this
        gives: no duplicate is ever emitted across a successful node
        completion or a checkpoint resume, because `committed_stream_events`
        is itself part of the checkpointed state. It does *not* cover a
        retry after `verify` raised a retryable exception -- that state
        update was never returned/checkpointed, so a later retry's
        `_emit_committed(state, "stage.started", ...)` sees a `state`
        without this node's key and commits `stage.started` a second time.
        That is a harmless, duplicate *progress* event (not the final
        evidence), so it is an acceptable gap rather than a guarantee this
        method makes. Returns the updated `committed_stream_events` list (to
        merge into the node's returned state) so `finalize` never repeats an
        event already committed here, or `None` if nothing was (newly)
        committed."""
        if not payload:
            return None
        key = _event_key(event_type, payload)
        committed = list(state.get("committed_stream_events") or ())
        if key in committed:
            return None
        await self._conversations.emit(
            self._conversation_id,
            self._turn.turn_id,
            event_type,
            dict(payload),
            lease_token=self._turn.lease_token,
        )
        committed.append(key)
        return committed

    async def model(self, state: ChatGraphState) -> dict[str, object]:
        active_plan = self._active_plan(state)
        batcher = DraftBatcher(
            turn_id=self._turn.turn_id,
            attempt=self._turn.attempt,
            drafts=self._drafts,
            redact=self._redact,
            start_seq=int(state.get("draft_seq", 0)),
        )
        budget = state.get("budget") or {}
        budget_used = state.get("budget_used") or {}
        max_tool_rounds = int(budget.get("maxToolRounds", 0))
        tool_rounds_used = int(budget_used.get("toolRounds", 0))
        visible_tools = tuple(state.get("tools") or ())
        offered = visible_tools if tool_rounds_used < max_tool_rounds else ()
        step = await self._steps.generate_conversation_answer(
            _require_step(state),
            self._turn.input_snapshot.value,
            answer_plan=active_plan,
            authorize=self._require_authorized,
            on_text=batcher.push,
            tools=offered,
        )
        if step.get("kind") == "tool_calls":
            await batcher.discard()
            if not offered:
                # Review Focus 4 fix round 1: the final round offered no
                # tools (`offered == ()`), yet the model still requested
                # one -- structurally this should be unreachable (the
                # gateway declares no callable functions when `tools` is
                # empty), but a non-compliant model must still never fail
                # the Turn outright. Fall back to the existing out-of-
                # scope-style abstention path instead of entering `tools`
                # (which would raise `TurnBudgetExhausted("tool-rounds")`).
                response = await self._steps.refuse_out_of_scope(
                    self._turn.input_snapshot.value, answer_plan=active_plan
                )
                step = {
                    "kind": "final",
                    "response": response.model_dump(mode="json", by_alias=True),
                }
        else:
            await batcher.close()
        if not offered and visible_tools:
            audit_span = self._turn_span if self._turn_span is not None else get_current_span()
            audit_span.set_attribute("tap.agent.tool_rounds_exhausted", True)
        return {"step": step, "draft_seq": batcher.current_seq}

    async def tools(self, state: ChatGraphState) -> dict[str, object]:
        """Execute one bounded tool-calling round (PR 5 Task 9): admits the
        model's pending tool calls against this Turn's visible tools and
        frozen source selection, runs each admitted call (soft-failing an
        `insights.query` authorization change rather than failing the
        Turn), and emits a `running` then terminal `task.step` for every
        requested call (admitted or rejected) plus a `stepId`-correlated
        `retrieval.hits_ready` for a knowledge/relation call that grew the
        evidence pool. `AuthorizationDenied`/`PermissionError` from
        `run_conversation_tool`'s re-authorization recheck propagate
        uncaught -- only `InsightsAuthorizationChanged` is treated as a
        soft per-call failure."""
        budget = state.get("budget") or {}
        budget_used = state.get("budget_used") or {}
        max_tool_rounds = int(budget.get("maxToolRounds", 0))
        tool_rounds_used = int(budget_used.get("toolRounds", 0))
        round_number = tool_rounds_used + 1
        if round_number > max_tool_rounds:
            raise TurnBudgetExhausted("tool-rounds")

        active_plan = self._active_plan(state)
        step = _require_step(state)
        value = self._turn.input_snapshot.value
        selected_source_ids = frozenset(item.source_id for item in value.resolved_resources)
        pending_raw = step.get("pending_tool_calls") or []
        requested = tuple(
            RequestedToolCall(
                call_id=str(item["callId"]),
                name=str(item["name"]),
                arguments_json=str(item["argumentsJson"]),
            )
            for item in cast(list[dict[str, str]], pending_raw)
        )
        outcomes = admit_tool_calls(
            requested,
            visible=tuple(state.get("tools") or ()),
            selected_source_ids=selected_source_ids,
        )

        current_step: dict[str, object] = dict(step)
        transcript = list(cast("list[dict[str, object]]", current_step.get("transcript") or []))
        committed = list(state.get("committed_stream_events") or ())

        async def emit(event_type: str, payload: Mapping[str, object]) -> None:
            nonlocal committed
            updated = await self._emit_committed(
                cast(ChatGraphState, {**state, "committed_stream_events": committed}),
                event_type,
                payload,
            )
            if updated is not None:
                committed = updated

        for index, outcome in enumerate(outcomes):
            this_step_id = _tool_step_id(self._turn.turn_id, round_number, index)
            if (
                isinstance(outcome, RejectedToolCall)
                and index >= MAX_REQUESTED_TOOL_CALLS_PER_ROUND
            ):
                # Final review M-8: past the requested-call total, a call is
                # answered in the transcript (every model call id needs its
                # tool reply) but emits no `task.step` events -- the round's
                # step count stays bounded however many calls the model asks
                # for.
                transcript.append(
                    {
                        "role": "tool",
                        "toolCallId": outcome.call_id,
                        "toolId": outcome.name,
                        "content": json.dumps({"status": "rejected", "reason": outcome.reason}),
                    }
                )
                continue
            if isinstance(outcome, RejectedToolCall):
                # Fix round 1: `outcome.name` is the model's own (possibly
                # unknown) wire-format function name -- resolve it to a
                # canonical tool id for the `tool` field when it names a
                # real tool, else a safe non-empty placeholder; the raw
                # name only ever appears inside the (redacted) summary.
                definition = tool_for_wire_name(outcome.name)
                tool_identifier = definition.tool_id if definition is not None else "unknown"
                summary = await self._redact_summary(f"{outcome.name} → 已拒绝（{outcome.reason}）")
                # Fix round 1 (Review Focus 3): a rejected call never runs,
                # but still reports `running` before its terminal status --
                # the same two-event shape every admitted call gets.
                await emit(
                    "task.step",
                    {
                        "stepId": this_step_id,
                        "tool": tool_identifier,
                        "status": "running",
                        "summary": summary,
                        "durationMs": 0,
                        "toolRound": round_number,
                    },
                )
                await emit(
                    "task.step",
                    {
                        "stepId": this_step_id,
                        "tool": tool_identifier,
                        "status": "rejected",
                        "summary": summary,
                        "durationMs": 0,
                        "toolRound": round_number,
                    },
                )
                transcript.append(
                    {
                        "role": "tool",
                        "toolCallId": outcome.call_id,
                        "toolId": outcome.name,
                        "content": json.dumps({"status": "rejected", "reason": outcome.reason}),
                    }
                )
                continue

            call = cast(AdmittedToolCall, outcome)
            started_at = time.monotonic()
            # Final review M-2: redact the query before cutting it to 60
            # characters, so an ID/email straddling the cut is never split
            # into an unrecognizable fragment.
            running_query = (await self._redact_summary(str(call.arguments.get("query", ""))))[:60]
            running_summary = await self._redact_summary(f'{call.tool_id} "{running_query}"')
            await emit(
                "task.step",
                {
                    "stepId": this_step_id,
                    "tool": call.tool_id,
                    "status": "running",
                    "summary": running_summary,
                    "durationMs": 0,
                    "toolRound": round_number,
                },
            )

            if call.tool_id == "insights.query":
                if self._insights_explanation is None:
                    # Fix round 1: no Insights runtime is configured for
                    # this deployment -- a structurally impossible call
                    # given `visible_tool_ids`'s `insights_available` gate,
                    # but still a soft per-call failure, never an
                    # `AssertionError` that would crash the Turn.
                    summary = await self._redact_summary("insights.query → 查询服务不可用")
                    await emit(
                        "task.step",
                        {
                            "stepId": this_step_id,
                            "tool": call.tool_id,
                            "status": "failed",
                            "summary": summary,
                            "durationMs": 0,
                            "toolRound": round_number,
                        },
                    )
                    transcript.append(
                        {
                            "role": "tool",
                            "toolCallId": call.call_id,
                            "toolId": call.tool_id,
                            "content": json.dumps({"status": "failed", "summary": summary}),
                        }
                    )
                    continue
                try:
                    with span("tool.insights.query", {"tap.tool.step_id": this_step_id}):
                        records = await self._insights_explanation.query_metrics(
                            self._conversations.scope,
                            tuple(getattr(value, "insights_report_refs", ())),
                            call.arguments,
                        )
                except InsightsAuthorizationChanged:
                    duration_ms = int((time.monotonic() - started_at) * 1000)
                    summary = await self._redact_summary("insights.query → 查询授权已变更")
                    await emit(
                        "task.step",
                        {
                            "stepId": this_step_id,
                            "tool": call.tool_id,
                            "status": "failed",
                            "summary": summary,
                            "durationMs": duration_ms,
                            "toolRound": round_number,
                        },
                    )
                    transcript.append(
                        {
                            "role": "tool",
                            "toolCallId": call.call_id,
                            "toolId": call.tool_id,
                            "content": json.dumps({"status": "failed", "summary": summary}),
                        }
                    )
                    continue
                new_step, added_labels = await self._steps.add_insights_records(
                    cast(ConversationAnswerStep, current_step), records
                )
                current_step = dict(new_step)
                duration_ms = int((time.monotonic() - started_at) * 1000)
                summary = await self._redact_summary(
                    f"insights.query → +{len(added_labels)} 条指标"
                    if added_labels
                    else "insights.query → 无新指标"
                )
                await emit(
                    "task.step",
                    {
                        "stepId": this_step_id,
                        "tool": call.tool_id,
                        "status": "completed",
                        "summary": summary,
                        "durationMs": duration_ms,
                        "toolRound": round_number,
                    },
                )
                transcript.append(
                    {
                        "role": "tool",
                        "toolCallId": call.call_id,
                        "toolId": call.tool_id,
                        "content": json.dumps(
                            {
                                "status": "completed",
                                "summary": summary,
                                "addedLabels": added_labels,
                            }
                        ),
                    }
                )
                continue

            with span(f"tool.{call.tool_id}", {"tap.tool.step_id": this_step_id}):
                tool_result = await self._steps.run_conversation_tool(
                    cast(ConversationAnswerStep, current_step),
                    value,
                    call,
                    answer_plan=active_plan,
                    authorize=self._require_authorized,
                )
            current_step = dict(tool_result["step"])
            duration_ms = int((time.monotonic() - started_at) * 1000)
            status = tool_result["status"]
            summary = await self._redact_summary(str(tool_result["summary"]))
            await emit(
                "task.step",
                {
                    "stepId": this_step_id,
                    "tool": call.tool_id,
                    "status": status,
                    "summary": summary,
                    "durationMs": duration_ms,
                    "toolRound": round_number,
                },
            )
            retrieval_hits = tool_result.get("retrieval_hits")
            if retrieval_hits:
                redacted = await self._redact_labels(retrieval_hits)
                if redacted is not None:
                    await emit("retrieval.hits_ready", {**redacted, "stepId": this_step_id})
            transcript.append(
                {
                    "role": "tool",
                    "toolCallId": call.call_id,
                    "toolId": call.tool_id,
                    "content": json.dumps(
                        {
                            "status": status,
                            "summary": summary,
                            "addedLabels": tool_result.get("added_labels", []),
                        }
                    ),
                }
            )

        current_step["kind"] = "prepared"
        current_step["transcript"] = transcript
        current_step.pop("pending_tool_calls", None)
        result: dict[str, object] = {"step": current_step, "tool_rounds_delta": 1}
        if committed != list(state.get("committed_stream_events") or ()):
            result["committed_stream_events"] = committed
        return result

    async def verify(self, state: ChatGraphState) -> dict[str, object]:
        active_plan = self._active_plan(state)
        result: dict[str, object] = {}
        committed = await self._emit_committed(state, "stage.started", {"stage": "verify"})
        if committed is not None:
            result["committed_stream_events"] = committed
        step = await self._steps.verify_conversation_answer(
            _require_step(state),
            self._turn.input_snapshot.value,
            answer_plan=active_plan,
            authorize=self._require_authorized,
        )
        result["step"] = step
        return result

    async def insights_explain(self, _state: ChatGraphState) -> dict[str, object]:
        if self._insights_explanation is None:
            raise RuntimeError("Insights explanation runtime is unavailable")
        turn = self._turn
        explanation_started = time.monotonic()
        result = await self._insights_explanation.explain(
            self._conversations.scope,
            self._insights_query_id,
            turn.input_snapshot.value.insights_report_refs,
            turn.input_snapshot.value.message,
            conversation_id=self._conversation_id,
            turn_id=turn.turn_id,
            selected_knowledge=turn.input_snapshot.value.resolved_resources,
        )
        explanation_duration_ms = int((time.monotonic() - explanation_started) * 1000)
        if result.get("queryId") != self._insights_query_id:
            raise InsightsQueryUnavailable("Insights result changed the historical query ID")
        try:
            InsightsExplanationResult.model_validate(result)
        except ValueError as exc:
            raise InsightsQueryUnavailable(
                "Insights result does not satisfy the response contract"
            ) from exc
        evidence = AnswerEvidence(
            "Insights interpretation",
            "completed",
            RetrievalSummary("completed", trace_id=self._insights_query_id),
            GraphContextStatus.NOT_REQUESTED,
            insights_explanation=dict(result),
        )
        knowledge_search_performed = result.get("knowledgeSearchPerformed", False) is True
        knowledge_sources = [
            {
                "sourceId": item.source_id,
                "revisionId": item.revision_id,
                "sourceContentHash": item.source_content_hash,
            }
            for item in turn.input_snapshot.value.resolved_resources
        ]
        knowledge_citations = [
            {
                "citationId": item.get("citationId"),
                "sourceId": item.get("sourceId"),
                "revisionId": item.get("revisionId"),
                "chunkId": item.get("chunkId"),
                "publicationId": item.get("publicationId"),
                "approvalDigest": item.get("approvalDigest"),
            }
            for item in result.get("evidenceExcerpts", [])
            if isinstance(item, dict) and item.get("sourceId")
        ]
        audit_span = self._turn_span if self._turn_span is not None else get_current_span()
        audit_span.set_attribute("tap.insights.conversation_id", self._conversation_id)
        audit_span.set_attribute("tap.insights.turn_id", turn.turn_id)
        audit_span.set_attribute("tap.insights.graph_run_id", turn.turn_id)
        audit_span.set_attribute("tap.insights.tool", "insights.query")
        audit_span.set_attribute("tap.insights.query_id", self._insights_query_id)
        metric_version = result.get("metricVersion")
        if metric_version is not None:
            audit_span.set_attribute("tap.insights.metric_version", metric_version)
        audit_span.set_attribute(
            "tap.insights.resource_refs", list(turn.input_snapshot.value.insights_report_refs)
        )
        audit_span.set_attribute(
            "tap.insights.knowledge_search_performed", knowledge_search_performed
        )
        audit_span.set_attribute("tap.insights.knowledge_sources", json.dumps(knowledge_sources))
        audit_span.set_attribute(
            "tap.insights.knowledge_citations", json.dumps(knowledge_citations)
        )
        audit_events: list[dict[str, object]] = [
            {
                "type": "context.assembled",
                "payload": {
                    "contextSnapshotId": turn.input_snapshot.snapshot_id,
                    "tokenCount": current_binding().usage.input_tokens,
                },
            },
            {
                "type": "stage.completed",
                "payload": {
                    "stage": "insights.explanation",
                    "durationMs": explanation_duration_ms,
                },
            },
        ]
        if knowledge_search_performed:
            audit_span.set_attribute(
                "tap.retrieval.source_ids",
                [item.source_id for item in turn.input_snapshot.value.resolved_resources],
            )
            audit_events.append(
                {
                    "type": "stage.completed",
                    "payload": {
                        "stage": "knowledge.search",
                        "durationMs": explanation_duration_ms,
                    },
                }
            )
        return {
            "result": {
                "evidence": _evidence_checkpoint(evidence),
                "terminalEvent": {"type": "turn.completed", "payload": {"state": "completed"}},
                "streamEvents": audit_events,
            }
        }

    def _failed_result(self, failure: Mapping[str, object]) -> dict[str, object]:
        evidence = AnswerEvidence(
            "", "failed", RetrievalSummary("failed"), GraphContextStatus.FAILED
        )
        problem_type = (
            "budget-exhausted"
            if failure.get("error") in ("TurnBudgetExhausted", "AnswerBudgetExhausted")
            else "answer-unavailable"
        )
        return {
            "result": {
                "evidence": _evidence_checkpoint(evidence),
                "terminalEvent": {
                    "type": "turn.failed",
                    "payload": {
                        "problem": build_problem(
                            problem_type, correlation_id=self._turn.turn_id
                        ).model_dump(mode="json", by_alias=True)
                    },
                },
                "streamEvents": [],
            }
        }

    async def finalize(self, state: ChatGraphState) -> dict[str, object]:
        failure = state.get("failure")
        if failure:
            return self._failed_result(failure)
        if state.get("route") == "insights":
            return {}
        active_plan = self._active_plan(state)
        out_of_scope = state.get("route") == "out_of_scope"
        if out_of_scope:
            audit_span = self._turn_span if self._turn_span is not None else get_current_span()
            audit_span.set_attribute("tap.plan.intent", "out_of_scope")
        step = None if out_of_scope else _require_step(state)
        answered: list[Any] = []

        async def provider(_snapshot: object) -> ProviderResult:
            if out_of_scope:
                answer = await self._steps.refuse_out_of_scope(
                    self._turn.input_snapshot.value, answer_plan=active_plan
                )
            else:
                assert step is not None
                answer = await self._steps.finalize_conversation_answer(
                    step,
                    self._turn.input_snapshot.value,
                    answer_plan=active_plan,
                    authorize=self._require_authorized,
                )
            answered.append(answer)
            citations = (
                await self._conversations.repository.resolve_citations(
                    answer.trace_id, tuple(item.citation_id for item in answer.citations)
                )
                if answer.citations
                else ()
            )
            graph_context = getattr(answer, "graph_context", None)
            return ProviderResult(
                answer=answer.answer,
                graph_context_status=GraphContextStatus(
                    getattr(answer, "graph_context_status", "UNAVAILABLE")
                ),
                graph_snapshot_id=getattr(answer, "graph_snapshot_id", None),
                retrieval_summary=RetrievalSummary(
                    "abstained" if answer.abstained else "completed",
                    trace_id=answer.trace_id,
                    # M-5: `authorized_hit_count`'s meaning is "the number of
                    # authorized evidence hits retrieval produced" (see
                    # `KnowledgeHttpService._retrieval_hits_payload`'s
                    # `len(response.evidence)`, the early `seed_search` path).
                    # This fallback path (no early `retrieval_hits` payload
                    # was available, e.g. `SingleShotAnswerSteps`) has no
                    # evidence count of its own to read here -- citations are
                    # the closest proxy the finalized answer carries, and
                    # every citation always traces back to an authorized hit
                    # -- so this undercounts only when an authorized hit
                    # produced no citation.
                    authorized_hit_count=len(answer.citations),
                ),
                citations=citations,
                abstained=answer.abstained,
                answer_plan_id=None if active_plan is None else active_plan.plan_id,
                graph_context=(
                    None
                    if graph_context is None
                    else graph_context.model_dump(mode="json", by_alias=True)
                ),
            )

        turn_processor = TurnProcessor(
            provider=provider, complete=lambda _evidence: None, reraise=self._retryable
        )
        evidence = await turn_processor.process(self._turn.input_snapshot)
        graph_context_payload = (
            None if turn_processor.last_result is None else turn_processor.last_result.graph_context
        )
        answer_response = answered[0] if answered else None
        terminal_event: dict[str, object] | None
        if evidence.outcome == "failed":
            terminal_event = {
                "type": "turn.failed",
                "payload": {
                    "problem": build_problem(
                        "answer-unavailable", correlation_id=self._turn.turn_id
                    ).model_dump(mode="json", by_alias=True)
                },
            }
        elif answer_response is None:
            terminal_event = None
        else:
            terminal_event = {
                "type": ("turn.abstained" if answer_response.abstained else "turn.completed"),
                "payload": {"answer": answer_response.model_dump(mode="json", by_alias=True)},
            }
        # Wall-clock epoch milliseconds, matching `seed_search`'s
        # `started_at_ms`; clamp at 0 since a resumed run's elapsed time
        # should never read negative (e.g. clock skew across a resume on a
        # different host).
        duration_ms = max(0, int(time.time() * 1000) - int(state.get("started_at_ms") or 0))
        return {
            "result": {
                "evidence": _evidence_checkpoint(evidence, graph_context=graph_context_payload),
                "terminalEvent": terminal_event,
                "streamEvents": stream_events(
                    evidence,
                    duration_ms,
                    turn=self._turn,
                    answer_response=answer_response,
                    graph_context_payload=graph_context_payload,
                    retrieval_performed=not out_of_scope,
                    already_committed=frozenset(state.get("committed_stream_events") or ()),
                ),
            }
        }
