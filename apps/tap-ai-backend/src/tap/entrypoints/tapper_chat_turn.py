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

import json
import time
from collections.abc import Awaitable, Callable
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
from tap.modules.ai.application.chat_graph import ChatGraphNodes, ChatGraphState
from tap.modules.ai.ports.insights import InsightsQueryUnavailable
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
from tap.platform.telemetry import current_binding


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
    if evidence.retrieval_summary.trace_id is not None:
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
    if evidence.answer:
        events.append({"type": "answer.delta", "payload": {"text": evidence.answer}})
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
        self, request, frozen_input, *, answer_plan=None, authorize=None
    ) -> ConversationAnswerStep:
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
        self, step, frozen_input, *, answer_plan=None, authorize=None
    ) -> ConversationAnswerStep:
        return step

    async def verify_conversation_answer(
        self, step, frozen_input, *, answer_plan=None, authorize=None
    ) -> ConversationAnswerStep:
        return step

    async def finalize_conversation_answer(
        self, step, frozen_input, *, answer_plan=None, authorize=None
    ):
        return step["response"]


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
    ) -> None:
        self._conversation_id = conversation_id
        self._turn = turn
        self._conversations = conversations
        self._steps = steps
        self._planner = planner
        self._knowledge = knowledge
        self._insights_explanation = insights_explanation
        self._require_authorized = require_authorized
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
            verify=self.verify,
            finalize=self.finalize,
        )

    def _active_plan(self, state: ChatGraphState) -> AnswerPlan | None:
        if self._planner is None:
            return None
        raw = state.get("answer_plan")
        if raw is None:
            return None
        return AnswerPlan.from_dict(raw)

    async def guard_in(self, _state: ChatGraphState) -> dict[str, object]:
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
        return {"route": "chat", "reasoning_mode": "direct", "answer_plan": plan.to_dict()}

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
            request, value, answer_plan=active_plan, authorize=self._require_authorized
        )
        return {"step": step, "started_at_ms": started_at_ms}

    async def model(self, state: ChatGraphState) -> dict[str, object]:
        active_plan = self._active_plan(state)
        step = await self._steps.generate_conversation_answer(
            state["step"],
            self._turn.input_snapshot.value,
            answer_plan=active_plan,
            authorize=self._require_authorized,
        )
        return {"step": step}

    async def verify(self, state: ChatGraphState) -> dict[str, object]:
        active_plan = self._active_plan(state)
        step = await self._steps.verify_conversation_answer(
            state["step"],
            self._turn.input_snapshot.value,
            answer_plan=active_plan,
            authorize=self._require_authorized,
        )
        return {"step": step}

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

    def _failed_result(self) -> dict[str, object]:
        evidence = AnswerEvidence(
            "", "failed", RetrievalSummary("failed"), GraphContextStatus.FAILED
        )
        return {
            "result": {
                "evidence": _evidence_checkpoint(evidence),
                "terminalEvent": {
                    "type": "turn.failed",
                    "payload": {
                        "problem": build_problem(
                            "answer-unavailable", correlation_id=self._turn.turn_id
                        ).model_dump(mode="json", by_alias=True)
                    },
                },
                "streamEvents": [],
            }
        }

    async def finalize(self, state: ChatGraphState) -> dict[str, object]:
        if state.get("failure"):
            return self._failed_result()
        if state.get("route") == "insights":
            return {}
        active_plan = self._active_plan(state)
        step = state["step"]
        answered: list[Any] = []

        async def provider(_snapshot: object) -> ProviderResult:
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

        turn_processor = TurnProcessor(provider=provider, complete=lambda _evidence: None)
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
                ),
            }
        }
