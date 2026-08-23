"""Authorized retrieval and grounded-answer orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tap.modules.access.domain.policy import (
    AuthorizationDenied,
    ResourceGrant,
    RetrievalPolicyContext,
)
from tap.modules.knowledge.domain.models import (
    AbstentionReason,
    AnswerMode,
    AnswerRequest,
    AnswerResponse,
    Citation,
    Claim,
    Evidence,
    ResolvedResourceRef,
    ResourceMode,
    ResourceRef,
    RetrievalProfileId,
    RevisionKind,
    SearchRequest,
    SearchResponse,
    SourceFamily,
    anchor_authorization_key,
)
from tap.modules.knowledge.ports.models import SearchExecution, SearchHit
from tap.modules.knowledge.ports.search import ModelPort, SearchPort


@dataclass(frozen=True, slots=True)
class RetrievalProfile:
    profile_id: RetrievalProfileId
    candidate_limit: int
    final_result_limit: int


PROFILES: dict[AnswerMode, RetrievalProfile] = {
    AnswerMode.QUICK: RetrievalProfile(
        profile_id=RetrievalProfileId.QUICK_HYBRID_V1,
        candidate_limit=20,
        final_result_limit=10,
    ),
    AnswerMode.DEEP: RetrievalProfile(
        profile_id=RetrievalProfileId.DEEP_HYBRID_V1,
        candidate_limit=50,
        final_result_limit=20,
    ),
}


class AuthorizedRetrieval:
    def __init__(
        self,
        *,
        search: SearchPort,
        model: ModelPort,
        id_factory: Callable[[], str],
    ) -> None:
        self._search = search
        self._model = model
        self._id_factory = id_factory

    async def search(
        self,
        request: SearchRequest,
        policy: RetrievalPolicyContext,
    ) -> SearchResponse:
        profile = PROFILES[request.answer_mode]
        source_families = self._source_families(request, policy)
        environment = self._environment(request, policy)
        corpus_version = self._corpus(request, policy)
        resources = tuple(self._resolve_resource(ref, policy) for ref in request.resource_refs)
        candidate_limit = min(request.top_k or profile.candidate_limit, profile.candidate_limit)
        trace_id = self._id_factory()
        query_plan_id = self._id_factory()
        context_snapshot_id = self._id_factory()

        embedding = await self._model.embed(request.query)
        hits = await self._search.search(
            SearchExecution(
                query=request.query,
                query_vector=embedding.vector,
                source_families=source_families,
                resources=resources,
                effective_environment=environment,
                corpus_version=corpus_version,
                candidate_limit=candidate_limit,
                profile_id=profile.profile_id.value,
                policy=policy,
            )
        )
        authorized_hits = tuple(
            hit for hit in hits if self._hit_is_in_execution(hit, source_families, corpus_version)
        )
        family_order = {family: index for index, family in enumerate(SourceFamily)}
        ordered_hits = sorted(
            authorized_hits,
            key=lambda hit: (
                -self._rrf_score(hit.local_rank),
                family_order[hit.family],
                hit.index_revision.physical_index,
                hit.chunk_id,
            ),
        )
        evidence = tuple(
            self._evidence(hit, position, policy.decision_id)
            for position, hit in enumerate(
                ordered_hits[: min(profile.final_result_limit, candidate_limit)],
                start=1,
            )
        )
        return SearchResponse(
            trace_id=trace_id,
            query_plan_id=query_plan_id,
            context_snapshot_id=context_snapshot_id,
            corpus_version=corpus_version,
            retrieval_profile_id=profile.profile_id,
            evidence=evidence,
        )

    async def answer(
        self,
        request: AnswerRequest,
        policy: RetrievalPolicyContext,
    ) -> AnswerResponse:
        search_response = await self.search(request.as_search_request(), policy)
        required = tuple(
            resource for resource in request.resource_refs if resource.mode is ResourceMode.REQUIRED
        )
        if not search_response.evidence:
            return self._abstain(search_response, AbstentionReason.INSUFFICIENT_EVIDENCE)
        missing_reason = self._required_resource_failure(required, search_response.evidence)
        if missing_reason is not None:
            return self._abstain(search_response, missing_reason)

        generation = await self._model.answer(
            request.query,
            search_response.evidence,
            search_response.retrieval_profile_id.value,
        )
        citations_by_label = {
            item.evidence_label: item.citation_id for item in search_response.evidence
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
                    search_response,
                    AbstentionReason.INSUFFICIENT_EVIDENCE,
                )
            claims.append(
                Claim(
                    claim_id=self._id_factory(),
                    text=generated_claim.text,
                    citation_ids=citation_ids,
                )
            )
        if generation.text and not claims:
            return self._abstain(search_response, AbstentionReason.INSUFFICIENT_EVIDENCE)

        return AnswerResponse(
            trace_id=search_response.trace_id,
            query_plan_id=search_response.query_plan_id,
            context_snapshot_id=search_response.context_snapshot_id,
            corpus_version=search_response.corpus_version,
            retrieval_profile_id=search_response.retrieval_profile_id,
            answer=generation.text,
            abstained=False,
            claims=tuple(claims),
            citations=tuple(self._citation(item) for item in search_response.evidence),
        )

    @staticmethod
    def _source_families(
        request: SearchRequest,
        policy: RetrievalPolicyContext,
    ) -> tuple[SourceFamily, ...]:
        allowed = {SourceFamily(family) for family in policy.allowed_source_families}
        requested = set(request.source_families) if request.source_families else allowed
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
        return ResolvedResourceRef(
            family=resource.family,
            source_id=resource.source_id,
            mode=resource.mode,
            revision_kind=revision_kind,
            revision=grant.revision,
            source_content_hash=grant.source_content_hash,
            anchor=resource.anchor,
        )

    @staticmethod
    def _anchor_allowed(resource: ResourceRef, grant: ResourceGrant) -> bool:
        if grant.allow_all_anchors:
            return True
        assert resource.anchor is not None
        return anchor_authorization_key(resource.anchor) in grant.allowed_anchor_keys

    def _evidence(self, hit: SearchHit, position: int, acl_decision_id: str) -> Evidence:
        return Evidence(
            family=hit.family,
            chunk_id=hit.chunk_id,
            logical_chunk_id=hit.logical_chunk_id,
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
            score=self._rrf_score(hit.local_rank),
            derived_from_chunk_ids=hit.derived_from_chunk_ids,
            provider_request_id=hit.provider_request_id,
        )

    @staticmethod
    def _hit_is_in_execution(
        hit: SearchHit,
        source_families: tuple[SourceFamily, ...],
        corpus_version: str,
    ) -> bool:
        return hit.family in source_families and hit.index_revision.corpus_version == corpus_version

    @staticmethod
    def _rrf_score(local_rank: int) -> float:
        return 1.0 / (60 + local_rank)

    @staticmethod
    def _citation(evidence: Evidence) -> Citation:
        return Citation(
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
        required: tuple[ResourceRef, ...],
        evidence: tuple[Evidence, ...],
    ) -> AbstentionReason | None:
        for resource in required:
            matching_source = tuple(
                item for item in evidence if item.source.source_id == resource.source_id
            )
            if not matching_source:
                return AbstentionReason.INSUFFICIENT_EVIDENCE
            if resource.requested_revision is not None and not any(
                item.source.revision == resource.requested_revision for item in matching_source
            ):
                return AbstentionReason.REVISION_MISMATCH
        return None

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
            degraded_mode=search_response.degraded_mode,
            degradation_reasons=search_response.degradation_reasons,
        )
