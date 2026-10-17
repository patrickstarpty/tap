"""Provider-neutral HTTP mapping for the Tapper knowledge application."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from collections.abc import Mapping
from typing import Any, Protocol
from uuid import uuid4

from opentelemetry.trace import get_current_span

from tap.contracts.http import (
    AbstentionReason,
    CitationPreview,
    DocumentAccepted,
    DocumentAnchor,
    DocumentDetail,
    DocumentPage,
    RetrievalAnswerRequest,
    RetrievalAnswerResponse,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
    SourceAccepted,
    SourceDetail,
    SourcePage,
    SourceRetryRequest,
    StructuralAnchor,
)
from tap.interfaces.http.dependencies import UploadInput
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.knowledge.api import (
    answer_request_from_http,
    answer_response_to_http,
    search_request_from_http,
    search_response_to_http,
)
from tap.modules.knowledge.application.answers import validate_answer_selection
from tap.modules.knowledge.application.citations import CitationPreviewResult
from tap.modules.knowledge.application.sources import SourceService
from tap.modules.knowledge.domain.models import (
    AnswerRequest,
    AnswerResponse,
    SearchRequest,
    SearchResponse,
)
from tap.modules.knowledge.domain.sources import SourceCommand


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
        "authorizedHitCount": len(response.evidence),
        "labels": labels,
    }


class DocumentOperations(Protocol):
    @property
    def scope(self) -> ProjectScopeContext: ...

    async def upload(self, upload: UploadInput) -> DocumentAccepted: ...

    async def list_documents(self, cursor: str | None, limit: int) -> DocumentPage: ...

    async def get_document(self, document_id: str) -> DocumentDetail: ...

    async def retry_document(self, document_id: str) -> DocumentAccepted: ...

    async def delete_document(self, document_id: str) -> None: ...


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


class CitationOperations(Protocol):
    @property
    def scope(self) -> ProjectScopeContext: ...

    async def resolve(self, citation_id: str) -> CitationPreviewResult: ...

    async def resolve_historical(self, citation_id: str) -> CitationPreviewResult: ...

    async def authorize_current(self, citation_id: str) -> None: ...


class SearchOperations(Protocol):
    @property
    def scope(self) -> ProjectScopeContext: ...

    async def search(self, request: SearchRequest) -> SearchResponse: ...


class KnowledgeHttpService:
    """Keep framework DTO conversion at the public edge of the application."""

    def __init__(
        self,
        *,
        documents: DocumentOperations,
        answers: AnswerOperations,
        citations: CitationOperations,
        searches: SearchOperations | None = None,
        sources: SourceService | None = None,
        corpus_version: str = "tapper-demo-v1",
        models: Any | None = None,
        answer_planner: Any | None = None,
        redactor: Any | None = None,
    ) -> None:
        if corpus_version not in {"tapper-demo-v1", "tapper-demo-v2"}:
            raise ValueError("unsupported projection corpus")
        self._documents = documents
        self._answers = answers
        self._citations = citations
        self._searches = searches
        self._sources = sources
        self._corpus_version = corpus_version
        self._models = models
        self.answer_planner = answer_planner
        self._redactor = redactor

    async def redaction_report(self, text: str) -> Mapping[str, int]:
        """Egress-redaction hit counts for `text`, by pattern kind; empty
        when no redactor is composed or the redactor exposes no report."""
        report = getattr(self._redactor, "redact_with_report", None)
        if report is None:
            return {}
        _sanitized, counts = await report(text)
        return counts

    @property
    def scope(self) -> ProjectScopeContext:
        """All operations must be bound to the same concrete repository scope."""
        scope = getattr(self._documents, "scope", None)
        if not isinstance(scope, ProjectScopeContext):
            raise AuthorizationDenied("scope-mismatch")
        for operation in (self._answers, self._citations, self._searches, self._sources):
            if operation is not None and getattr(operation, "scope", None) != scope:
                raise AuthorizationDenied("scope-mismatch")
        return scope

    def _source_service(self) -> SourceService:
        if self._sources is None:
            from tap.modules.knowledge.ports.errors import KnowledgeRuntimeUnavailable

            raise KnowledgeRuntimeUnavailable
        return self._sources

    async def upload_source(
        self, upload: UploadInput, key: str, correlation: str
    ) -> SourceAccepted:
        result = await self._source_service().upload(
            upload, SourceCommand(key, "source.upload", correlation)
        )
        assert isinstance(result, SourceAccepted)
        return result

    async def list_sources(self, cursor: str | None, limit: int) -> SourcePage:
        return await self._source_service().list_sources(cursor, limit)

    async def get_source(self, source_id: str, cursor: str | None, limit: int) -> SourceDetail:
        return await self._source_service().detail(source_id, cursor, limit)

    async def retry_source(
        self, source_id: str, body: SourceRetryRequest, key: str, correlation: str
    ) -> SourceAccepted:
        return await self._source_service().retry(
            source_id, body, SourceCommand(key, "source.retry", correlation)
        )

    async def delete_source(self, source_id: str, key: str, correlation: str) -> None:
        await self._source_service().delete_command(
            source_id, SourceCommand(key, "source.delete", correlation)
        )

    async def upload(
        self, upload: UploadInput, key: str | None = None, correlation: str | None = None
    ) -> DocumentAccepted:
        if key is None:
            return await self._documents.upload(upload)
        result = await self._source_service().upload(
            upload, SourceCommand(key, "document.upload", correlation or "missing")
        )
        assert isinstance(result, DocumentAccepted)
        return result

    async def list_documents(self, cursor: str | None, limit: int) -> DocumentPage:
        return await self._documents.list_documents(cursor, limit)

    async def get_document(self, document_id: str) -> DocumentDetail:
        return await self._documents.get_document(document_id)

    async def retry_document(
        self, document_id: str, key: str | None = None, correlation: str | None = None
    ) -> DocumentAccepted:
        if key is None:
            return await self._documents.retry_document(document_id)
        return await self._source_service().retry_document(
            document_id, SourceCommand(key, "document.retry", correlation or "missing")
        )

    async def delete_document(
        self, document_id: str, key: str | None = None, correlation: str | None = None
    ) -> None:
        if key is None:
            await self._documents.delete_document(document_id)
            return
        await self._source_service().delete_document(
            document_id, SourceCommand(key, "document.delete", correlation or "missing")
        )

    async def answer(self, request: RetrievalAnswerRequest) -> RetrievalAnswerResponse:
        domain_request = answer_request_from_http(request)
        validate_answer_selection(domain_request)
        response = await self._answers.answer(domain_request)
        return answer_response_to_http(response)

    async def resolve_conversation_selection(self, revision_ids: tuple[str, ...]):
        resolver = getattr(self._answers, "resolve_conversation_selection", None)
        if resolver is None:
            from tap.modules.knowledge.ports.errors import KnowledgeRuntimeUnavailable

            raise KnowledgeRuntimeUnavailable
        return await resolver(revision_ids)

    async def answer_conversation(
        self, request: RetrievalAnswerRequest, frozen_input, *, answer_plan=None, authorize=None
    ):
        # Defence in depth (M4): this is also called directly by
        # prompt-suggestion grounding outside the `tapper-chat-v2` graph,
        # which never runs `guard_in`'s own out-of-scope routing. Refuse
        # here too, before any retrieval or generation call.
        if answer_plan is not None and answer_plan.intent == "out_of_scope":
            return await self.refuse_out_of_scope(frozen_input, answer_plan=answer_plan)
        async with self._plan_deadline(answer_plan):
            return await self._answer_conversation(
                request, frozen_input, answer_plan=answer_plan, authorize=authorize
            )

    @staticmethod
    def _plan_deadline(answer_plan):
        """One shared deadline guard used by `answer_conversation` and every
        resumable phase below: raises `AnswerBudgetExhausted` (a `TimeoutError`
        subclass) immediately if the plan's persisted deadline has already
        passed (before any work, including
        `_authorized_frozen_context`), else returns an `asyncio.timeout`
        context scoped to the time left. `answer_plan.deadline_at` is an
        absolute timestamp, so recomputing it fresh at the start of every
        phase -- rather than passing a single captured budget along -- keeps
        nested uses of this guard safe: each nested call only ever tightens
        the remaining time, never resets it."""
        remaining = None if answer_plan is None else answer_plan.deadline_at - time.time()
        if remaining is not None and remaining <= 0:
            raise AnswerBudgetExhausted("persisted answer budget exhausted")
        return asyncio.timeout(remaining)

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
        self, request: RetrievalAnswerRequest, frozen_input, *, answer_plan=None, authorize=None
    ):
        """The reference composition of the four resumable phases below,
        inside the one persisted-deadline guard `answer_conversation` opened."""
        step = await self.prepare_conversation_answer(
            request, frozen_input, answer_plan=answer_plan, authorize=authorize
        )
        step = await self.generate_conversation_answer(
            step, frozen_input, answer_plan=answer_plan, authorize=authorize
        )
        step = await self.verify_conversation_answer(
            step, frozen_input, answer_plan=answer_plan, authorize=authorize
        )
        return await self.finalize_conversation_answer(
            step, frozen_input, answer_plan=answer_plan, authorize=authorize
        )

    async def prepare_conversation_answer(
        self, request: RetrievalAnswerRequest, frozen_input, *, answer_plan=None, authorize=None
    ):
        """Resumable prepare phase: retrieval (or a non-retrieval route's
        complete answer) only, re-deriving and re-checking authority from
        `frozen_input`, bounded by the plan's persisted deadline from the
        start -- including the non-retrieval model call, which otherwise has
        no time limit of its own."""
        async with self._plan_deadline(answer_plan):
            context = await self._authorized_frozen_context(
                original_question=request.query,
                frozen_input=frozen_input,
                answer_plan=answer_plan,
                authorize=authorize,
            )
            if isinstance(context, RetrievalAnswerResponse):
                return {
                    "kind": "final",
                    "response": context.model_dump(mode="json", by_alias=True),
                }
            revisions, policy, governance = context
            answer_execution = self._execution(answer_plan) if answer_plan is not None else None
            from tap.modules.knowledge.application.evidence_bundle import encode_bundle
            from tap.platform.serialization.dataclass_json import to_json

            domain_request = answer_request_from_http(request)
            prepared = await self._answers.prepare_frozen(
                domain_request,
                revisions,
                policy,
                governance=governance,
                answer_execution=answer_execution,
                authorize=authorize,
            )
            if isinstance(prepared, AnswerResponse):
                return {"kind": "verified", "response": to_json(prepared)}
            return {
                "kind": "prepared",
                "bundle": encode_bundle(prepared),
                # PR 3: the `seed_search` graph node emits `retrieval.hits_ready`
                # as soon as this step is produced -- before the `model` node
                # ever runs -- using exactly this payload, built here while the
                # (not-yet-encoded) evidence/relation context is still at hand.
                "retrieval_hits": _retrieval_hits_payload(prepared.response, prepared.relation),
            }

    async def generate_conversation_answer(
        self, step, frozen_input, *, answer_plan=None, authorize=None, on_text=None
    ):
        """Resumable generate phase: only acts on a `"prepared"` step,
        re-authorizing from `frozen_input` before the model call, bounded by
        the plan's persisted deadline from the start; any other step is
        returned unchanged. `on_text` (PR 3) is the `model` graph node's
        draft-streaming callback, passed through to the knowledge gateway's
        streaming model call; omitted entirely when `None` so a
        `generate_frozen` double that does not know about it is unaffected."""
        async with self._plan_deadline(answer_plan):
            if step.get("kind") != "prepared":
                return step
            answer_execution = self._execution(answer_plan) if answer_plan is not None else None
            from tap.modules.knowledge.application.evidence_bundle import decode_bundle
            from tap.platform.serialization.dataclass_json import to_json

            context = await self._authorized_frozen_context(
                original_question=frozen_input.message,
                frozen_input=frozen_input,
                answer_plan=answer_plan,
                authorize=authorize,
            )
            if isinstance(context, RetrievalAnswerResponse):
                raise ValueError("resumed step no longer matches a retrieval route")
            revisions, policy, governance = context
            bundle = decode_bundle(step["bundle"])
            generation = await self._answers.generate_frozen(
                bundle,
                revisions,
                policy,
                governance=governance,
                model_alias=frozen_input.model_alias,
                answer_execution=answer_execution,
                authorize=authorize,
                **({"on_text": on_text} if on_text is not None else {}),
            )
            return {
                "kind": "generated",
                "bundle": step["bundle"],
                "generation": to_json(generation),
            }

    async def verify_conversation_answer(
        self, step, frozen_input, *, answer_plan=None, authorize=None
    ):
        """Resumable verify phase: only acts on a `"generated"` step,
        re-authorizing from `frozen_input` before verification, bounded by
        the plan's persisted deadline from the start; any other step is
        returned unchanged."""
        async with self._plan_deadline(answer_plan):
            if step.get("kind") != "generated":
                return step
            answer_execution = self._execution(answer_plan) if answer_plan is not None else None
            from tap.modules.knowledge.application.evidence_bundle import decode_bundle
            from tap.modules.knowledge.ports.models import AnswerGeneration
            from tap.platform.serialization.dataclass_json import from_json, to_json

            context = await self._authorized_frozen_context(
                original_question=frozen_input.message,
                frozen_input=frozen_input,
                answer_plan=answer_plan,
                authorize=authorize,
            )
            if isinstance(context, RetrievalAnswerResponse):
                raise ValueError("resumed step no longer matches a retrieval route")
            revisions, policy, _governance = context
            bundle = decode_bundle(step["bundle"])
            generation = from_json(AnswerGeneration, step["generation"])
            response = await self._answers.verify_frozen(
                bundle,
                generation,
                revisions,
                policy,
                answer_execution=answer_execution,
                authorize=authorize,
            )
            return {"kind": "verified", "response": to_json(response)}

    async def finalize_conversation_answer(
        self, step, frozen_input, *, answer_plan=None, authorize=None
    ):
        """Resumable finalize phase: a `"final"` step maps straight to HTTP; a
        `"verified"` step (from `verify`, or an abstaining `prepare`) is
        persisted through `save_frozen` before the HTTP mapping -- all bounded
        by the plan's persisted deadline from the start (`save_frozen` itself
        takes no execution-budget parameter)."""
        async with self._plan_deadline(answer_plan):
            if step.get("kind") == "final":
                final_response = RetrievalAnswerResponse.model_validate(step["response"])
                return await self._redact_final_response(final_response)
            if step.get("kind") != "verified":
                raise ValueError("conversation answer step is not ready to finalize")
            from tap.platform.serialization.dataclass_json import from_json

            context = await self._authorized_frozen_context(
                original_question=frozen_input.message,
                frozen_input=frozen_input,
                answer_plan=answer_plan,
                authorize=authorize,
            )
            if isinstance(context, RetrievalAnswerResponse):
                raise ValueError("resumed step no longer matches a retrieval route")
            revisions, _policy, _governance = context
            response = from_json(AnswerResponse, step["response"])
            # Fail-closed: if `report()` raises `RedactionUnavailable` (e.g. an
            # unterminated credential value, or the answer exceeds the
            # redactor's bound), it propagates out of `finalize` uncaught --
            # nothing before `verify` is checked, so the turn must not save or
            # return an unredacted answer. `classify_turn_failure` treats it as
            # deterministic (not retryable).
            response = await self._redact_answer_response(response)
            await self._answers.save_frozen(response, frozen_input.message, revisions)
            return answer_response_to_http(response)

    @staticmethod
    def _paragraph_offsets(text: str) -> list[tuple[str, int, int]]:
        """Split `text` on `"\\n\\n"`, returning each paragraph with its
        `(start, end)` offset into `text` -- the same boundary a claim's
        text is validated against when claims are first generated
        (`retrieve.py`'s `_resolve_generated_claims`)."""
        paragraphs: list[tuple[str, int, int]] = []
        start = 0
        for paragraph in text.split("\n\n"):
            end = start + len(paragraph)
            paragraphs.append((paragraph, start, end))
            start = end + 2
        return paragraphs

    async def _redact_answer_response(self, response: AnswerResponse) -> AnswerResponse:
        """Redact `response.answer` and every claim's text with the same
        egress redactor before the frozen snapshot is built/saved, so a
        claim's text stays an exact (now-redacted) paragraph of the (now-
        redacted) answer. The abstained path has an empty answer, so there
        is nothing to redact. Only the answer-body hit counts land on the
        current span -- a claim is always a verbatim paragraph of the
        answer, so counting its hits too would double-count the same match;
        no hits means nothing is written."""
        report = getattr(self._redactor, "redact_with_report", None)
        if report is None or not response.answer:
            return response
        redacted_answer, hits = await report(response.answer)
        available = self._paragraph_offsets(redacted_answer)

        new_claims = []
        for claim in response.claims:
            redacted_text, _claim_hits = await report(claim.text)
            match_index = next(
                (index for index, (text, _s, _e) in enumerate(available) if text == redacted_text),
                None,
            )
            if match_index is None:
                new_claims.append(dataclasses.replace(claim, text=redacted_text))
                continue
            _text, claim_start, claim_end = available.pop(match_index)
            new_claims.append(
                dataclasses.replace(
                    claim, text=redacted_text, answer_start=claim_start, answer_end=claim_end
                )
            )

        if hits:
            get_current_span().set_attribute("tap.guard.redaction.output_hits", json.dumps(hits))
        return dataclasses.replace(response, answer=redacted_answer, claims=tuple(new_claims))

    async def _redact_final_response(
        self, response: RetrievalAnswerResponse
    ) -> RetrievalAnswerResponse:
        """Same redaction as `_redact_answer_response`, for the direct/
        model-only `"final"` step: that route never reaches `save_frozen`,
        but still returns the answer straight to the caller, so it must be
        redacted and counted the same way."""
        report = getattr(self._redactor, "redact_with_report", None)
        if report is None or not response.answer:
            return response
        redacted_answer, hits = await report(response.answer)
        available = self._paragraph_offsets(redacted_answer)

        new_claims = []
        for claim in response.claims:
            redacted_text, _claim_hits = await report(claim.text)
            match_index = next(
                (index for index, (text, _s, _e) in enumerate(available) if text == redacted_text),
                None,
            )
            if match_index is None:
                new_claims.append(claim.model_copy(update={"text": redacted_text}))
                continue
            _text, claim_start, claim_end = available.pop(match_index)
            new_claims.append(
                claim.model_copy(
                    update={
                        "text": redacted_text,
                        "answer_start": claim_start,
                        "answer_end": claim_end,
                    }
                )
            )

        if hits:
            get_current_span().set_attribute("tap.guard.redaction.output_hits", json.dumps(hits))
        redacted = response.model_copy(update={"answer": redacted_answer, "claims": new_claims})
        # Validate (M5): `model_copy` skips validators, so a redaction that
        # left an inconsistent response (e.g. a claim offset now outside the
        # redacted answer) must fail closed here rather than reach the caller.
        return RetrievalAnswerResponse.model_validate(
            redacted.model_dump(mode="json", by_alias=True)
        )

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
        `RetrievalAnswerResponse` for a route that never reaches it."""
        from tap.modules.chat.domain.conversations import content_digest
        from tap.modules.knowledge.application.demo_policy import build_demo_policy_context
        from tap.modules.knowledge.ports.answers import ReadyDocumentRevision

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
            generation = await self._models.chat(
                original_question,
                model_alias=frozen_input.model_alias,
                governance=governance,
                **(
                    {"answer_plan_id": answer_plan.plan_id, "answer_input": answer_input}
                    if answer_plan is not None
                    else {}
                ),
            )
            if authorize is not None:
                await authorize()
            identity = generation.gateway_call_id or generation.provider_request_id or uuid4().hex
            return RetrievalAnswerResponse(
                trace_id=identity,
                query_plan_id=f"model-only-{identity}",
                context_snapshot_id=f"model-only-{identity}",
                corpus_version=self._corpus_version,
                retrieval_profile_id="direct-chat-v1",
                degraded_mode=False,
                answer=generation.text,
                abstained=False,
                claims=[],
                citations=[],
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
            generation = await self._models.chat(
                original_question,
                model_alias=frozen_input.model_alias,
                governance=governance,
                answer_plan_id=answer_plan.plan_id,
                answer_input=answer_input,
            )
            if authorize is not None:
                await authorize()
            return RetrievalAnswerResponse(
                trace_id=answer_plan.plan_id,
                query_plan_id=answer_plan.plan_id,
                context_snapshot_id=answer_plan.plan_id,
                corpus_version=self._corpus_version,
                retrieval_profile_id="direct-chat-v1",
                degraded_mode=False,
                answer=generation.text,
                abstained=False,
                claims=[],
                citations=[],
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
        return RetrievalAnswerResponse(
            trace_id=plan.plan_id,
            query_plan_id=plan.plan_id,
            context_snapshot_id=plan.plan_id,
            corpus_version=self._corpus_version,
            retrieval_profile_id="direct-chat-v1",
            degraded_mode=False,
            answer=answer,
            abstained=False,
            claims=[],
            citations=[],
            graph_context_status="NOT_SELECTED",
        )

    async def refuse_out_of_scope(self, frozen_input, *, answer_plan):
        return RetrievalAnswerResponse(
            trace_id=answer_plan.plan_id,
            query_plan_id=answer_plan.plan_id,
            context_snapshot_id=answer_plan.plan_id,
            corpus_version=self._corpus_version,
            retrieval_profile_id="direct-chat-v1",
            degraded_mode=False,
            answer="",
            abstained=True,
            abstention_reason=AbstentionReason.OUT_OF_SCOPE,
            claims=[],
            citations=[],
            graph_context_status="NOT_SELECTED",
        )

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
            )
        return governance

    async def search(self, request: RetrievalSearchRequest) -> RetrievalSearchResponse:
        """Expose real evidence only to trusted in-process verification, never an HTTP route."""

        if self._searches is None:
            raise RuntimeError("internal knowledge search is not configured")
        response = await self._searches.search(search_request_from_http(request))
        return search_response_to_http(response)

    async def citation(self, citation_id: str) -> CitationPreview:
        preview = await self._citations.resolve(citation_id)
        return self._citation_preview(preview)

    async def historical_citation(self, citation_id: str) -> CitationPreview:
        preview = await self._citations.resolve_historical(citation_id)
        return self._citation_preview(preview)

    @staticmethod
    def _citation_preview(preview) -> CitationPreview:  # type: ignore[no-untyped-def]
        anchor = preview.anchor
        return CitationPreview(
            citation_id=preview.citation_id,
            document_id=preview.document_id,
            revision_id=preview.revision_id,
            filename=preview.filename,
            source_content_hash=preview.source_content_hash,
            chunk_content_hash=preview.chunk_content_hash,
            anchor=StructuralAnchor(
                root=DocumentAnchor(
                    type="document",
                    heading_path=list(anchor.heading_path) or None,
                    page=anchor.page,
                    bbox=list(anchor.bbox) or None,
                    start_offset=anchor.start_offset,
                    end_offset=anchor.end_offset,
                    inventory_item_id=anchor.inventory_item_id,
                )
            ),
            quote=preview.quote,
            prefix=preview.prefix,
            suffix=preview.suffix,
        )
