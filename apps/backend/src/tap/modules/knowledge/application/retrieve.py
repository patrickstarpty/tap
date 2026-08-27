"""Authorized retrieval and grounded-answer orchestration."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass

from tap.modules.access.application.ports import CurrentPolicyVerificationPort
from tap.modules.access.domain.policy import (
    AuthorizationDenied,
    PolicyUnavailable,
    ResourceGrant,
    RetrievalPolicyContext,
)
from tap.modules.knowledge.domain.models import (
    AbstentionReason,
    AnswerMode,
    AnswerRequest,
    AnswerResponse,
    BddAnchor,
    Citation,
    Claim,
    CodeAnchor,
    ContextLayer,
    ContextLayerKind,
    ContextSnapshot,
    DocumentAnchor,
    Evidence,
    FailureAnchor,
    FilterableSubtree,
    ModelCallProvenance,
    OpenApiAnchor,
    QueryPlan,
    ResolvedResourceRef,
    ResourceMode,
    ResourceRef,
    RetrievalProfileId,
    RevisionKind,
    SearchRequest,
    SearchResponse,
    SourceFamily,
    SourceRevisionRef,
    StructuralAnchor,
    anchor_authorization_key,
    context_snapshot_binds_query_plan,
)
from tap.modules.knowledge.ports.models import (
    AnswerGeneration,
    Embedding,
    SearchExecution,
    SearchHit,
)
from tap.modules.knowledge.ports.redaction import EgressRedactionPort
from tap.modules.knowledge.ports.search import ModelPort, SearchPort


@dataclass(frozen=True, slots=True)
class RetrievalProfile:
    profile_id: RetrievalProfileId
    candidate_limit: int
    final_result_limit: int
    preferred_resource_boost: float


PROFILES: dict[AnswerMode, RetrievalProfile] = {
    AnswerMode.QUICK: RetrievalProfile(
        profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
        candidate_limit=20,
        final_result_limit=10,
        preferred_resource_boost=0.01,
    ),
    AnswerMode.DEEP: RetrievalProfile(
        profile_id=RetrievalProfileId.DEEP_HYBRID_V1,
        candidate_limit=50,
        final_result_limit=20,
        preferred_resource_boost=0.01,
    ),
}

_FAMILY_PROVENANCE: dict[
    SourceFamily,
    tuple[RevisionKind, tuple[type[StructuralAnchor], ...]],
] = {
    SourceFamily.CODE: (RevisionKind.GIT_COMMIT, (CodeAnchor,)),
    SourceFamily.BDD: (RevisionKind.GIT_COMMIT, (BddAnchor,)),
    SourceFamily.DOC: (RevisionKind.BLOB_VERSION, (DocumentAnchor, OpenApiAnchor)),
    SourceFamily.FAILURE: (RevisionKind.MYSQL_VERSION, (FailureAnchor,)),
}
_KNOWN_SOURCE_TYPE_FAMILY = {
    "code": SourceFamily.CODE,
    "code_summary": SourceFamily.CODE,
    "bdd": SourceFamily.BDD,
    "doc": SourceFamily.DOC,
    "document": SourceFamily.DOC,
    "openapi": SourceFamily.DOC,
    "failure": SourceFamily.FAILURE,
}


@dataclass(frozen=True, slots=True)
class _RetrievalRun:
    response: SearchResponse
    plan: QueryPlan
    context_snapshot: ContextSnapshot
    policy: RetrievalPolicyContext


class AuthorizedRetrieval:
    def __init__(
        self,
        *,
        search: SearchPort,
        model: ModelPort,
        policy_verifier: CurrentPolicyVerificationPort,
        redactor: EgressRedactionPort,
        id_factory: Callable[[], str],
    ) -> None:
        self._search = search
        self._model = model
        self._policy_verifier = policy_verifier
        self._redactor = redactor
        self._id_factory = id_factory

    async def search(
        self,
        request: SearchRequest,
        policy: RetrievalPolicyContext,
    ) -> SearchResponse:
        return (await self._retrieve(request, policy)).response

    async def answer(
        self,
        request: AnswerRequest,
        policy: RetrievalPolicyContext,
    ) -> AnswerResponse:
        run = await self._retrieve(request.as_search_request(), policy)
        required = tuple(
            resource for resource in run.plan.resources if resource.mode is ResourceMode.REQUIRED
        )
        if not run.response.evidence:
            return self._abstain(run.response, AbstentionReason.INSUFFICIENT_EVIDENCE)
        missing_reason = self._required_resource_failure(required, run.response.evidence)
        if missing_reason is not None:
            return self._abstain(run.response, missing_reason)
        if self._has_conflicting_sources(run.response.evidence):
            return self._abstain(run.response, AbstentionReason.CONFLICTING_SOURCES)
        current = await self._verify_current(run.policy)
        self._validate_binding(current, run.plan, run.context_snapshot)
        generation = await self._model.answer(
            run.plan.sanitized_query,
            run.response.evidence,
            run.response.retrieval_profile_id.value,
        )
        current = await self._verify_current(current)
        self._validate_binding(current, run.plan, run.context_snapshot)
        citations_by_label = {
            item.evidence_label: item.citation_id for item in run.response.evidence
        }
        claims: list[Claim] = []
        for generated_claim in generation.claims:
            citation_ids = tuple(
                citations_by_label[label]
                for label in generated_claim.evidence_labels
                if label in citations_by_label
            )
            if not citation_ids or len(citation_ids) != len(generated_claim.evidence_labels):
                return self._abstain(
                    run.response,
                    AbstentionReason.INSUFFICIENT_EVIDENCE,
                )
            span = self._complete_paragraph_span(generation.text, generated_claim.text)
            if span is None:
                return self._abstain(
                    run.response,
                    AbstentionReason.INSUFFICIENT_EVIDENCE,
                )
            claims.append(
                Claim(
                    claim_id=self._id_factory(),
                    text=generated_claim.text,
                    answer_start=span[0],
                    answer_end=span[1],
                    citation_ids=citation_ids,
                )
            )
        if generation.text and not claims:
            return self._abstain(run.response, AbstentionReason.INSUFFICIENT_EVIDENCE)

        claims.sort(key=lambda item: (item.answer_start, item.answer_end))
        if any(
            later.answer_start < earlier.answer_end for earlier, later in zip(claims, claims[1:])
        ):
            return self._abstain(run.response, AbstentionReason.INSUFFICIENT_EVIDENCE)

        return AnswerResponse(
            trace_id=run.response.trace_id,
            query_plan_id=run.response.query_plan_id,
            context_snapshot_id=run.response.context_snapshot_id,
            corpus_version=run.response.corpus_version,
            retrieval_profile_id=run.response.retrieval_profile_id,
            answer=generation.text,
            abstained=False,
            claims=tuple(claims),
            citations=tuple(self._citation(item) for item in run.response.evidence),
            embedding_provenance=run.response.embedding_provenance,
            answer_provenance=self._generation_provenance(generation),
        )

    @staticmethod
    def _complete_paragraph_span(answer: str, claim_text: str) -> tuple[int, int] | None:
        """Locate one claim only when it is exactly one complete answer paragraph."""
        start = answer.find(claim_text)
        if start < 0 or answer.find(claim_text, start + 1) >= 0:
            return None
        end = start + len(claim_text)
        if (start != 0 and not answer[:start].endswith("\n\n")) or (
            end != len(answer) and not answer[end:].startswith("\n\n")
        ):
            return None
        return start, end

    async def _retrieve(
        self,
        request: SearchRequest,
        policy: RetrievalPolicyContext,
    ) -> _RetrievalRun:
        current = await self._verify_current(policy)
        profile = PROFILES[request.answer_mode]
        source_families = self._source_families(request, current)
        environment = self._environment(request, current)
        corpus_version = self._corpus(request, current)
        resources = tuple(self._resolve_resource(ref, current) for ref in request.resource_refs)
        candidate_limit = min(request.top_k or profile.candidate_limit, profile.candidate_limit)

        redaction = await self._redactor.redact(request.query)
        operation_id = self._id_factory()
        plan = QueryPlan(
            query_plan_id=self._id_factory(),
            operation_id=operation_id,
            tenant_id=current.tenant_id,
            project_id=current.project_id,
            policy_decision_id=current.decision_id,
            policy_version=current.policy_version,
            acl_digest=current.acl_digest,
            answer_mode=request.answer_mode,
            retrieval_profile_id=profile.profile_id,
            source_families=source_families,
            resources=resources,
            effective_environment=environment,
            corpus_version=corpus_version,
            candidate_limit=candidate_limit,
            raw_request_hash=self._raw_request_hash(request),
            sanitized_query=redaction.sanitized_text,
            sanitized_query_hash=self._text_hash(redaction.sanitized_text),
            redaction_version=redaction.redaction_version,
            embedding_model_id=self._model.embedding_model_id,
            embedding_dimension=self._model.embedding_dimension,
        )
        context_snapshot = ContextSnapshot(
            context_snapshot_id=self._id_factory(),
            operation_id=operation_id,
            tenant_id=current.tenant_id,
            project_id=current.project_id,
            policy_decision_id=current.decision_id,
            policy_version=current.policy_version,
            acl_digest=current.acl_digest,
            layers=(
                ContextLayer(
                    kind=ContextLayerKind.CURRENT_TURN,
                    ref_ids=(),
                    content_hash=plan.sanitized_query_hash,
                    token_count=len(plan.sanitized_query.split()),
                ),
            ),
        )
        trace_id = self._id_factory()

        current = await self._verify_current(current)
        self._validate_binding(current, plan, context_snapshot)
        embedding = await self._model.embed(plan.sanitized_query)
        self._validate_embedding(embedding, plan)
        current = await self._verify_current(current)
        self._validate_binding(current, plan, context_snapshot)
        hits = await self._search.search(
            SearchExecution(
                policy=current,
                plan=plan,
                context_snapshot=context_snapshot,
                query_vector=embedding.vector,
            )
        )
        current = await self._verify_current(current)
        self._validate_binding(current, plan, context_snapshot)
        if not all(self._hit_is_in_execution(hit, plan) for hit in hits):
            raise AuthorizationDenied("Search returned evidence outside bound execution")
        authorized_hits = hits
        family_order = {family: index for index, family in enumerate(SourceFamily)}
        ordered_hits = sorted(
            authorized_hits,
            key=lambda hit: (
                -self._fused_score(hit, plan.resources, profile),
                family_order[hit.family],
                hit.index_revision.physical_index,
                hit.chunk_id,
            ),
        )
        evidence = tuple(
            self._evidence(
                hit,
                position,
                current.decision_id,
                self._fused_score(hit, plan.resources, profile),
            )
            for position, hit in enumerate(
                ordered_hits[: min(profile.final_result_limit, candidate_limit)],
                start=1,
            )
        )
        response = SearchResponse(
            trace_id=trace_id,
            query_plan_id=plan.query_plan_id,
            context_snapshot_id=context_snapshot.context_snapshot_id,
            corpus_version=plan.corpus_version,
            retrieval_profile_id=profile.profile_id,
            evidence=evidence,
            embedding_provenance=self._embedding_provenance(embedding),
        )
        return _RetrievalRun(
            response=response,
            plan=plan,
            context_snapshot=context_snapshot,
            policy=current,
        )

    async def _verify_current(
        self,
        expected: RetrievalPolicyContext,
    ) -> RetrievalPolicyContext:
        current = await self._policy_verifier.verify_current(expected)
        if current is None:
            raise PolicyUnavailable("current Project Policy is unavailable")
        if not isinstance(current, RetrievalPolicyContext) or current != expected:
            raise AuthorizationDenied("retrieval policy facts are stale or changed")
        return current

    def _validate_binding(
        self,
        policy: RetrievalPolicyContext,
        plan: QueryPlan,
        snapshot: ContextSnapshot,
    ) -> None:
        policy_facts = (
            policy.tenant_id,
            policy.project_id,
            policy.decision_id,
            policy.policy_version,
            policy.acl_digest,
        )
        if (
            policy_facts
            != (
                plan.tenant_id,
                plan.project_id,
                plan.policy_decision_id,
                plan.policy_version,
                plan.acl_digest,
            )
            or not context_snapshot_binds_query_plan(plan, snapshot)
            or plan.corpus_version != policy.active_corpus_version
            or not {item.value for item in plan.source_families} <= policy.allowed_source_families
            or plan.embedding_model_id != self._model.embedding_model_id
            or plan.embedding_dimension != self._model.embedding_dimension
            or plan.sanitized_query_hash != self._text_hash(plan.sanitized_query)
        ):
            raise AuthorizationDenied("policy, query plan, and context snapshot are not bound")

    @staticmethod
    def _validate_embedding(embedding: Embedding, plan: QueryPlan) -> None:
        if embedding.model_id != plan.embedding_model_id:
            raise AuthorizationDenied("embedding model does not match the query plan")
        if len(embedding.vector) != plan.embedding_dimension or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in embedding.vector
        ):
            raise AuthorizationDenied("embedding vector does not match the query plan")

    @staticmethod
    def _source_families(
        request: SearchRequest,
        policy: RetrievalPolicyContext,
    ) -> tuple[SourceFamily, ...]:
        allowed = {SourceFamily(family) for family in policy.allowed_source_families}
        requested = set(request.source_families) if request.source_families else allowed
        scoped_families = {
            resource.family
            for resource in request.resource_refs
            if resource.mode is ResourceMode.SCOPE
        }
        if scoped_families:
            requested &= scoped_families
        effective = tuple(family for family in SourceFamily if family in requested & allowed)
        if not effective:
            raise AuthorizationDenied("requested source scope is not authorized")
        return effective

    @staticmethod
    def _environment(
        request: SearchRequest,
        policy: RetrievalPolicyContext,
    ) -> str | None:
        if (
            request.requested_environment is not None
            and request.requested_environment not in policy.allowed_environments
        ):
            raise AuthorizationDenied("requested environment is not authorized")
        return request.requested_environment

    @staticmethod
    def _corpus(request: SearchRequest, policy: RetrievalPolicyContext) -> str:
        if (
            request.requested_corpus_version is not None
            and request.requested_corpus_version != policy.active_corpus_version
        ):
            raise AuthorizationDenied("requested corpus is not active for this policy")
        return policy.active_corpus_version

    @staticmethod
    def _resolve_resource(
        resource: ResourceRef,
        policy: RetrievalPolicyContext,
    ) -> ResolvedResourceRef:
        grant = next(
            (
                candidate
                for candidate in policy.resource_grants
                if candidate.family == resource.family.value
                and candidate.source_id == resource.source_id
            ),
            None,
        )
        if grant is None:
            raise AuthorizationDenied("resource is not authorized")
        if (
            resource.requested_revision is not None
            and resource.requested_revision != grant.revision
        ):
            raise AuthorizationDenied("resource revision is unavailable or unauthorized")
        if resource.anchor is not None and not AuthorizedRetrieval._anchor_allowed(resource, grant):
            raise AuthorizationDenied("resource anchor is not authorized")
        try:
            revision_kind = RevisionKind(grant.revision_kind)
        except ValueError as error:
            raise AuthorizationDenied("resource policy has an invalid revision kind") from error
        subtree = None
        if resource.anchor is not None:
            anchor_key = anchor_authorization_key(resource.anchor)
            subtree_grant = next(
                (
                    candidate
                    for candidate in grant.subtree_grants
                    if candidate.anchor_key == anchor_key
                ),
                None,
            )
            if subtree_grant is not None:
                subtree = FilterableSubtree(
                    root_ids=subtree_grant.root_ids,
                    parent_ids=subtree_grant.parent_ids,
                    logical_chunk_ids=subtree_grant.logical_chunk_ids,
                )
            elif resource.mode is ResourceMode.SCOPE:
                raise AuthorizationDenied("anchored scope has no trusted filterable subtree")
        return ResolvedResourceRef(
            family=resource.family,
            source_id=resource.source_id,
            mode=resource.mode,
            revision_kind=revision_kind,
            revision=grant.revision,
            source_content_hash=grant.source_content_hash,
            anchor=resource.anchor,
            subtree=subtree,
        )

    @staticmethod
    def _anchor_allowed(resource: ResourceRef, grant: ResourceGrant) -> bool:
        if grant.allow_all_anchors:
            return True
        assert resource.anchor is not None
        return anchor_authorization_key(resource.anchor) in grant.allowed_anchor_keys

    def _evidence(
        self,
        hit: SearchHit,
        position: int,
        acl_decision_id: str,
        fused_score: float,
    ) -> Evidence:
        return Evidence(
            family=hit.family,
            chunk_id=hit.chunk_id,
            logical_chunk_id=hit.logical_chunk_id,
            root_id=hit.root_id,
            parent_id=hit.parent_id,
            title=hit.title,
            content=hit.content,
            source=hit.source,
            chunk_content_hash=hit.chunk_content_hash,
            content_role=hit.content_role,
            citation_id=self._id_factory(),
            evidence_label=f"S{position}",
            index_revision=hit.index_revision,
            embedding_model_version=hit.embedding_model_version,
            acl_decision_id=acl_decision_id,
            score=fused_score,
            derived_from_chunk_ids=hit.derived_from_chunk_ids,
            provider_request_id=hit.provider_request_id,
        )

    @staticmethod
    def _hit_is_in_execution(
        hit: SearchHit,
        plan: QueryPlan,
    ) -> bool:
        if (
            not AuthorizedRetrieval._hit_has_compatible_provenance(hit)
            or hit.family not in plan.source_families
            or hit.index_revision.corpus_version != plan.corpus_version
            or hit.embedding_model_version != plan.embedding_model_id
        ):
            return False
        scoped = tuple(
            resource
            for resource in plan.resources
            if resource.mode is ResourceMode.SCOPE and resource.family is hit.family
        )
        if not scoped:
            return True
        for resource in scoped:
            if not (
                hit.source.source_id == resource.source_id
                and hit.source.revision_kind is resource.revision_kind
                and hit.source.revision == resource.revision
                and hit.source.source_content_hash == resource.source_content_hash
            ):
                continue
            if resource.subtree is None or (
                (hit.root_id is not None and hit.root_id in resource.subtree.root_ids)
                or (hit.parent_id is not None and hit.parent_id in resource.subtree.parent_ids)
                or hit.logical_chunk_id in resource.subtree.logical_chunk_ids
            ):
                return True
        return False

    @staticmethod
    def _hit_has_compatible_provenance(hit: SearchHit) -> bool:
        """Reject provider-neutral hits whose provenance crosses source families."""
        if (
            not isinstance(hit, SearchHit)
            or not isinstance(hit.family, SourceFamily)
            or not isinstance(hit.source, SourceRevisionRef)
            or not isinstance(hit.source.source_type, str)
        ):
            return False
        expected = _FAMILY_PROVENANCE.get(hit.family)
        if expected is None:
            return False
        revision_kind, anchor_types = expected
        known_family = _KNOWN_SOURCE_TYPE_FAMILY.get(hit.source.source_type)
        return (
            hit.source.revision_kind is revision_kind
            and isinstance(hit.source.anchor, anchor_types)
            and (known_family is None or known_family is hit.family)
        )

    @staticmethod
    def _rrf_score(local_rank: int) -> float:
        return 1.0 / (60 + local_rank)

    def _fused_score(
        self,
        hit: SearchHit,
        resources: tuple[ResolvedResourceRef, ...],
        profile: RetrievalProfile,
    ) -> float:
        preferred = any(
            resource.mode is ResourceMode.PREFERRED
            and resource.family is hit.family
            and resource.source_id == hit.source.source_id
            and resource.revision == hit.source.revision
            and resource.source_content_hash == hit.source.source_content_hash
            for resource in resources
        )
        return self._rrf_score(hit.local_rank) + (
            profile.preferred_resource_boost if preferred else 0.0
        )

    @staticmethod
    def _citation(evidence: Evidence) -> Citation:
        return Citation(
            family=evidence.family,
            citation_id=evidence.citation_id,
            evidence_label=evidence.evidence_label,
            chunk_id=evidence.chunk_id,
            logical_chunk_id=evidence.logical_chunk_id,
            source=evidence.source,
            chunk_content_hash=evidence.chunk_content_hash,
            content_role=evidence.content_role,
            derived_from_chunk_ids=evidence.derived_from_chunk_ids,
        )

    @staticmethod
    def _required_resource_failure(
        required: tuple[ResolvedResourceRef, ...],
        evidence: tuple[Evidence, ...],
    ) -> AbstentionReason | None:
        for resource in required:
            matching_source = tuple(
                item
                for item in evidence
                if item.family is resource.family
                and item.source.source_id == resource.source_id
                and item.source.revision_kind is resource.revision_kind
                and item.source.revision == resource.revision
                and item.source.source_content_hash == resource.source_content_hash
            )
            if not matching_source:
                return AbstentionReason.INSUFFICIENT_EVIDENCE
            if resource.anchor is not None and not any(
                anchor_authorization_key(item.source.anchor)
                == anchor_authorization_key(resource.anchor)
                for item in matching_source
            ):
                return AbstentionReason.REVISION_MISMATCH
            if resource.subtree is not None and not any(
                (item.root_id is not None and item.root_id in resource.subtree.root_ids)
                or (item.parent_id is not None and item.parent_id in resource.subtree.parent_ids)
                or item.logical_chunk_id in resource.subtree.logical_chunk_ids
                for item in matching_source
            ):
                return AbstentionReason.REVISION_MISMATCH
        return None

    @staticmethod
    def _has_conflicting_sources(evidence: tuple[Evidence, ...]) -> bool:
        hashes_by_logical_chunk: dict[str, set[str]] = {}
        for item in evidence:
            hashes_by_logical_chunk.setdefault(item.logical_chunk_id, set()).add(
                item.chunk_content_hash
            )
        return any(len(hashes) > 1 for hashes in hashes_by_logical_chunk.values())

    @staticmethod
    def _embedding_provenance(embedding: Embedding) -> ModelCallProvenance:
        return ModelCallProvenance(
            configured_model_id=embedding.model_id,
            provider_request_id=embedding.provider_request_id,
            gateway_call_id=embedding.gateway_call_id,
            gateway_model_id=embedding.gateway_model_id,
            provider_model_id=embedding.provider_model_id,
            completion_id=embedding.completion_id,
        )

    @staticmethod
    def _generation_provenance(generation: AnswerGeneration) -> ModelCallProvenance:
        return ModelCallProvenance(
            configured_model_id=generation.model_id,
            provider_request_id=generation.provider_request_id,
            gateway_call_id=generation.gateway_call_id,
            gateway_model_id=generation.gateway_model_id,
            provider_model_id=generation.provider_model_id,
            completion_id=generation.completion_id,
        )

    @staticmethod
    def _abstain(
        search_response: SearchResponse,
        reason: AbstentionReason,
    ) -> AnswerResponse:
        return AnswerResponse(
            trace_id=search_response.trace_id,
            query_plan_id=search_response.query_plan_id,
            context_snapshot_id=search_response.context_snapshot_id,
            corpus_version=search_response.corpus_version,
            retrieval_profile_id=search_response.retrieval_profile_id,
            answer="",
            abstained=True,
            abstention_reason=reason,
            claims=(),
            citations=tuple(
                AuthorizedRetrieval._citation(item) for item in search_response.evidence
            ),
            embedding_provenance=search_response.embedding_provenance,
            answer_provenance=None,
            degraded_mode=search_response.degraded_mode,
            degradation_reasons=search_response.degradation_reasons,
        )

    @staticmethod
    def _raw_request_hash(request: SearchRequest) -> str:
        payload = {
            "answerMode": request.answer_mode.value,
            "query": request.query,
            "requestedCorpusVersion": request.requested_corpus_version,
            "requestedEnvironment": request.requested_environment,
            "resourceRefs": [
                {
                    "anchor": AuthorizedRetrieval._anchor_payload(resource.anchor),
                    "family": resource.family.value,
                    "mode": resource.mode.value,
                    "requestedRevision": resource.requested_revision,
                    "sourceId": resource.source_id,
                }
                for resource in request.resource_refs
            ],
            "sourceFamilies": [family.value for family in request.source_families],
            "topK": request.top_k,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _text_hash(text: str) -> str:
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _anchor_payload(anchor: StructuralAnchor | None) -> dict[str, object] | None:
        if anchor is None:
            return None
        if isinstance(anchor, DocumentAnchor):
            return {
                "bbox": list(anchor.bbox),
                "endOffset": anchor.end_offset,
                "headingPath": list(anchor.heading_path),
                "page": anchor.page,
                "startOffset": anchor.start_offset,
                "type": "document",
            }
        if isinstance(anchor, CodeAnchor):
            return {
                "lineEnd": anchor.line_end,
                "lineStart": anchor.line_start,
                "path": anchor.path,
                "repo": anchor.repo,
                "symbol": anchor.symbol,
                "type": "code",
            }
        if isinstance(anchor, BddAnchor):
            return {
                "featureId": anchor.feature_id,
                "scenarioId": anchor.scenario_id,
                "stepId": anchor.step_id,
                "type": "bdd",
            }
        if isinstance(anchor, OpenApiAnchor):
            return {
                "jsonPointer": anchor.json_pointer,
                "method": anchor.method,
                "path": anchor.path,
                "type": "openapi",
            }
        return {
            "incidentId": anchor.incident_id,
            "runId": anchor.run_id,
            "timeEnd": anchor.time_end,
            "timeStart": anchor.time_start,
            "type": "failure",
        }
