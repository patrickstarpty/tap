"""Authorized retrieval and grounded-answer orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Protocol, cast

from tap.modules.access.application.ports import CurrentPolicyVerificationPort
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.access.domain.policy import (
    AuthorizationDenied,
    PolicyUnavailable,
    ResourceGrant,
    RetrievalPolicyContext,
)
from tap.modules.ai.application.agents.protocol import AgentContext
from tap.modules.ai.application.agents.registry import AgentRegistry
from tap.modules.knowledge.application.answer_templates import assemble_answer
from tap.modules.knowledge.application.planned_answer import AuthorizedAnswerExecution
from tap.modules.knowledge.application.publication import (
    FlowchartPublicationGate,
    PublicationBinding,
    PublishedKnowledgeAuthority,
)
from tap.modules.knowledge.application.relation_analysis import (
    EvidenceRef,
    RelationAnalysisInput,
    RelationContext,
    RelationContextStatus,
    RelationEvidence,
)
from tap.modules.knowledge.application.relation_claims import reconcile_relation_claims
from tap.modules.knowledge.domain.documents import (
    DocumentId,
    RevisionId,
    chunk_id_for,
    logical_chunk_id_for,
    logical_chunk_projection_id,
)
from tap.modules.knowledge.domain.flowchart_paths import (
    flowchart_claim_is_consistent,
    flowchart_path_context,
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
    ContentRole,
    ContextLayer,
    ContextLayerKind,
    ContextSnapshot,
    DocumentAnchor,
    EdgeCitation,
    Evidence,
    FailureAnchor,
    FilterableSubtree,
    ModelCallProvenance,
    OpenApiAnchor,
    QueryPlan,
    RelationOutcome,
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
from tap.modules.knowledge.ports.errors import ModelUnavailable, SearchUnavailable
from tap.modules.knowledge.ports.models import (
    AnswerGeneration,
    Embedding,
    GeneratedClaim,
    SearchExecution,
    SearchHit,
)
from tap.modules.knowledge.ports.redaction import EgressRedactionPort
from tap.modules.knowledge.ports.search import (
    AnswerGenerationPort,
    GovernedKnowledgeModels,
    QueryEmbeddingPort,
    SearchPort,
)
from tap.platform.telemetry import span

_MAX_EVIDENCE = 20


class RelationAnalysis(Protocol):
    """Narrow retrieval-facing relation analysis port: run the deterministic
    relation subgraph against selection-filtered retrieval evidence."""

    async def analyse(
        self,
        query: str,
        evidence: tuple[Evidence, ...],
        source_revision_ids: tuple[str, ...],
        *,
        turn_id: str | None = None,
    ) -> RelationContext: ...


class RegisteredRelationAnalysis:
    """Adapts the retrieval-facing `RelationAnalysis` port onto the
    `"relation-analysis"` agent subgraph resolved from an `AgentRegistry`."""

    def __init__(self, registry: AgentRegistry, *, scope: ProjectScopeContext) -> None:
        self._agent = registry.get("relation-analysis")
        self._scope = scope

    async def analyse(
        self,
        query: str,
        evidence: tuple[Evidence, ...],
        source_revision_ids: tuple[str, ...],
        *,
        turn_id: str | None = None,
    ) -> RelationContext:
        context = AgentContext(
            scope=self._scope,
            source_revision_ids=frozenset(source_revision_ids),
            graph_version=None,
            turn_id=turn_id,
        )
        value = RelationAnalysisInput(
            query=query,
            source_revision_ids=frozenset(source_revision_ids),
            evidence=tuple(_evidence_ref(item) for item in evidence),
            graph_version=None,
        )
        return cast(RelationContext, await self._agent.run(context, value))


def _evidence_ref(item: Evidence) -> EvidenceRef:
    return EvidenceRef(
        label=item.evidence_label,
        chunk_id=item.chunk_id,
        source_revision_id=item.source.revision,
        document_revision_id=item.source.revision,
    )


def _document_anchor_from_mapping(anchor: Mapping[str, object]) -> DocumentAnchor | None:
    """Parse a relation support's raw anchor mapping into a `DocumentAnchor`,
    or `None` if it is not the `document` anchor shape -- the caller then
    skips this support in favor of the next one (Task 5 review I2)."""
    if anchor.get("type") != "document":
        return None
    start_offset = anchor.get("startOffset")
    end_offset = anchor.get("endOffset")
    if start_offset is None or end_offset is None:
        return None
    try:
        return DocumentAnchor(
            heading_path=tuple(cast(Any, anchor.get("headingPath")) or ()),
            page=None if anchor.get("page") is None else int(cast(Any, anchor["page"])),
            bbox=tuple(cast(Any, anchor.get("bbox")) or ()),
            start_offset=int(cast(Any, start_offset)),
            end_offset=int(cast(Any, end_offset)),
            inventory_item_id=cast(Any, anchor.get("inventoryItemId")),
        )
    except (TypeError, ValueError):
        return None


def _canonical_document_anchor_json(anchor: DocumentAnchor) -> str:
    value: dict[str, object] = {
        "endOffset": anchor.end_offset,
        "headingPath": list(anchor.heading_path),
        "startOffset": anchor.start_offset,
        "type": "document",
    }
    if anchor.page is not None:
        value["page"] = anchor.page
    if anchor.inventory_item_id is not None:
        value["inventoryItemId"] = anchor.inventory_item_id
    if anchor.bbox:
        value["bbox"] = list(anchor.bbox)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _label_num(label: str) -> int:
    return int(label[1:])


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
    publication: PublicationBinding | None = None


def _resolve_generated_claims(
    generation: AnswerGeneration,
    evidence: tuple[Evidence, ...],
    *,
    id_factory: Callable[[], str],
    extra_citation_ids: Mapping[str, str] = MappingProxyType({}),
) -> tuple[Claim, ...] | None:
    citations_by_label = {item.evidence_label: item.citation_id for item in evidence}
    citations_by_label.update(extra_citation_ids)
    claims: list[Claim] = []
    for generated_claim in generation.claims:
        citation_ids = tuple(
            citations_by_label[label]
            for label in generated_claim.evidence_labels
            if label in citations_by_label
        )
        if not citation_ids or len(citation_ids) != len(generated_claim.evidence_labels):
            return None
        if "\n\n" in generated_claim.text:
            return None
        spans: list[tuple[int, int]] = []
        start = 0
        for paragraph in generation.text.split("\n\n"):
            end = start + len(paragraph)
            if paragraph == generated_claim.text:
                spans.append((start, end))
            start = end + 2
        if len(spans) != 1:
            return None
        claims.append(
            Claim(
                claim_id=id_factory(),
                text=generated_claim.text,
                answer_start=spans[0][0],
                answer_end=spans[0][1],
                citation_ids=citation_ids,
            )
        )
    if generation.text and not claims:
        return None

    claims.sort(key=lambda item: (item.answer_start, item.answer_end))
    if any(later.answer_start < earlier.answer_end for earlier, later in zip(claims, claims[1:])):
        return None
    return tuple(claims)


class AuthorizedRetrieval:
    def __init__(
        self,
        *,
        search: SearchPort,
        embeddings: QueryEmbeddingPort | None = None,
        answers: AnswerGenerationPort | None = None,
        models: GovernedKnowledgeModels | None = None,
        policy_verifier: CurrentPolicyVerificationPort,
        redactor: EgressRedactionPort,
        id_factory: Callable[[], str],
        publication_authority: PublishedKnowledgeAuthority | None = None,
        flowchart_gate: FlowchartPublicationGate | None = None,
        relation_analysis: RelationAnalysis | None = None,
        retrieval_augment: bool = True,
    ) -> None:
        self._search = search
        if models is not None:
            if embeddings is not None or answers is not None:
                raise ValueError("governed models cannot be mixed with legacy providers")
            embeddings = models
            answers = models
        if embeddings is None or answers is None:
            raise ValueError("Knowledge requires a complete model boundary")
        self._models = models
        self._embeddings = embeddings
        self._answers = answers
        self._policy_verifier = policy_verifier
        self._redactor = redactor
        self._id_factory = id_factory
        self._publication_authority = publication_authority
        # Direct chunk lifecycles skip publication, except for reviewed flowchart images.
        self._flowchart_gate = None if publication_authority is not None else flowchart_gate
        self._relation_analysis = relation_analysis
        self._retrieval_augment = retrieval_augment

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
        *,
        frozen_policy: bool = False,
        governance=None,
        graph_context: tuple[Mapping[str, object], ...] = (),
        model_alias: str | None = None,
        answer_execution: AuthorizedAnswerExecution | None = None,
        authorize=None,
    ) -> AnswerResponse:
        answer_input = None
        missing = False
        if authorize is not None:
            await authorize()
        if answer_execution is None:
            run = await self._retrieve(
                request.as_search_request(), policy, frozen_policy=frozen_policy
            )
        else:
            run, evidence_map = await self._planned_retrieval(
                request.as_search_request(),
                policy,
                answer_execution,
                frozen_policy=frozen_policy,
                authorize=authorize,
            )
            answer_input = assemble_answer(
                template_id=answer_execution.template_id,
                template_version=answer_execution.template_version,
                template_digest=answer_execution.template_digest,
                original_question=request.query,
                standalone_question=answer_execution.standalone_question,
                evidence_map=evidence_map,
                output_requirements=answer_execution.output_requirements,
                queries=answer_execution.queries,
            )
            answer_input.context["planId"] = answer_execution.plan_id
            missing = bool(answer_input.context["missingEvidence"])
        original_evidence = run.response.evidence
        flow_context = flowchart_path_context(run.response.evidence)
        complete_edges = False
        has_flow_image = any(
            item.publication_id
            and item.approved_item_id
            and isinstance(item.source.anchor, DocumentAnchor)
            and item.source.anchor.bbox
            and item.content.startswith(("流程图节点 ", "流程图连线 "))
            for item in run.response.evidence
        )
        if has_flow_image and callable(getattr(self._search, "flowchart_edges", None)):
            topology = await self._retrieve(
                request.as_search_request(),
                policy,
                frozen_policy=frozen_policy,
                answer_plan_id=None if answer_execution is None else answer_execution.plan_id,
                authorize=authorize,
                exact_flowchart=True,
            )
            if self._publication_authority is not None and run.publication is not None:
                await self._publication_authority.revalidate(run.publication)
            # Every enumerated arrow retains its own citation. Image node snippets
            # must not consume the bounded answer's edge coverage.
            run = topology
            flow_context = flowchart_path_context(run.response.evidence)
            complete_edges = bool(flow_context)
        if flow_context:
            # A separate bounded query seeks textual business rules within exactly
            # the same selected, published authority as the original question.
            capacity = 20 - len(run.response.evidence)
            if capacity > 0:
                if authorize is not None:
                    await authorize()
                try:
                    rules = await self._retrieve(
                        replace(
                            request.as_search_request(),
                            query=(
                                f"{request.query} 业务规则 条件 阈值 "
                                "优先级 business rules thresholds"
                            ),
                            top_k=min(4, capacity),
                        ),
                        policy,
                        frozen_policy=frozen_policy,
                        answer_plan_id=None
                        if answer_execution is None
                        else answer_execution.plan_id,
                        authorize=authorize,
                    )
                    if self._publication_authority is not None and rules.publication is not None:
                        await self._publication_authority.revalidate(rules.publication)
                    if self._publication_authority is not None and run.publication is not None:
                        await self._publication_authority.revalidate(run.publication)
                    existing = {
                        (item.source.source_id, item.source.revision, item.chunk_id)
                        for item in run.response.evidence
                    }
                    merged = list(run.response.evidence)
                    for item in rules.response.evidence:
                        identity = (item.source.source_id, item.source.revision, item.chunk_id)
                        if identity not in existing and len(merged) < 20:
                            existing.add(identity)
                            merged.append(replace(item, evidence_label=f"S{len(merged) + 1}"))
                    publication = run.publication
                    if self._publication_authority is not None:
                        publication = await self._publication_authority.authorize_selection(
                            policy.project_id,
                            tuple(sorted({item.source.revision for item in merged})),
                        )
                    run = replace(
                        run,
                        response=replace(run.response, evidence=tuple(merged)),
                        publication=publication,
                    )
                except (SearchUnavailable, ModelUnavailable):
                    pass
            flow_context = flowchart_path_context(run.response.evidence)
            if complete_edges:
                flow_context["coverage"] = "complete-published-approved-edge-set"
                flow_context["instruction"] += (
                    " All published approved edges were enumerated. Excluded or unconfirmed "
                    "connections are not represented; this is not proof of the original process's "
                    "completeness. Paths may still be truncated by traversal bounds."
                )
            graph_context = (*graph_context, flow_context)
        if answer_execution is not None and flow_context:
            current_labels = {
                (item.source.source_id, item.source.revision, item.chunk_id): item.evidence_label
                for item in run.response.evidence
            }
            remapped = {
                item.evidence_label: current_labels.get(
                    (item.source.source_id, item.source.revision, item.chunk_id)
                )
                for item in original_evidence
            }
            evidence_map = {
                key: tuple(remapped[label] for label in labels if remapped.get(label))
                for key, labels in evidence_map.items()
            }
            answer_input = assemble_answer(
                template_id=answer_execution.template_id,
                template_version=answer_execution.template_version,
                template_digest=answer_execution.template_digest,
                original_question=request.query,
                standalone_question=answer_execution.standalone_question,
                evidence_map=evidence_map,
                output_requirements=answer_execution.output_requirements,
                queries=answer_execution.queries,
            )
            answer_input.context["planId"] = answer_execution.plan_id
            missing = bool(answer_input.context["missingEvidence"])
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
        if self._publication_authority is not None and run.publication is not None:
            await self._publication_authority.revalidate(run.publication)
        current = run.policy if frozen_policy else await self._verify_current(run.policy)
        self._validate_binding(current, run.plan, run.context_snapshot)
        if authorize is not None:
            await authorize()

        relation: RelationContext | None = None
        if self._relation_analysis is not None:
            relation = await self._relation_analysis.analyse(
                request.query,
                run.response.evidence,
                tuple(sorted({resource.revision for resource in run.plan.resources})),
                turn_id=None if answer_execution is None else answer_execution.plan_id,
            )
        if relation is not None and self._retrieval_augment and relation.augment_chunks:
            run, relation = await self._augment_from_relations(
                run,
                relation,
                request,
                policy,
                frozen_policy=frozen_policy,
                answer_plan_id=None if answer_execution is None else answer_execution.plan_id,
                authorize=authorize,
            )
        relation_first = answer_execution is not None and answer_execution.intent == "relation"

        generation_query = (
            (await self._redactor.redact(request.query)).sanitized_text
            if answer_execution is not None
            else run.plan.sanitized_query
        )
        use_extended_answer = (
            governance is not None
            or graph_context
            or model_alias is not None
            or answer_input is not None
            or relation is not None
        )
        generation = (
            await cast(Any, self._answers).answer(
                generation_query,
                run.response.evidence,
                run.response.retrieval_profile_id.value,
                governance=governance,
                graph_context=graph_context,
                model_alias=model_alias,
                relation_context=relation,
                relation_first=relation_first,
                **({"answer_input": answer_input} if answer_input is not None else {}),
            )
            if use_extended_answer
            else await self._answers.answer(
                run.plan.sanitized_query,
                run.response.evidence,
                run.response.retrieval_profile_id.value,
            )
        )
        current = current if frozen_policy else await self._verify_current(current)
        if authorize is not None:
            await authorize()
        self._validate_binding(current, run.plan, run.context_snapshot)
        if self._publication_authority is not None and run.publication is not None:
            await self._publication_authority.revalidate(run.publication)
        if flow_context and any(
            not flowchart_claim_is_consistent(claim.evidence_labels, flow_context)
            for claim in generation.claims
        ):
            return self._abstain(run.response, AbstentionReason.INSUFFICIENT_EVIDENCE)

        relation_outcome: RelationOutcome | None = None
        stripped = 0
        unresolvable = 0
        dropped_for_cap = 0
        extra_citation_ids: dict[str, str] = {}
        edge_citations: tuple[Citation, ...] = ()
        s_citations = [self._citation(item) for item in run.response.evidence]
        if relation is not None:
            reconciled = reconcile_relation_claims(
                generation, relation, graph_version=relation.graph_version
            )
            stripped = reconciled.stripped
            if reconciled.claims == () and generation.claims:
                return self._abstain(run.response, AbstentionReason.INSUFFICIENT_EVIDENCE)
            generation = replace(generation, text=reconciled.text, claims=reconciled.claims)
            if relation.status is RelationContextStatus.APPLIED:
                by_label = relation.by_label()
                used_labels = tuple(
                    dict.fromkeys(
                        label
                        for claim in generation.claims
                        for label in claim.evidence_labels
                        if label.startswith("R")
                    )
                )
                resolved: dict[str, Citation] = {}
                for label in used_labels:
                    relation_evidence = by_label.get(label)
                    if relation_evidence is None:
                        continue
                    built = self._edge_citation_for_relation(
                        relation_evidence,
                        run.response.evidence,
                        relation.graph_version or "",
                        run.plan.resources,
                    )
                    if built is None:
                        unresolvable += 1
                        continue
                    resolved[label] = built

                # The closed 20-citation bound (`ports/answers.py`) covers S and R
                # together; augmentation routinely pushes S toward 20 on its own.
                # Drop the lowest-ranked *uncited* S citations first (they cost
                # nothing claim-wise), then -- only if still over -- the
                # lowest-ranked cited R citations, stripping those labels from
                # claims and counting them as `stripped` (same bucket as an
                # invalid R citation: `invalid-relation-citation` / degraded).
                used_s_labels = {
                    label
                    for claim in generation.claims
                    for label in claim.evidence_labels
                    if label.startswith("S")
                }
                overflow = len(s_citations) + len(resolved) - _MAX_EVIDENCE
                if overflow > 0:
                    droppable_s = sorted(
                        (item for item in s_citations if item.evidence_label not in used_s_labels),
                        key=lambda item: _label_num(item.evidence_label),
                        reverse=True,
                    )
                    drop_ids = {item.citation_id for item in droppable_s[:overflow]}
                    if drop_ids:
                        s_citations = [
                            item for item in s_citations if item.citation_id not in drop_ids
                        ]
                    overflow = len(s_citations) + len(resolved) - _MAX_EVIDENCE
                if overflow > 0:
                    worst_r_labels = sorted(resolved, key=_label_num, reverse=True)[:overflow]
                    for label in worst_r_labels:
                        del resolved[label]
                    dropped_for_cap = len(worst_r_labels)
                    stripped += dropped_for_cap

                if unresolvable or dropped_for_cap:
                    final_claims: list[GeneratedClaim] = []
                    for claim in generation.claims:
                        kept = tuple(
                            label
                            for label in claim.evidence_labels
                            if label in resolved or not label.startswith("R")
                        )
                        if not kept:
                            continue
                        final_claims.append(
                            claim
                            if kept == claim.evidence_labels
                            else replace(claim, evidence_labels=kept)
                        )
                    generation = replace(
                        generation,
                        text="\n\n".join(claim.text for claim in final_claims),
                        claims=tuple(final_claims),
                    )
                    if not final_claims and reconciled.claims:
                        return self._abstain(run.response, AbstentionReason.INSUFFICIENT_EVIDENCE)
                # Renumber the surviving edge citations sequentially R1..Rn in
                # ascending original-rank order (not order of first appearance in
                # claim text), so the closed R-position rule in `ports/answers.py`
                # holds regardless of the original R-labels' gaps, and citing only
                # e.g. R2 always comes out as R1 -- deterministic and independent
                # of how the model ordered its claims.
                final_labels_sorted = sorted(resolved, key=_label_num)
                renumbered: list[Citation] = []
                for position, label in enumerate(final_labels_sorted, start=1):
                    citation = replace(resolved[label], evidence_label=f"R{position}")
                    renumbered.append(citation)
                    extra_citation_ids[label] = citation.citation_id
                edge_citations = tuple(renumbered)
            diagnostics = dict(relation.diagnostics)
            if unresolvable:
                diagnostics["relation-support-unresolvable"] = unresolvable
            relation_outcome = RelationOutcome(
                status=relation.status,
                graph_version=relation.graph_version,
                seed_count=len(relation.seeds),
                paths=relation.path_summaries(),
                relation_count=max(0, len(relation.relations) - unresolvable),
                diagnostics=diagnostics,
            )

        claims = _resolve_generated_claims(
            generation,
            run.response.evidence,
            id_factory=self._id_factory,
            extra_citation_ids=extra_citation_ids,
        )
        if claims is None:
            return self._abstain(run.response, AbstentionReason.INSUFFICIENT_EVIDENCE)
        if not generation.text and not claims:
            return self._abstain(run.response, AbstentionReason.INSUFFICIENT_EVIDENCE)

        cited_citation_ids = {citation_id for claim in claims for citation_id in claim.citation_ids}
        used_edge_citations = tuple(
            item for item in edge_citations if item.citation_id in cited_citation_ids
        )
        degradation_reasons = tuple(
            reason
            for reason, present in (
                ("partial-evidence", missing),
                ("invalid-relation-citation", stripped > 0),
                (
                    "relation-support-unresolvable",
                    relation_outcome is not None
                    and "relation-support-unresolvable" in relation_outcome.diagnostics,
                ),
            )
            if present
        )
        return AnswerResponse(
            trace_id=run.response.trace_id,
            query_plan_id=run.response.query_plan_id,
            context_snapshot_id=run.response.context_snapshot_id,
            corpus_version=run.response.corpus_version,
            retrieval_profile_id=run.response.retrieval_profile_id,
            answer=generation.text,
            abstained=False,
            claims=claims,
            citations=(
                *s_citations,
                *used_edge_citations,
            ),
            embedding_provenance=run.response.embedding_provenance,
            answer_provenance=self._generation_provenance(generation),
            degraded_mode=missing or stripped > 0 or unresolvable > 0,
            degradation_reasons=degradation_reasons,
            relation=relation_outcome,
        )

    async def _augment_from_relations(
        self,
        run: _RetrievalRun,
        relation: RelationContext,
        answer_request: AnswerRequest,
        policy: RetrievalPolicyContext,
        *,
        frozen_policy: bool,
        answer_plan_id: str | None,
        authorize,
    ) -> tuple[_RetrievalRun, RelationContext]:
        """Re-authorize `relation.augment_chunks` and fuse the surviving candidates
        into `run.response.evidence` through a normal, fully-ACL'd retrieval call,
        exactly like the existing flowchart business-rule supplement above -- this
        is what actually gates augmentation, not the agent (see Task 3 amendment)."""
        capacity = _MAX_EVIDENCE - len(run.response.evidence)
        if capacity <= 0:
            return run, relation
        # Only the revisions this query actually scoped for search (`run.plan`'s
        # resolved resources) -- a candidate granted by policy but outside this
        # query's own selection is "unselected", same as one with no grant at all.
        allowed_revisions = {resource.revision for resource in run.plan.resources}
        candidates = []
        for chunk in relation.augment_chunks:
            if chunk.source_revision_id not in allowed_revisions:
                continue
            if self._publication_authority is not None:
                try:
                    await self._publication_authority.authorize_selection(
                        policy.project_id, (chunk.source_revision_id,)
                    )
                except AuthorizationDenied:
                    continue
            candidates.append(chunk)
        if not candidates:
            return run, relation
        candidate_chunk_ids = {chunk.chunk_id for chunk in candidates}
        seed_labels = tuple(dict.fromkeys(seed.label for seed in relation.seeds))
        augment_query = (
            answer_request.query
            if not seed_labels
            else f"{answer_request.query} {' '.join(seed_labels)}"
        )
        if authorize is not None:
            await authorize()
        try:
            augmentation = await self._retrieve(
                replace(
                    answer_request.as_search_request(),
                    query=augment_query,
                    top_k=min(5, capacity),
                ),
                policy,
                frozen_policy=frozen_policy,
                answer_plan_id=answer_plan_id,
                authorize=authorize,
            )
        except (SearchUnavailable, ModelUnavailable):
            return run, relation
        if self._publication_authority is not None and augmentation.publication is not None:
            await self._publication_authority.revalidate(augmentation.publication)
        if self._publication_authority is not None and run.publication is not None:
            await self._publication_authority.revalidate(run.publication)
        existing = {
            (item.source.source_id, item.source.revision, item.chunk_id)
            for item in run.response.evidence
        }
        merged = list(run.response.evidence)
        for item in augmentation.response.evidence:
            identity = (item.source.source_id, item.source.revision, item.chunk_id)
            if (
                item.chunk_id in candidate_chunk_ids
                and identity not in existing
                and len(merged) < _MAX_EVIDENCE
            ):
                existing.add(identity)
                merged.append(replace(item, evidence_label=f"S{len(merged) + 1}"))
        if len(merged) == len(run.response.evidence):
            return run, relation
        publication = run.publication
        if self._publication_authority is not None:
            publication = await self._publication_authority.authorize_selection(
                policy.project_id,
                tuple(sorted({item.source.revision for item in merged})),
            )
        new_run = replace(
            run, response=replace(run.response, evidence=tuple(merged)), publication=publication
        )
        evidence_refs = tuple(_evidence_ref(item) for item in merged)
        return new_run, relation.rebind(evidence_refs)

    def _edge_citation_for_relation(
        self,
        relation: RelationEvidence,
        evidence: tuple[Evidence, ...],
        graph_version: str,
        resources: tuple[ResolvedResourceRef, ...],
    ) -> Citation | None:
        """Build one `kind="edge"` citation from the relation's first resolvable
        support. A support already cited as an `S` label has its chunk fields
        copied verbatim; a snippet-only support (never promoted to `S` by
        `_augment_from_relations`) is reconstructed from its own
        `RelationSupport` fields plus the matching selected revision's
        `source_content_hash` (Task 5 review I2) -- real formulas
        (`chunk_id_for`/`logical_chunk_id_for`), the same ones the search index
        itself uses, so the resulting identity is re-verifiable. A support
        whose anchor is not the `document` shape, or whose revision/document_id
        cannot be resolved, is skipped in favor of the next support."""
        evidence_by_label = {item.evidence_label: item for item in evidence}
        for support in relation.support:
            if support.evidence_label is None:
                continue
            source_item = evidence_by_label.get(support.evidence_label)
            if source_item is None:
                continue
            return Citation(
                family=source_item.family,
                citation_id=self._id_factory(),
                evidence_label=relation.label,
                chunk_id=source_item.chunk_id,
                logical_chunk_id=source_item.logical_chunk_id,
                source=source_item.source,
                chunk_content_hash=source_item.chunk_content_hash,
                content_role=source_item.content_role,
                kind="edge",
                edge=self._edge_fields(relation, graph_version),
            )
        resources_by_revision = {resource.revision: resource for resource in resources}
        for support in relation.support:
            if support.evidence_label is not None or support.document_id is None:
                continue
            resource = resources_by_revision.get(support.document_revision_id)
            if resource is None or resource.revision_kind is not RevisionKind.BLOB_VERSION:
                continue
            anchor = _document_anchor_from_mapping(support.anchor)
            if anchor is None:
                continue
            anchor_json = _canonical_document_anchor_json(anchor)
            source = SourceRevisionRef(
                source_id=resource.source_id,
                source_type="doc",
                revision_kind=resource.revision_kind,
                revision=resource.revision,
                source_content_hash=resource.source_content_hash,
                anchor=anchor,
            )
            chunk_id = str(
                chunk_id_for(RevisionId(resource.revision), anchor_json, support.content_digest)
            )
            logical_chunk_id = logical_chunk_projection_id(
                logical_chunk_id_for(DocumentId(support.document_id), anchor_json)
            )
            return Citation(
                family=SourceFamily.DOC,
                citation_id=self._id_factory(),
                evidence_label=relation.label,
                chunk_id=chunk_id,
                logical_chunk_id=logical_chunk_id,
                source=source,
                chunk_content_hash=support.content_digest,
                content_role=ContentRole.SOURCE,
                kind="edge",
                edge=self._edge_fields(relation, graph_version),
            )
        return None

    @staticmethod
    def _edge_fields(relation: RelationEvidence, graph_version: str) -> EdgeCitation:
        return EdgeCitation(
            edge_id=relation.edge_id,
            graph_version=graph_version,
            subject_node_id=relation.subject_node_id,
            subject_label=relation.subject_label,
            object_node_id=relation.object_node_id,
            object_label=relation.object_label,
            relation_type=relation.relation_type,
            relation_label=relation.relation_label,
        )

    async def _planned_retrieval(self, request, policy, execution, *, frozen_policy, authorize):
        if (
            execution.project_id != policy.project_id
            or execution.acl_digest != policy.acl_digest
            or execution.original_question != request.query
        ):
            raise AuthorizationDenied("answer plan authority changed")
        allowed_sources = {item.source_id for item in request.resource_refs}
        if any(not set(item.source_ids) <= allowed_sources for item in execution.queries):
            raise AuthorizationDenied("answer plan expands selected sources")
        profile = PROFILES[request.answer_mode]
        candidates = min(profile.candidate_limit, execution.candidate_limit)
        evidence_limit = min(profile.final_result_limit, execution.evidence_limit)
        count = len(execution.queries)
        if candidates < count:
            raise ValueError("shared candidate budget exhausted")
        results = {}
        pending = {item.id: item for item in execution.queries}

        async def retrieve_query(query, allocation):
            if authorize is not None:
                await authorize()
            if any(
                results.get(identity) is None or not results[identity].response.evidence
                for identity in query.depends_on
            ):
                return None
            try:
                return await self._retrieve(
                    replace(
                        request,
                        query=query.text,
                        top_k=allocation,
                        resource_refs=tuple(
                            ref
                            for ref in request.resource_refs
                            if ref.source_id in query.source_ids
                        ),
                    ),
                    policy,
                    frozen_policy=frozen_policy,
                    answer_plan_id=execution.plan_id,
                    authorize=authorize,
                )
            except (SearchUnavailable, ModelUnavailable):
                return None

        allocations = {
            item.id: candidates // count + (index < candidates % count)
            for index, item in enumerate(execution.queries)
        }
        while pending:
            ready = [item for item in pending.values() if set(item.depends_on) <= set(results)]
            if not ready:
                raise ValueError("invalid answer dependency graph")
            tasks = [
                asyncio.create_task(retrieve_query(item, allocations[item.id])) for item in ready
            ]
            try:
                runs = await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            for item, run in zip(ready, runs, strict=True):
                results[item.id] = run
                del pending[item.id]
        successful = [run for run in results.values() if run is not None]
        if not successful:
            raise SearchUnavailable("all planned queries unavailable")
        evidence = []
        labels = {}
        per_query = {}
        for item in execution.queries:
            run = results[item.id]
            if run is not None:
                if self._publication_authority is not None and run.publication is not None:
                    await self._publication_authority.revalidate(run.publication)
            per_query[item.id] = () if run is None else run.response.evidence
        # Round-robin by rank reserves coverage across necessary subquestions before
        # taking another result from any one question; deduplication shares that capacity.
        for rank in range(max(map(len, per_query.values()), default=0)):
            for hits in per_query.values():
                if rank < len(hits):
                    hit = hits[rank]
                    identity = (hit.source.source_id, hit.source.revision, hit.chunk_id)
                    if identity not in labels and len(evidence) < evidence_limit:
                        labels[identity] = f"S{len(evidence) + 1}"
                        evidence.append(replace(hit, evidence_label=labels[identity]))
        evidence_map = {
            query_id: tuple(
                labels[identity]
                for hit in hits
                if (identity := (hit.source.source_id, hit.source.revision, hit.chunk_id)) in labels
            )
            for query_id, hits in per_query.items()
        }
        base = successful[0]
        publication = base.publication
        if self._publication_authority is not None:
            publication = await self._publication_authority.authorize_selection(
                policy.project_id,
                tuple(
                    sorted(
                        {
                            revision
                            for run in successful
                            if run.publication is not None
                            for revision in run.publication.source_revision_ids
                        }
                    )
                ),
            )
        return replace(
            base, response=replace(base.response, evidence=tuple(evidence)), publication=publication
        ), evidence_map

    async def _retrieve(
        self,
        request: SearchRequest,
        policy: RetrievalPolicyContext,
        *,
        frozen_policy: bool = False,
        answer_plan_id: str | None = None,
        authorize=None,
        exact_flowchart: bool = False,
    ) -> _RetrievalRun:
        with span("retrieval.search") as current_span:
            run = await self._retrieve_impl(
                request,
                policy,
                frozen_policy=frozen_policy,
                answer_plan_id=answer_plan_id,
                authorize=authorize,
                exact_flowchart=exact_flowchart,
            )
            evidence = run.response.evidence
            current_span.set_attribute("tap.retrieval.hit_count", len(evidence))
            current_span.set_attribute(
                "tap.retrieval.chunk_ids", [item.chunk_id for item in evidence[:32]]
            )
            current_span.set_attribute(
                "tap.retrieval.document_ids", [item.source.source_id for item in evidence[:32]]
            )
            current_span.set_attribute(
                "tap.retrieval.scores", [item.score for item in evidence[:32]]
            )
            current_span.set_attribute("tap.retrieval.exact_flowchart", exact_flowchart)
            return run

    async def _retrieve_impl(
        self,
        request: SearchRequest,
        policy: RetrievalPolicyContext,
        *,
        frozen_policy: bool = False,
        answer_plan_id: str | None = None,
        authorize=None,
        exact_flowchart: bool = False,
    ) -> _RetrievalRun:
        if (
            frozen_policy
            and self._models is not None
            and (
                self._models.scope.enterprise_id != policy.tenant_id
                or self._models.scope.project_id != policy.project_id
            )
        ):
            raise AuthorizationDenied("model gateway Project does not match retrieval policy")
        current = policy if frozen_policy else await self._verify_current(policy)
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
            embedding_model_id=self._embeddings.embedding_model_id,
            embedding_dimension=self._embeddings.embedding_dimension,
            answer_plan_id=answer_plan_id,
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

        current = current if frozen_policy else await self._verify_current(current)
        self._validate_binding(current, plan, context_snapshot)
        if authorize is not None:
            await authorize()
        embedding = await self._embeddings.embed(plan.sanitized_query)
        self._validate_embedding(embedding, plan)
        current = current if frozen_policy else await self._verify_current(current)
        self._validate_binding(current, plan, context_snapshot)
        if authorize is not None:
            await authorize()
        publication = None
        if self._publication_authority is not None:
            publication = await self._publication_authority.authorize_selection(
                current.project_id, tuple(resource.revision for resource in plan.resources)
            )
        flowcharts = (
            None
            if self._flowchart_gate is None
            else await self._flowchart_gate.current(current.project_id)
        )
        approved_item_scope = (
            None
            if publication is None
            else tuple(
                (revision, publication.for_revision(revision).approved_item_ids)
                for revision in sorted({resource.revision for resource in plan.resources})
            )
        )
        if exact_flowchart and publication is None and flowcharts is not None:
            approved_item_scope = tuple(
                (revision, flowcharts.for_revision(revision).approved_item_ids)
                for revision in sorted({resource.revision for resource in plan.resources})
                if revision in flowcharts.source_revision_ids
            )
        search_method = (
            getattr(self._search, "flowchart_edges") if exact_flowchart else self._search.search
        )
        hits = await search_method(
            SearchExecution(
                policy=current,
                plan=plan,
                context_snapshot=context_snapshot,
                query_vector=embedding.vector,
                approved_item_scope=approved_item_scope,
            )
        )
        current = current if frozen_policy else await self._verify_current(current)
        self._validate_binding(current, plan, context_snapshot)
        if not all(self._hit_is_in_execution(hit, plan) for hit in hits):
            raise AuthorizationDenied("Search returned evidence outside bound execution")
        if self._publication_authority is not None and publication is not None:
            await self._publication_authority.authorize_hits(current.project_id, hits)
            await self._publication_authority.revalidate(publication)
        authorized_hits = (
            hits
            if self._flowchart_gate is None
            else tuple(hit for hit in hits if _flowchart_hit_is_approved(hit, flowcharts))
        )
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
                publication
                if publication is not None
                or flowcharts is None
                or hit.source.revision not in flowcharts.source_revision_ids
                else flowcharts,
            )
            for position, hit in enumerate(
                ordered_hits[
                    : 20 if exact_flowchart else min(profile.final_result_limit, candidate_limit)
                ],
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
            publication=publication,
        )

    async def _verify_current(
        self,
        expected: RetrievalPolicyContext,
    ) -> RetrievalPolicyContext:
        if self._models is not None and (
            self._models.scope.enterprise_id != expected.tenant_id
            or self._models.scope.project_id != expected.project_id
        ):
            raise AuthorizationDenied("model gateway Project does not match retrieval policy")
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
            or plan.embedding_model_id != self._embeddings.embedding_model_id
            or plan.embedding_dimension != self._embeddings.embedding_dimension
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
        grants = tuple(
            candidate
            for candidate in policy.resource_grants
            if candidate.family == resource.family.value
            and candidate.source_id == resource.source_id
            and (
                resource.requested_revision is None
                or candidate.revision == resource.requested_revision
            )
        )
        if len(grants) != 1:
            raise AuthorizationDenied("resource revision is unavailable, ambiguous or unauthorized")
        grant = grants[0]
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
        publication: PublicationBinding | None,
    ) -> Evidence:
        if publication is not None:
            publication = publication.for_revision(hit.source.revision)
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
            publication_id=None if publication is None else publication.publication_id,
            approval_digest=None if publication is None else publication.approval_digest,
            approved_item_id=(
                hit.source.anchor.inventory_item_id
                if publication is not None and isinstance(hit.source.anchor, DocumentAnchor)
                else None
            ),
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
            or not isinstance(hit.content, str)
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
            and hit.chunk_content_hash == AuthorizedRetrieval._text_hash(hit.content)
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
            publication_id=evidence.publication_id,
            approval_digest=evidence.approval_digest,
            approved_item_id=evidence.approved_item_id,
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
        values_by_identity: dict[tuple[str, str], set[tuple[tuple[str, str, str], str]]] = {}
        for item in evidence:
            source_revision = (
                item.source.source_id,
                item.source.revision,
                item.source.source_content_hash,
            )
            identities = [("logical-chunk", item.logical_chunk_id)]
            anchor = item.source.anchor
            if isinstance(anchor, DocumentAnchor) and anchor.heading_path:
                identities.append(("document-heading", anchor.heading_path[-1].casefold()))
            for identity in identities:
                values_by_identity.setdefault(identity, set()).add(
                    (source_revision, item.chunk_content_hash)
                )
        for identity, values in values_by_identity.items():
            if identity[0] == "logical-chunk":
                if len({content_hash for _, content_hash in values}) > 1:
                    return True
                continue
            hashes_by_source: dict[tuple[str, str, str], set[str]] = {}
            for source, content_hash in values:
                hashes_by_source.setdefault(source, set()).add(content_hash)
            source_hashes = list(hashes_by_source.values())
            if any(
                source_hashes[left].isdisjoint(source_hashes[right])
                for left in range(len(source_hashes))
                for right in range(left + 1, len(source_hashes))
            ):
                return True
        return False

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


def _flowchart_hit_is_approved(hit: SearchHit, flowcharts: PublicationBinding | None) -> bool:
    """Image-region evidence answers only as an approved item of a published review."""
    anchor = hit.source.anchor
    if not isinstance(anchor, DocumentAnchor) or not anchor.bbox:
        return True
    if flowcharts is None or hit.source.revision not in flowcharts.source_revision_ids:
        return False
    return (
        anchor.inventory_item_id in flowcharts.for_revision(hit.source.revision).approved_item_ids
    )
