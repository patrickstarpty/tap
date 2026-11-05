"""Conversation answer execution: deadlines, authority, resumable phases and evidence growth.

Only checkpoint codecs serialize phase payloads. HTTP retains document/source/citation
transport; workers and grounding checks consume this application directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import time
from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Any, Protocol
from uuid import uuid4

from opentelemetry.trace import get_current_span

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.ai.domain.skill_activation import PromptSkill, TurnSkillContext
from tap.modules.knowledge.application.conversation_phases import (
    ConversationAnswerStep,
    ConversationExecutionContext,
    ConversationToolResult,
    DirectAnswer,
    PreparedAnswer,
)
from tap.modules.knowledge.domain.models import AbstentionReason, AnswerRequest, AnswerResponse
from tap.modules.knowledge.ports.redaction import RedactionUnavailable


class AnswerBudgetExhausted(TimeoutError):
    """The answer plan's persisted deadline passed before this phase could run."""


def _anchor_location(anchor: object) -> str | None:
    """A short human-readable location for one evidence item's source
    anchor, for `retrieval.hits_ready`'s `labels` (spec SS2.3: "label ->
    source title/location"); duck-typed so any closed `StructuralAnchor`
    variant without a heading path or page (e.g. a code anchor) safely
    yields `None` instead of a location."""
    heading_path = getattr(anchor, "heading_path", None)
    if heading_path:
        return " / ".join(heading_path)
    page = getattr(anchor, "page", None)
    if page is not None:
        return f"第 {page} 页"
    return None


def _retrieval_hits_payload(response: Any, relation: Any | None) -> dict[str, object]:
    """`retrieval.hits_ready`'s payload, built from the just-prepared
    (not-yet-encoded) `SearchResponse`/`RelationContext` -- one label entry
    per `S`-labeled evidence chunk and per `R`-labeled relation, each mapped
    to a source title and, when available, a location."""
    labels: list[dict[str, object]] = [
        {
            "label": item.evidence_label,
            "title": item.title or item.source.source_id,
            "location": _anchor_location(item.source.anchor),
        }
        for item in response.evidence
    ]
    if relation is not None:
        labels.extend(
            {
                "label": item.label,
                "title": f"{item.subject_label} {item.relation_label} {item.object_label}",
                "location": None,
            }
            for item in relation.relations
        )
    return {
        "traceId": response.trace_id,
        # M-5: the number of authorized evidence hits retrieval produced
        # (not citations, which are resolved later and may be fewer) --
        # `ChatTurnNodes.finalize`'s fallback path (no early `retrieval_hits`
        # payload) uses the same meaning where it has the data for it,
        # falling back to the closest proxy it carries otherwise.
        "authorizedHitCount": len(response.evidence),
        "labels": labels,
    }


def _tool_call_summary(tool_id: str, query: str, labels: tuple[str, ...], *, unit: str) -> str:
    """`run_conversation_tool`'s `summary` text: `tool_id “<query>” → +N
    条<unit>（<first label>–<last label>）`, or `→ 无新<unit>` when nothing new
    was admitted into the pool (already full, or every candidate deduped
    against an existing label). `query` is already redacted and bounded by
    `ConversationAnswerExecutor._summary_query` (final review M-2)."""
    prefix = f"{tool_id} “{query}”"
    if not labels:
        return f"{prefix} → 无新{unit}"
    label_range = labels[0] if labels[0] == labels[-1] else f"{labels[0]}–{labels[-1]}"
    return f"{prefix} → +{len(labels)} 条{unit}（{label_range}）"


def turn_skill_context(
    frozen_input: Any, activated: tuple[PromptSkill, ...], *, governed: bool
) -> TurnSkillContext | None:
    """The Skills one model call renders outside its generation governance,
    plus the Turn's frozen candidate catalog (Skill Hub spec 1.2/1.3).

    A governed Turn's governance already renders its frozen (manual/pinned)
    Skill blocks, so only the Skills `skill.activate` loaded are added. An
    ungoverned (Tapper-default) Turn has no governance, so its frozen Skills
    are rendered here too, ahead of the activated ones; a frozen Skill is
    manual when the caller requested its revision, otherwise pinned.
    `TurnInput` freezes revision identities only, so a frozen Skill's
    `skill_id` is its revision id. `None` when there is nothing to render."""

    frozen: tuple[PromptSkill, ...] = ()
    if not governed:
        names = frozen_input.skill_labels or frozen_input.skill_revision_ids
        requested = frozenset(frozen_input.requested_skill_revision_ids)
        frozen = tuple(
            PromptSkill(
                skill_id=revision_id,
                revision_id=revision_id,
                name=name,
                instruction=instruction,
                mode="manual" if revision_id in requested else "pinned",
            )
            for revision_id, name, instruction in zip(
                frozen_input.skill_revision_ids,
                names,
                frozen_input.skill_instruction_templates,
                strict=False,
            )
        )
    blocks = (*frozen, *activated)
    catalog = tuple(frozen_input.skill_catalog)
    if not blocks and not catalog:
        return None
    return TurnSkillContext(catalog=catalog, blocks=blocks)


class AnswerOperations(Protocol):
    @property
    def scope(self) -> ProjectScopeContext: ...

    async def answer(self, request: AnswerRequest) -> AnswerResponse: ...

    async def answer_frozen(self, *args, **kwargs) -> AnswerResponse: ...

    async def prepare_frozen(self, *args, **kwargs): ...

    async def generate_frozen(self, *args, **kwargs): ...

    async def verify_frozen(self, *args, **kwargs) -> AnswerResponse: ...

    async def save_frozen(self, *args, **kwargs) -> None: ...

    async def authorize_frozen_selection(self, revisions) -> None: ...

    async def resolve_conversation_selection(self, revision_ids): ...

    async def search_more_frozen(self, *args, **kwargs): ...

    async def relations_frozen(self, *args, **kwargs): ...


class ConversationAnswerExecutor:
    """Own a complete answer or resume its graph-internal phases with fresh authority."""

    def __init__(
        self,
        *,
        answers: AnswerOperations,
        citations: Any,
        corpus_version: str = "tapper-demo-v1",
        models: Any = None,
        answer_planner: Any = None,
        redactor: Any = None,
        relations_available: bool = False,
    ) -> None:
        if corpus_version not in {"tapper-demo-v1", "tapper-demo-v2"}:
            raise ValueError("unsupported projection corpus")
        self._answers = answers
        self._citations = citations
        self._corpus_version = corpus_version
        self._models = models
        self.answer_planner = answer_planner
        self._redactor = redactor
        self.relations_available = relations_available

    @property
    def scope(self) -> ProjectScopeContext:
        scope = getattr(self._answers, "scope", None)
        if not isinstance(scope, ProjectScopeContext) or self._citations.scope != scope:
            raise AuthorizationDenied("scope-mismatch")
        return scope

    async def resolve_conversation_selection(self, revision_ids: tuple[str, ...]):
        resolver = getattr(self._answers, "resolve_conversation_selection", None)
        if resolver is None:
            from tap.modules.knowledge.ports.errors import KnowledgeRuntimeUnavailable

            raise KnowledgeRuntimeUnavailable
        return await resolver(revision_ids)

    async def _summary_query(self, query: str) -> str:
        """The tool query as shown in a `task.step` summary: redacted first,
        then cut to 60 characters (final review M-2) -- cutting first could
        split an ID/email into a fragment no redaction pattern matches. The
        caller (`ChatTurnNodes.tools`) redacts the whole summary again with
        the v2 chat egress rules; a redaction failure here degrades to a
        content-free placeholder rather than failing the tool call."""
        report = getattr(self._redactor, "redact_with_report", None)
        if report is None:
            return query[:60]
        try:
            redacted, _hits = await report(query)
        except RedactionUnavailable:
            return "内容已隐去"
        return redacted[:60]

    async def redaction_report(self, text: str) -> Mapping[str, int]:
        """Egress-redaction hit counts for `text`, by pattern kind; empty
        when no redactor is composed or the redactor exposes no report."""
        report = getattr(self._redactor, "redact_with_report", None)
        if report is None:
            return {}
        _sanitized, counts = await report(text)
        return counts

    async def answer_conversation(
        self, request: AnswerRequest, context: ConversationExecutionContext
    ) -> AnswerResponse | DirectAnswer:
        # Defence in depth (M4): this is also called directly by
        # prompt-suggestion grounding outside the `tapper-chat-v2` graph,
        # which never runs `guard_in`'s own out-of-scope routing. Refuse
        # here too, before any retrieval or generation call.
        frozen_input, answer_plan = context.frozen_input, context.answer_plan
        if answer_plan is not None and answer_plan.intent == "out_of_scope":
            return await self.refuse_out_of_scope(frozen_input, answer_plan=answer_plan)
        async with self._plan_deadline(answer_plan):
            return await self._answer_conversation(request, context)

    @staticmethod
    @contextlib.asynccontextmanager
    async def _plan_deadline(answer_plan):
        """One shared deadline guard used by `answer_conversation` and every
        resumable phase below: raises `AnswerBudgetExhausted` (a `TimeoutError`
        subclass) immediately if the plan's persisted deadline has already
        passed (before any work, including
        `_authorized_frozen_context`), else returns an `asyncio.timeout`
        context scoped to the time left. `answer_plan.deadline_at` is an
        absolute timestamp, so recomputing it fresh at the start of every
        phase -- rather than passing a single captured budget along -- keeps
        nested uses of this guard safe: each nested call only ever tightens
        the remaining time, never resets it.

        Expiring mid-phase also raises `AnswerBudgetExhausted` (capability-4
        fix round 1): inside a Turn the plan window ends with the Turn's
        wall-clock budget, so a bare `TimeoutError` here would be retried as
        transient instead of failing as `budget-exhausted`."""
        remaining = None if answer_plan is None else answer_plan.deadline_at - time.time()
        if remaining is not None and remaining <= 0:
            raise AnswerBudgetExhausted("persisted answer budget exhausted")
        timeout_cm = asyncio.timeout(remaining)
        try:
            async with timeout_cm:
                yield
        except TimeoutError:
            if timeout_cm.expired():
                raise AnswerBudgetExhausted("persisted answer budget exhausted") from None
            raise

    async def authorize_planning(self, snapshot) -> None:
        from tap.modules.knowledge.ports.answers import ReadyDocumentRevision

        if snapshot.project_id != self.scope.project_id:
            raise AuthorizationDenied("scope-mismatch")
        if snapshot.value.resolved_resources:
            await self._answers.authorize_frozen_selection(
                tuple(
                    ReadyDocumentRevision(
                        item.document_id, item.revision_id, item.source_content_hash, item.source_id
                    )
                    for item in snapshot.value.resolved_resources
                )
            )

    async def authorize_completed_answer(self, snapshot, evidence) -> None:
        """Reopen trusted authority for a checkpointed answer before delivery."""
        from tap.modules.chat.domain.conversations import content_digest

        if snapshot.project_id != self.scope.project_id:
            raise AuthorizationDenied("scope-mismatch")
        frozen = snapshot.value.resolved_resources
        if frozen:
            try:
                current, policy = await self._answers.resolve_conversation_selection(
                    tuple(item.revision_id for item in frozen)
                )
                if {
                    (item.source_id, item.document_id, item.revision_id, item.source_content_hash)
                    for item in current
                } != {
                    (item.source_id, item.document_id, item.revision_id, item.source_content_hash)
                    for item in frozen
                }:
                    raise AuthorizationDenied("frozen revision selection changed")
                if (
                    snapshot.value.acl_digest != policy.acl_digest
                    or snapshot.value.retrieval_policy_digest
                    != content_digest(
                        {
                            "decisionId": policy.decision_id,
                            "policyVersion": policy.policy_version,
                            "corpusVersion": policy.active_corpus_version,
                        }
                    )
                ):
                    raise AuthorizationDenied("frozen retrieval policy changed")
                await self._answers.authorize_frozen_selection(current)
            except asyncio.CancelledError:
                raise
            except AuthorizationDenied:
                raise
            except Exception as error:
                raise AuthorizationDenied(
                    "checkpoint selection authorization unavailable"
                ) from error
        elif evidence.citations:
            raise AuthorizationDenied("citation is outside frozen resources")
        for citation in evidence.citations:
            try:
                await self._citations.authorize_current(citation.citation_snapshot_id)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                raise AuthorizationDenied("checkpoint citation authorization changed") from error

    async def _answer_conversation(
        self, request: AnswerRequest, context: ConversationExecutionContext
    ) -> AnswerResponse | DirectAnswer:
        """The reference composition of the four resumable phases below,
        inside the one persisted-deadline guard `answer_conversation` opened."""
        step = await self.prepare_conversation_answer(request, context)
        step = await self.generate_conversation_answer(step, context)
        step = await self.verify_conversation_answer(step, context)
        return await self.finalize_conversation_answer(step, context)

    async def prepare_conversation_answer(
        self,
        request: AnswerRequest,
        context: ConversationExecutionContext,
        *,
        include_relations: bool = True,
    ) -> ConversationAnswerStep:
        """Resumable prepare phase: retrieval (or a non-retrieval route's
        complete answer) only, re-deriving and re-checking authority from
        `frozen_input`, bounded by the plan's persisted deadline from the
        start -- including the non-retrieval model call, which otherwise has
        no time limit of its own. `include_relations=False` (Task 9's
        `seed_search` graph node call) skips relation analysis inline --
        `graph.relations` runs it later, as a tool call."""
        frozen_input, answer_plan, authorize = (
            context.frozen_input,
            context.answer_plan,
            context.authorize,
        )
        async with self._plan_deadline(answer_plan):
            authority = await self._authorized_frozen_context(
                original_question=request.query,
                frozen_input=frozen_input,
                answer_plan=answer_plan,
                authorize=authorize,
            )
            if isinstance(authority, DirectAnswer):
                return {
                    "kind": "final",
                    "response": authority,
                }
            revisions, policy, governance = authority
            answer_execution = self._execution(answer_plan) if answer_plan is not None else None

            domain_request = request
            prepared = await self._answers.prepare_frozen(
                domain_request,
                revisions,
                policy,
                governance=governance,
                answer_execution=answer_execution,
                authorize=authorize,
                include_relations=include_relations,
            )
            if isinstance(prepared, AnswerResponse):
                return {"kind": "verified", "response": prepared}
            return {
                "kind": "prepared",
                "bundle": prepared,
                # PR 3: the `seed_search` graph node emits `retrieval.hits_ready`
                # as soon as this step is produced -- before the `model` node
                # ever runs -- using exactly this payload, built here while the
                # (not-yet-encoded) evidence/relation context is still at hand.
                "retrieval_hits": _retrieval_hits_payload(prepared.response, prepared.relation),
            }

    async def generate_conversation_answer(
        self,
        step: ConversationAnswerStep,
        context: ConversationExecutionContext,
        *,
        on_text=None,
        tools: tuple[str, ...] = (),
        activated_skills: tuple[PromptSkill, ...] = (),
    ) -> ConversationAnswerStep:
        """Resumable generate phase: only acts on a `"prepared"` step,
        re-authorizing from `frozen_input` before the model call, bounded by
        the plan's persisted deadline from the start; any other step is
        returned unchanged. `on_text` (PR 3) is the `model` graph node's
        draft-streaming callback, passed through to the knowledge gateway's
        streaming model call; omitted entirely when `None` so a
        `generate_frozen` double that does not know about it is unaffected.
        `tools` (PR 5 Task 7) are the tool IDs offered this round; when the
        model asks to call one, this returns a `"tool_calls"` step carrying
        `pending_tool_calls` and the extended `transcript` instead of a
        `"generated"` step -- the model's draft is discarded unparsed
        (`AnswerGeneration.text` stays `""`). `activated_skills` (Skill Hub)
        are the Skills `skill.activate` loaded so far this Turn; with the
        frozen Skills and catalog they reach the gateway as `skills` (see
        `turn_skill_context`), omitted when there is nothing to render."""
        frozen_input, answer_plan, authorize = (
            context.frozen_input,
            context.answer_plan,
            context.authorize,
        )
        async with self._plan_deadline(answer_plan):
            if step["kind"] != "prepared":
                return step
            answer_execution = self._execution(answer_plan) if answer_plan is not None else None
            from tap.modules.ai.domain.persona import persona_for

            authority = await self._authorized_frozen_context(
                original_question=frozen_input.message,
                frozen_input=frozen_input,
                answer_plan=answer_plan,
                authorize=authorize,
            )
            if isinstance(authority, DirectAnswer):
                raise ValueError("resumed step no longer matches a retrieval route")
            revisions, policy, governance = authority
            bundle = step["bundle"]
            transcript = step.get("transcript", [])
            skills = turn_skill_context(
                frozen_input, activated_skills, governed=governance is not None
            )
            generation = await self._answers.generate_frozen(
                bundle,
                revisions,
                policy,
                governance=governance,
                model_alias=frozen_input.model_alias,
                answer_execution=answer_execution,
                authorize=authorize,
                persona=persona_for(frozen_input.persona_version),
                **({"on_text": on_text} if on_text is not None else {}),
                **({"tools": tools} if tools else {}),
                **({"transcript": tuple(transcript)} if transcript else {}),
                **({"skills": skills} if skills is not None else {}),
            )
            if generation.tool_calls:
                pending_tool_calls = [
                    {
                        "callId": call.call_id,
                        "name": call.name,
                        "argumentsJson": call.arguments_json,
                    }
                    for call in generation.tool_calls
                ]
                return {
                    "kind": "tool_calls",
                    "bundle": step["bundle"],
                    "transcript": [
                        *transcript,
                        {"role": "assistant", "toolCalls": pending_tool_calls},
                    ],
                    "pending_tool_calls": pending_tool_calls,
                }
            return {
                "kind": "generated",
                "bundle": step["bundle"],
                "generation": generation,
            }

    async def verify_conversation_answer(
        self, step: ConversationAnswerStep, context: ConversationExecutionContext
    ) -> ConversationAnswerStep:
        """Resumable verify phase: only acts on a `"generated"` step,
        re-authorizing from `frozen_input` before verification, bounded by
        the plan's persisted deadline from the start; any other step is
        returned unchanged."""
        frozen_input, answer_plan, authorize = (
            context.frozen_input,
            context.answer_plan,
            context.authorize,
        )
        async with self._plan_deadline(answer_plan):
            if step["kind"] != "generated":
                return step
            answer_execution = self._execution(answer_plan) if answer_plan is not None else None

            authority = await self._authorized_frozen_context(
                original_question=frozen_input.message,
                frozen_input=frozen_input,
                answer_plan=answer_plan,
                authorize=authorize,
            )
            if isinstance(authority, DirectAnswer):
                raise ValueError("resumed step no longer matches a retrieval route")
            revisions, policy, _governance = authority
            bundle = step["bundle"]
            generation = step["generation"]
            response = await self._answers.verify_frozen(
                bundle,
                generation,
                revisions,
                policy,
                answer_execution=answer_execution,
                authorize=authorize,
            )
            return {"kind": "verified", "response": response}

    async def finalize_conversation_answer(
        self, step: ConversationAnswerStep, context: ConversationExecutionContext
    ) -> AnswerResponse | DirectAnswer:
        """Resumable finalize phase: a `"final"` step returns its completed answer; a
        `"verified"` step (from `verify`, or an abstaining `prepare`) is
        persisted through `save_frozen` before delivery conversion -- all bounded
        by the plan's persisted deadline from the start (`save_frozen` itself
        takes no execution-budget parameter)."""
        frozen_input, answer_plan, authorize = (
            context.frozen_input,
            context.answer_plan,
            context.authorize,
        )
        async with self._plan_deadline(answer_plan):
            if step["kind"] == "final":
                final_response = step["response"]
                return await self._redact_answer_response(final_response)
            if step["kind"] != "verified":
                raise ValueError("conversation answer step is not ready to finalize")

            authority = await self._authorized_frozen_context(
                original_question=frozen_input.message,
                frozen_input=frozen_input,
                answer_plan=answer_plan,
                authorize=authorize,
            )
            if isinstance(authority, DirectAnswer):
                raise ValueError("resumed step no longer matches a retrieval route")
            revisions, _policy, _governance = authority
            response = step["response"]
            # Fail-closed: if `report()` raises `RedactionUnavailable` (e.g. an
            # unterminated credential value, or the answer exceeds the
            # redactor's bound), it propagates out of `finalize` uncaught --
            # nothing before `verify` is checked, so the turn must not save or
            # return an unredacted answer. `classify_turn_failure` treats it as
            # deterministic (not retryable).
            response = await self._redact_answer_response(response)
            await self._answers.save_frozen(response, frozen_input.message, revisions)
            return response

    @staticmethod
    def _locate_redacted_claim(
        redacted_answer: str,
        redacted_claim_text: str,
        search_from: int,
        anchor: int | None,
    ) -> tuple[int, int] | None:
        """The span of `redacted_claim_text` in `redacted_answer`, or None.

        PR 3 claims are cited sentences inside paragraphs, so redaction that
        changes the answer's length leaves every original offset stale. The
        claim is kept only when its redacted text has exactly one span in the
        *whole* redacted answer -- the same rule `_validate_gateway_response`
        and the HTTP contract apply. Uniqueness only after the previous claim
        is not enough: an uncited sentence earlier in the answer that redacts
        to the same text would make those whole-answer validators reject the
        entire answer. That span must also start at or after the previous
        claim's end and, when `anchor` (the claim's own position in the
        redacted answer) is known, start exactly there, so a claim is never
        re-pointed at a different sentence that merely redacts alike. A
        missing, ambiguous or misplaced match drops only this claim."""
        if not redacted_claim_text:
            return None
        spans: list[tuple[int, int]] = []
        found = redacted_answer.find(redacted_claim_text)
        while found != -1 and len(spans) < 2:
            spans.append((found, found + len(redacted_claim_text)))
            found = redacted_answer.find(redacted_claim_text, found + len(redacted_claim_text))
        if len(spans) != 1:
            return None
        start, end = spans[0]
        if start < search_from or (anchor is not None and start != anchor):
            return None
        return start, end

    async def _redacted_claim_spans(self, report, answer: str, redacted_answer: str, claims):
        """Each claim that stays locatable after redaction, as `(claim,
        redacted_text, start, end)` in answer order (see
        `_locate_redacted_claim`). The claim's anchor is the length of its
        redacted answer prefix when that prefix is literally the start of the
        redacted answer (redaction did not cross the claim boundary); claim
        and prefix hit counts are not recorded -- both are slices of the
        answer whose hits are counted once by the caller.

        The prefix is only an anchoring aid: a prefix can end right after a
        credential key (`password=`), inside an open quoted secret or inside a
        PEM block, so the redactor rejects it (`RedactionUnavailable`) even
        though the whole answer redacted cleanly. Such a claim has no anchor
        and is located by the uniqueness and ordering checks alone; a claim
        text that cannot be redacted still fails closed.

        Every claim that is not kept is counted on the current span as
        `tap.guard.redaction.dropped_claims` (only when nonzero, like the
        output hits), so a drop is observable."""
        located_claims = []
        search_from = 0
        for claim in claims:
            redacted_text, _claim_hits = await report(claim.text)
            try:
                redacted_prefix, _prefix_hits = await report(answer[: claim.answer_start])
            except RedactionUnavailable:
                anchor = None
            else:
                anchor = (
                    len(redacted_prefix) if redacted_answer.startswith(redacted_prefix) else None
                )
            located = self._locate_redacted_claim(
                redacted_answer, redacted_text, search_from, anchor
            )
            if located is None:
                continue
            search_from = located[1]
            located_claims.append((claim, redacted_text, *located))
        dropped = len(claims) - len(located_claims)
        if dropped:
            get_current_span().set_attribute("tap.guard.redaction.dropped_claims", dropped)
        return located_claims

    async def _redact_answer_response[T: (AnswerResponse, DirectAnswer)](self, response: T) -> T:
        """Redact `response.answer` and every claim's text with the same
        egress redactor before the frozen snapshot is built/saved, so a
        claim's text stays an exact (now-redacted) substring of the (now-
        redacted) answer. The abstained path has an empty answer, so there
        is nothing to redact. Only the answer-body hit counts land on the
        current span -- a claim is always a verbatim slice of the answer,
        so counting its hits too would double-count the same match; no
        hits means nothing is written."""
        report = getattr(self._redactor, "redact_with_report", None)
        if report is None or not response.answer:
            return response
        redacted_answer, hits = await report(response.answer)

        new_claims = [
            dataclasses.replace(
                claim, text=redacted_text, answer_start=claim_start, answer_end=claim_end
            )
            for claim, redacted_text, claim_start, claim_end in await self._redacted_claim_spans(
                report, response.answer, redacted_answer, response.claims
            )
        ]

        if hits:
            get_current_span().set_attribute("tap.guard.redaction.output_hits", json.dumps(hits))
        return dataclasses.replace(response, answer=redacted_answer, claims=tuple(new_claims))

    async def _authorized_frozen_context(
        self,
        *,
        original_question: str,
        frozen_input,
        answer_plan=None,
        authorize=None,
    ):
        """Re-derive revisions, governance and the `RetrievalPolicyContext`
        from `frozen_input` and re-check their digests; every resumable phase
        calls this independently, which also serves as its authorization
        recheck on resume. Returns either `(revisions, policy, governance)`
        for a route that still requires retrieval, or a complete
        `DirectAnswer` for a route that never reaches it."""
        from tap.modules.ai.domain.persona import persona_for
        from tap.modules.chat.domain.conversations import content_digest
        from tap.modules.knowledge.application.demo_policy import build_demo_policy_context
        from tap.modules.knowledge.ports.answers import ReadyDocumentRevision

        persona = persona_for(frozen_input.persona_version)
        revisions = tuple(
            sorted(
                (
                    ReadyDocumentRevision(
                        item.document_id,
                        item.revision_id,
                        item.source_content_hash,
                        item.source_id,
                    )
                    for item in frozen_input.resolved_resources
                ),
                key=lambda item: item.document_id,
            )
        )
        governance = self._generation_governance(frozen_input)
        answer_input = None
        if answer_plan is not None:
            from tap.modules.chat.domain.answer_plan import PlanningInput

            answer_plan.validate_binding(
                PlanningInput(
                    project_id=self.scope.project_id,
                    turn_id=answer_plan.turn_id,
                    input_digest=content_digest(
                        frozen_input.material(
                            project_id=self.scope.project_id, turn_id=answer_plan.turn_id
                        )
                    ),
                    acl_digest=frozen_input.acl_digest,
                    policy_digest=frozen_input.retrieval_policy_digest,
                    original_question=original_question,
                    model_alias=frozen_input.model_alias,
                    source_ids=tuple(item.source_id for item in frozen_input.resolved_resources),
                )
            )
            if authorize is not None:
                await authorize()
            if answer_plan.route not in {"retrieve", "graph"}:
                from tap.modules.knowledge.application.answer_templates import assemble_answer

                answer_input = assemble_answer(
                    template_id=answer_plan.template_id,
                    template_version=answer_plan.template_version,
                    template_digest=answer_plan.template_digest,
                    original_question=answer_plan.original_question,
                    standalone_question=answer_plan.standalone_query,
                    evidence_map={},
                    output_requirements=answer_plan.output_requirements,
                    missing_fields=answer_plan.missing,
                )
        if not revisions:
            if self._models is None:
                raise ValueError("model-only conversation runtime is unavailable")
            expected_acl = content_digest({"mode": "model-only", "resources": []})
            expected_policy = content_digest({"mode": "model-only", "retrieval": "not-selected"})
            if (
                frozen_input.source_revision_ids
                or frozen_input.document_revision_ids
                or frozen_input.acl_digest != expected_acl
                or frozen_input.retrieval_policy_digest != expected_policy
            ):
                raise ValueError("accepted model-only authority changed")
            if answer_plan is not None and answer_plan.route != "direct":
                return self._stopped_answer(answer_plan, answer_input)
            # A direct answer offers no tools, so this renders only the Turn's
            # frozen Skills (none extra for a governed Turn) and no catalog.
            skills = turn_skill_context(frozen_input, (), governed=governance is not None)
            generation = await self._models.chat(
                original_question,
                model_alias=frozen_input.model_alias,
                governance=governance,
                persona=persona,
                **(
                    {"answer_plan_id": answer_plan.plan_id, "answer_input": answer_input}
                    if answer_plan is not None
                    else {}
                ),
                **({"skills": skills} if skills is not None else {}),
            )
            if authorize is not None:
                await authorize()
            identity = generation.gateway_call_id or generation.provider_request_id or uuid4().hex
            return DirectAnswer(
                trace_id=identity,
                query_plan_id=f"model-only-{identity}",
                context_snapshot_id=f"model-only-{identity}",
                corpus_version=self._corpus_version,
                retrieval_profile_id="direct-chat-v1",
                degraded_mode=False,
                answer=generation.text,
                abstained=False,
                claims=(),
                citations=(),
                graph_context_status="NOT_SELECTED",
            )
        policy = build_demo_policy_context(revisions, corpus_version=self._corpus_version)
        if frozen_input.acl_digest != policy.acl_digest or frozen_input.retrieval_policy_digest != (
            content_digest(
                {
                    "decisionId": policy.decision_id,
                    "policyVersion": policy.policy_version,
                    "corpusVersion": policy.active_corpus_version,
                }
            )
        ):
            raise ValueError("accepted retrieval authority changed")
        if answer_plan is not None and answer_plan.route not in {"retrieve", "graph"}:
            await self._answers.authorize_frozen_selection(revisions)
            if answer_plan.route != "direct":
                return self._stopped_answer(answer_plan, answer_input)
            if self._models is None:
                raise ValueError("direct answer model is unavailable")
            skills = turn_skill_context(frozen_input, (), governed=governance is not None)
            generation = await self._models.chat(
                original_question,
                model_alias=frozen_input.model_alias,
                governance=governance,
                answer_plan_id=answer_plan.plan_id,
                answer_input=answer_input,
                persona=persona,
                **({"skills": skills} if skills is not None else {}),
            )
            if authorize is not None:
                await authorize()
            return DirectAnswer(
                trace_id=answer_plan.plan_id,
                query_plan_id=answer_plan.plan_id,
                context_snapshot_id=answer_plan.plan_id,
                corpus_version=self._corpus_version,
                retrieval_profile_id="direct-chat-v1",
                degraded_mode=False,
                answer=generation.text,
                abstained=False,
                claims=(),
                citations=(),
                graph_context_status="NOT_SELECTED",
            )
        return revisions, policy, governance

    @staticmethod
    def _execution(plan):
        from tap.modules.chat.application.plan_answer import authorized_execution

        return authorized_execution(plan)

    def _stopped_answer(self, plan, answer_input):
        if plan.route in {"insights", "file_analysis"}:
            answer = "所需的指标或完整文件分析能力尚不可用，无法提供可靠的统计结果。"
        else:
            answer = answer_input.context["clarificationQuestion"] or "请补充回答所需的信息。"
        return DirectAnswer(
            trace_id=plan.plan_id,
            query_plan_id=plan.plan_id,
            context_snapshot_id=plan.plan_id,
            corpus_version=self._corpus_version,
            retrieval_profile_id="direct-chat-v1",
            degraded_mode=False,
            answer=answer,
            abstained=False,
            answer_kind="clarification" if plan.route == "clarify" else "answer",
            claims=(),
            citations=(),
            graph_context_status="NOT_SELECTED",
        )

    async def refuse_out_of_scope(self, frozen_input, *, answer_plan) -> DirectAnswer:
        return DirectAnswer(
            trace_id=answer_plan.plan_id,
            query_plan_id=answer_plan.plan_id,
            context_snapshot_id=answer_plan.plan_id,
            corpus_version=self._corpus_version,
            retrieval_profile_id="direct-chat-v1",
            degraded_mode=False,
            answer="",
            abstained=True,
            abstention_reason=AbstentionReason.OUT_OF_SCOPE,
            claims=(),
            citations=(),
            graph_context_status="NOT_SELECTED",
        )

    async def run_conversation_tool(
        self, step: ConversationAnswerStep, context: ConversationExecutionContext, call
    ) -> ConversationToolResult:
        """Grow a `"prepared"` step's evidence pool through one admitted
        `knowledge.search`/`graph.relations` tool call: re-authorize from
        `frozen_input` (same recheck every other resumable phase performs),
        then delegate to `search_more_frozen`/`relations_frozen`.

        `SearchUnavailable`/`ModelUnavailable` propagate uncaught (transient,
        PR 2's retry policy owns them); `RelationAnalysisUnavailable`,
        `RelationGraphChanged`, and a relation result that never reached
        `APPLIED` are soft failures -- the step/pool is returned unchanged
        and `status="failed"`, so the turn can keep going instead of failing
        outright over one tool call."""
        frozen_input, answer_plan, authorize = (
            context.frozen_input,
            context.answer_plan,
            context.authorize,
        )
        from tap.modules.knowledge.application.relation_analysis import RelationGraphChanged
        from tap.modules.knowledge.application.retrieve import RelationAnalysisUnavailable
        from tap.modules.knowledge.domain.models import RelationContextStatus

        if step["kind"] != "prepared" and step["kind"] != "tool_calls":
            raise ValueError("conversation tool requires prepared evidence")
        if call.tool_id not in {"knowledge.search", "graph.relations"}:
            raise ValueError(f"run_conversation_tool does not support {call.tool_id!r}")

        async with self._plan_deadline(answer_plan):
            authority = await self._authorized_frozen_context(
                original_question=frozen_input.message,
                frozen_input=frozen_input,
                answer_plan=answer_plan,
                authorize=authorize,
            )
            if isinstance(authority, DirectAnswer):
                raise ValueError("resumed step no longer matches a retrieval route")
            revisions, policy, _governance = authority
            answer_execution = self._execution(answer_plan) if answer_plan is not None else None
            bundle = step["bundle"]
            query = str(call.arguments.get("query", ""))
            summary_query = await self._summary_query(query)
            transcript = step.get("transcript", [])

            if call.tool_id == "knowledge.search":
                source_ids = tuple(call.arguments.get("sourceIds") or ())
                new_bundle, added = await self._answers.search_more_frozen(
                    bundle,
                    query,
                    source_ids,
                    revisions,
                    policy,
                    answer_execution=answer_execution,
                    authorize=authorize,
                )
                added_labels = [item.evidence_label for item in added]
                retrieval_hits = _retrieval_hits_payload(
                    SimpleNamespace(evidence=added, trace_id=new_bundle.response.trace_id), None
                )
                return {
                    "step": {
                        "kind": "prepared",
                        "bundle": new_bundle,
                        "transcript": transcript,
                    },
                    "status": "completed",
                    "summary": _tool_call_summary(
                        "knowledge.search", summary_query, tuple(added_labels), unit="证据"
                    ),
                    "added_labels": added_labels,
                    "retrieval_hits": retrieval_hits,
                }

            # graph.relations
            try:
                new_bundle, added_relations = await self._answers.relations_frozen(
                    bundle,
                    query,
                    revisions,
                    policy,
                    authorize=authorize,
                    turn_id=None if answer_plan is None else answer_plan.turn_id,
                    answer_execution=answer_execution,
                )
            except (RelationAnalysisUnavailable, RelationGraphChanged):
                return {
                    "step": step,
                    "status": "failed",
                    "summary": f'graph.relations "{summary_query}" → 关系分析不可用',
                    "added_labels": [],
                    "retrieval_hits": None,
                }
            if (
                new_bundle.relation is None
                or new_bundle.relation.status is not RelationContextStatus.APPLIED
            ):
                return {
                    "step": step,
                    "status": "failed",
                    "summary": f'graph.relations "{summary_query}" → 无新关系',
                    "added_labels": [],
                    "retrieval_hits": None,
                }
            added_labels = [relation.label for relation in added_relations]
            existing_identity = {
                (item.source.source_id, item.source.revision, item.chunk_id)
                for item in bundle.response.evidence
            }
            added_evidence = tuple(
                item
                for item in new_bundle.response.evidence
                if (item.source.source_id, item.source.revision, item.chunk_id)
                not in existing_identity
            )
            retrieval_hits = _retrieval_hits_payload(
                SimpleNamespace(evidence=added_evidence, trace_id=new_bundle.response.trace_id),
                SimpleNamespace(relations=added_relations) if added_relations else None,
            )
            return {
                "step": {
                    "kind": "prepared",
                    "bundle": new_bundle,
                    "transcript": transcript,
                },
                "status": "completed",
                "summary": _tool_call_summary(
                    "graph.relations", summary_query, tuple(added_labels), unit="关系"
                ),
                "added_labels": added_labels,
                "retrieval_hits": retrieval_hits,
            }

    async def add_insights_records(
        self, step: ConversationAnswerStep, records: Sequence[Mapping[str, object]]
    ) -> tuple[PreparedAnswer, list[str]]:
        """Grow a `"prepared"` step's evidence pool with `insights.query`
        metric records (Task 8): dedupe by `(queryId, metricId)` against
        what the pool already has, then continue the `I<n>` label sequence
        -- same discipline as `knowledge.search`/`graph.relations`, never
        relabeling an existing record. Capped at 20 records, same as S/R."""

        if step["kind"] != "prepared" and step["kind"] != "tool_calls":
            raise ValueError("insights records require prepared evidence")
        bundle = step["bundle"]
        existing_records = list(bundle.insights_records)
        existing_keys = {
            (str(item.get("queryId")), str(item.get("metricId"))) for item in existing_records
        }
        max_label = 0
        for item in existing_records:
            label = str(item.get("label", ""))
            if label.startswith("I") and label[1:].isdigit():
                max_label = max(max_label, int(label[1:]))

        added_labels: list[str] = []
        for record in records:
            key = (str(record.get("queryId")), str(record.get("metricId")))
            if key in existing_keys or len(existing_records) >= 20:
                continue
            max_label += 1
            label = f"I{max_label}"
            labeled = {**dict(record), "label": label}
            existing_records.append(labeled)
            existing_keys.add(key)
            added_labels.append(label)

        new_bundle = dataclasses.replace(bundle, insights_records=tuple(existing_records))
        new_step: PreparedAnswer = {
            "kind": "prepared",
            "bundle": new_bundle,
            "transcript": step.get("transcript", []),
        }
        return new_step, added_labels

    @staticmethod
    def _generation_governance(frozen_input):
        import json

        from tap.modules.ai.domain.models import GenerationGovernance, schema_digest, text_digest

        governance = None
        if frozen_input.agent_revision_id is not None:
            if (
                frozen_input.agent_system_instruction is None
                or frozen_input.agent_system_instruction_digest
                != text_digest(frozen_input.agent_system_instruction)
                or frozen_input.agent_output_schema_json is None
            ):
                raise ValueError("accepted agent execution content is invalid")
            output_schema = json.loads(frozen_input.agent_output_schema_json)
            if (
                not isinstance(output_schema, dict)
                or schema_digest(output_schema) != frozen_input.agent_output_schema_digest
            ):
                raise ValueError("accepted agent output schema is invalid")
            governance = GenerationGovernance(
                model_alias=frozen_input.model_alias,
                system_instruction=frozen_input.agent_system_instruction,
                system_instruction_digest=frozen_input.agent_system_instruction_digest,
                skill_instructions=frozen_input.skill_instruction_templates,
                skill_instruction_digests=frozen_input.skill_instruction_template_digests,
                tool_allowlist=frozenset(frozen_input.agent_tool_allowlist),
                output_schema=output_schema,
                output_schema_digest=frozen_input.agent_output_schema_digest,
                revision_digests=(
                    frozen_input.agent_revision_digest,
                    frozen_input.agent_system_instruction_digest,
                    frozen_input.agent_output_schema_digest,
                    *frozen_input.skill_revision_digests,
                    *frozen_input.skill_instruction_template_digests,
                ),
                agent_revision_id=frozen_input.agent_revision_id,
                skill_names=frozen_input.skill_labels or frozen_input.skill_revision_ids,
                skill_revision_ids=frozen_input.skill_revision_ids,
            )
        return governance
