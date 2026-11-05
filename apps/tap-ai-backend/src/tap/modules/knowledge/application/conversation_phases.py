"""Typed answer phases and the v3 checkpoint seam.

Authority is never serialized: a resumed phase receives a fresh execution context.
Required fields distinguish each phase; JSON conversion is confined to this codec.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict, cast

from tap.modules.chat.domain.answer_plan import AnswerPlan
from tap.modules.chat.domain.conversations import TurnInput
from tap.modules.knowledge.application.evidence_bundle import (
    EvidenceBundle,
    decode_bundle,
    encode_bundle,
)
from tap.modules.knowledge.domain.models import (
    AbstentionReason,
    AnswerRequest,
    AnswerResponse,
    Claim,
    ResourceMode,
    ResourceRef,
    SourceFamily,
)
from tap.modules.knowledge.ports.models import AnswerGeneration
from tap.platform.serialization.dataclass_json import from_json, to_json


@dataclass(frozen=True, slots=True)
class ConversationExecutionContext:
    frozen_input: TurnInput
    answer_plan: AnswerPlan | None = None
    authorize: Callable[[], Awaitable[None]] | None = None

    def request(self) -> AnswerRequest:
        """The graph request is derived from the same frozen resources as its authority."""
        return AnswerRequest(
            query=self.frozen_input.message,
            source_families=(SourceFamily.DOC,),
            resource_refs=tuple(
                ResourceRef(family=SourceFamily.DOC, source_id=r.source_id, mode=ResourceMode.SCOPE)
                for r in self.frozen_input.resolved_resources
            ),
        )


@dataclass(frozen=True, slots=True)
class DirectAnswer:
    """A completed non-retrieval answer; no transport model participates in execution."""

    trace_id: str
    query_plan_id: str
    context_snapshot_id: str
    corpus_version: str
    retrieval_profile_id: str
    answer: str
    abstained: bool
    degraded_mode: bool = False
    claims: tuple[Claim, ...] = ()
    citations: tuple[Mapping[str, object], ...] = ()
    abstention_reason: AbstentionReason | None = None
    answer_kind: Literal["answer", "clarification"] = "answer"
    graph_context_status: str = "NOT_SELECTED"
    graph_snapshot_id: str | None = None
    degradation_reasons: tuple[str, ...] = ()
    graph_context: Mapping[str, object] | None = None
    insights_facts: tuple[Mapping[str, object], ...] = ()


class PreparedAnswer(TypedDict):
    kind: Literal["prepared"]
    bundle: EvidenceBundle
    retrieval_hits: NotRequired[dict[str, object]]
    transcript: NotRequired[list[dict[str, object]]]


class GeneratedAnswer(TypedDict):
    kind: Literal["generated"]
    bundle: EvidenceBundle
    generation: AnswerGeneration


class VerifiedAnswer(TypedDict):
    kind: Literal["verified"]
    response: AnswerResponse


class FinalAnswer(TypedDict):
    kind: Literal["final"]
    response: DirectAnswer


class ToolCallsAnswer(TypedDict):
    kind: Literal["tool_calls"]
    bundle: EvidenceBundle
    transcript: list[dict[str, object]]
    pending_tool_calls: list[dict[str, str]]


ConversationAnswerStep = (
    PreparedAnswer | GeneratedAnswer | VerifiedAnswer | FinalAnswer | ToolCallsAnswer
)


class ConversationToolResult(TypedDict):
    step: ConversationAnswerStep
    status: Literal["completed", "failed"]
    summary: str
    added_labels: list[str]
    retrieval_hits: dict[str, object] | None


def encode_conversation_step(step: ConversationAnswerStep) -> dict[str, object]:
    encoded = dict(step)
    if step["kind"] == "prepared" or step["kind"] == "generated" or step["kind"] == "tool_calls":
        encoded["bundle"] = encode_bundle(step["bundle"])
    if step["kind"] == "generated":
        encoded["generation"] = to_json(step["generation"])
    if step["kind"] == "verified":
        encoded["response"] = to_json(step["response"])
    if step["kind"] == "final":
        from tap.modules.knowledge.api import conversation_answer_to_http

        encoded["response"] = conversation_answer_to_http(step["response"]).model_dump(
            mode="json", by_alias=True
        )
    return encoded


def decode_conversation_step(raw: object) -> ConversationAnswerStep:
    """Accept schema-v3 snapshots, reject missing phase payloads before execution."""
    required = {
        "prepared": ("bundle",),
        "generated": ("bundle", "generation"),
        "verified": ("response",),
        "final": ("response",),
        "tool_calls": ("bundle", "transcript", "pending_tool_calls"),
    }
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("kind"), str)
        or raw["kind"] not in required
    ):
        raise ValueError("checkpoint is missing a valid answer step")
    kind = raw["kind"]
    if any(key not in raw for key in required[kind]):
        raise ValueError(f"answer step {kind} lacks required payload")
    step = dict(raw)
    try:
        if "bundle" in required[kind]:
            step["bundle"] = decode_bundle(raw["bundle"])
        if kind == "generated":
            step["generation"] = from_json(AnswerGeneration, raw["generation"])
        if kind == "verified":
            step["response"] = from_json(AnswerResponse, raw["response"])
        if kind == "final":
            step["response"] = decode_direct_answer(raw["response"])
        if kind in {"prepared", "tool_calls"}:
            transcript = raw.get("transcript", [])
            if not isinstance(transcript, list) or not all(
                isinstance(item, Mapping) for item in transcript
            ):
                raise ValueError("invalid transcript")
        if kind == "tool_calls":
            calls = raw["pending_tool_calls"]
            if not isinstance(calls, list) or not all(
                isinstance(call, dict)
                and all(
                    isinstance(call.get(key), str) for key in ("callId", "name", "argumentsJson")
                )
                for call in calls
            ):
                raise ValueError("invalid pending tool calls")
    except (TypeError, KeyError, ValueError, AttributeError) as error:
        raise ValueError(f"invalid answer step {kind} payload") from error
    return cast(ConversationAnswerStep, step)


def decode_direct_answer(raw: object) -> DirectAnswer:
    """Read the historical final-route HTTP-shaped v3 checkpoint at the codec seam."""
    from tap.contracts.http import RetrievalAnswerResponse

    value = RetrievalAnswerResponse.model_validate(raw)
    return DirectAnswer(
        trace_id=value.trace_id,
        query_plan_id=value.query_plan_id,
        context_snapshot_id=value.context_snapshot_id,
        corpus_version=value.corpus_version,
        retrieval_profile_id=value.retrieval_profile_id,
        answer=value.answer,
        abstained=value.abstained,
        degraded_mode=value.degraded_mode,
        abstention_reason=None
        if value.abstention_reason is None
        else AbstentionReason(value.abstention_reason.value),
        answer_kind=value.answer_kind,
        claims=tuple(
            Claim(c.claim_id, c.text, c.answer_start, c.answer_end, tuple(c.citation_ids))
            for c in value.claims
        ),
        citations=tuple(c.model_dump(mode="json", by_alias=True) for c in value.citations),
        graph_context_status=value.graph_context_status,
        graph_snapshot_id=value.graph_snapshot_id,
        degradation_reasons=tuple(value.degradation_reasons or ()),
        graph_context=None
        if value.graph_context is None
        else value.graph_context.model_dump(mode="json", by_alias=True),
        insights_facts=tuple(
            f.model_dump(mode="json", by_alias=True) for f in value.insights_facts or ()
        ),
    )
