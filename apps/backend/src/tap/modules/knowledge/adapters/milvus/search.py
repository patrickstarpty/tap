"""Strict Milvus implementation of the provider-neutral Knowledge SearchPort."""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Mapping
from typing import Literal

from tap.modules.access.domain.policy import ResourceGrant
from tap.modules.knowledge.adapters.milvus.audit import (
    MilvusSearchAuditEvent,
    SearchAuditSink,
)
from tap.modules.knowledge.adapters.milvus.config import (
    MilvusIndexTarget,
    MilvusSearchConfig,
)
from tap.modules.knowledge.adapters.milvus.filter import compile_milvus_filter
from tap.modules.knowledge.adapters.milvus.mapping import map_milvus_hit
from tap.modules.knowledge.adapters.milvus.targets import BoundMilvusTarget, bind_target
from tap.modules.knowledge.adapters.milvus.transport import (
    MILVUS_OUTPUT_FIELDS,
    MilvusChannelRequest,
    MilvusHybridRequest,
    MilvusReader,
)
from tap.modules.knowledge.domain.models import (
    FilterableSubtree,
    ResolvedResourceRef,
    ResourceMode,
    SourceFamily,
    anchor_authorization_key,
    context_snapshot_binds_query_plan,
)
from tap.modules.knowledge.ports.errors import (
    SearchBoundsExceeded,
    SearchUnavailable,
)
from tap.modules.knowledge.ports.models import SearchExecution, SearchHit
from tap.modules.knowledge.ports.search import SearchPort


class MilvusSearchAdapter(SearchPort):
    """Execute one doc-only, bounded hybrid query against one freshly bound target."""

    def __init__(
        self,
        config: MilvusSearchConfig,
        reader: MilvusReader,
        audit_sink: SearchAuditSink,
    ) -> None:
        if not isinstance(config, MilvusSearchConfig):
            raise TypeError("Milvus search requires validated configuration")
        if not callable(getattr(reader, "hybrid_search", None)):
            raise TypeError("Milvus search requires a reader")
        if not callable(getattr(audit_sink, "emit", None)):
            raise TypeError("Milvus search requires an audit sink")
        self._config = config
        self._reader = reader
        self._audit_sink = audit_sink
        audit_target = config.targets.get(SourceFamily.DOC)
        self._audit_target = audit_target if isinstance(audit_target, MilvusIndexTarget) else None

    async def search(self, execution: SearchExecution) -> tuple[SearchHit, ...]:
        started = time.monotonic()
        physical_collection: str | None = None
        provider_row_count = 0
        rejected_row_count = 0
        provider_request_ids: tuple[str, ...] = ()
        try:
            target = self._validate_execution(execution)
            filter_expression = compile_milvus_filter(
                execution,
                SourceFamily.DOC,
                max_bytes=self._config.max_filter_bytes,
            )
            bound = await bind_target(self._reader, target)
            physical_collection = str(bound.physical_collection)
            request = _hybrid_request(
                execution,
                bound,
                filter_expression,
                min(execution.plan.candidate_limit, self._config.candidate_limit),
            )
            rows = await self._reader.hybrid_search(request)
            if not isinstance(rows, tuple):
                raise SearchUnavailable("search provider returned a malformed result page")
            provider_row_count = len(rows)
            if provider_row_count > request.limit:
                rejected_row_count = provider_row_count
                raise SearchUnavailable("search provider returned too many rows")
            try:
                hits = tuple(
                    map_milvus_hit(row, bound, local_rank)
                    for local_rank, row in enumerate(rows, start=1)
                )
            except SearchUnavailable:
                rejected_row_count = provider_row_count
                raise
            provider_request_ids = _provider_request_ids(hits)
        except SearchBoundsExceeded as error:
            await self._emit_failure_without_masking(
                execution,
                started=started,
                physical_collection=physical_collection,
                provider_row_count=provider_row_count,
                rejected_row_count=rejected_row_count,
                provider_request_ids=provider_request_ids,
                error_code="bounds",
            )
            raise error
        except SearchUnavailable as error:
            await self._emit_failure_without_masking(
                execution,
                started=started,
                physical_collection=physical_collection,
                provider_row_count=provider_row_count,
                rejected_row_count=rejected_row_count,
                provider_request_ids=provider_request_ids,
                error_code="unavailable",
            )
            raise error
        except Exception:
            unavailable = SearchUnavailable("search provider is unavailable")
            await self._emit_failure_without_masking(
                execution,
                started=started,
                physical_collection=physical_collection,
                provider_row_count=provider_row_count,
                rejected_row_count=rejected_row_count,
                provider_request_ids=provider_request_ids,
                error_code="unavailable",
            )
            raise unavailable from None

        event = self._event(
            execution,
            outcome="success",
            started=started,
            physical_collection=physical_collection,
            provider_row_count=provider_row_count,
            rejected_row_count=0,
            provider_request_ids=provider_request_ids,
            error_code=None,
        )
        try:
            await self._audit_sink.emit(event)
        except Exception:
            raise SearchUnavailable("search audit is unavailable") from None
        return hits

    async def close(self) -> None:
        """Close the owned reader; the reader keeps closure terminal and idempotent."""
        await self._reader.close()

    def _validate_execution(self, execution: SearchExecution) -> MilvusIndexTarget:
        if not isinstance(execution, SearchExecution):
            raise SearchBoundsExceeded("search execution is outside the closed contract")
        plan = execution.plan
        policy = execution.policy
        if plan.source_families != (SourceFamily.DOC,):
            raise SearchBoundsExceeded("source family is not configured")
        if (
            not isinstance(self._config.targets, Mapping)
            or len(self._config.targets) != 1
            or tuple(self._config.targets) != (SourceFamily.DOC,)
        ):
            raise SearchBoundsExceeded("Milvus target selection is outside the bound")
        target = self._config.targets.get(SourceFamily.DOC)
        if not isinstance(target, MilvusIndexTarget) or target.family is not SourceFamily.DOC:
            raise SearchBoundsExceeded("Milvus target selection is outside the bound")
        if (
            (
                policy.tenant_id,
                policy.project_id,
                policy.decision_id,
                policy.policy_version,
                policy.acl_digest,
            )
            != (
                plan.tenant_id,
                plan.project_id,
                plan.policy_decision_id,
                plan.policy_version,
                plan.acl_digest,
            )
            or not context_snapshot_binds_query_plan(plan, execution.context_snapshot)
            or plan.corpus_version != policy.active_corpus_version
            or plan.sanitized_query_hash != _sha256(plan.sanitized_query)
        ):
            raise SearchUnavailable("policy, plan, and context snapshot are not bound")
        if not policy.actor.allowed_group_ids or not policy.allowed_classifications:
            raise SearchUnavailable("policy has no authorized ACL values")
        if (
            plan.effective_environment is not None
            and plan.effective_environment not in policy.allowed_environments
        ):
            raise SearchUnavailable("execution environment no longer matches current policy")
        if SourceFamily.DOC.value not in policy.allowed_source_families:
            raise SearchUnavailable("execution source family no longer matches current policy")
        if type(plan.candidate_limit) is not int or not 1 <= plan.candidate_limit <= 100:
            raise SearchBoundsExceeded("candidate limit is outside the execution bound")
        if len(plan.resources) > 20:
            raise SearchBoundsExceeded("resource scope exceeds the execution bound")
        if not all(
            _resource_matches_policy(resource, policy.resource_grants)
            for resource in plan.resources
        ):
            raise SearchUnavailable("execution contains a resource absent from current policy")
        scoped = tuple(
            resource for resource in plan.resources if resource.mode is ResourceMode.SCOPE
        )
        if scoped and not any(resource.family is SourceFamily.DOC for resource in scoped):
            raise SearchUnavailable("global scope does not cover the selected family")
        if (
            not isinstance(execution.query_vector, tuple)
            or not execution.query_vector
            or len(execution.query_vector) != plan.embedding_dimension
            or len(execution.query_vector) != target.vector_dimension
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in execution.query_vector
            )
        ):
            raise SearchBoundsExceeded("query vector is outside the execution bound")
        if (
            target.embedding_model_version != plan.embedding_model_id
            or target.vector_dimension != plan.embedding_dimension
        ):
            raise SearchUnavailable("query and index vector spaces do not match")
        return target

    async def _emit_failure_without_masking(
        self,
        execution: object,
        *,
        started: float,
        physical_collection: str | None,
        provider_row_count: int,
        rejected_row_count: int,
        provider_request_ids: tuple[str, ...],
        error_code: Literal["unavailable", "bounds"],
    ) -> None:
        event = self._event(
            execution,
            outcome="failure",
            started=started,
            physical_collection=physical_collection,
            provider_row_count=provider_row_count,
            rejected_row_count=rejected_row_count,
            provider_request_ids=provider_request_ids,
            error_code=error_code,
        )
        try:
            await self._audit_sink.emit(event)
        except Exception:
            return

    def _event(
        self,
        execution: object,
        *,
        outcome: Literal["success", "failure"],
        started: float,
        physical_collection: str | None,
        provider_row_count: int,
        rejected_row_count: int,
        provider_request_ids: tuple[str, ...],
        error_code: Literal["unavailable", "bounds"] | None,
    ) -> MilvusSearchAuditEvent:
        plan = getattr(execution, "plan", None)
        target = self._audit_target
        return MilvusSearchAuditEvent(
            outcome=outcome,
            provider="milvus",
            query_plan_id=_audit_string(getattr(plan, "query_plan_id", None)),
            acl_digest=_audit_string(getattr(plan, "acl_digest", None)),
            alias=target.alias if target is not None else "unconfigured",
            physical_collection=physical_collection,
            schema_version=target.schema_version if target is not None else "unconfigured",
            corpus_version=target.corpus_version if target is not None else "unconfigured",
            embedding_model_version=(
                target.embedding_model_version if target is not None else "unconfigured"
            ),
            provider_row_count=provider_row_count,
            rejected_row_count=rejected_row_count,
            elapsed_milliseconds=max(0, int((time.monotonic() - started) * 1_000)),
            provider_request_ids=provider_request_ids,
            error_code=error_code,
        )


def _hybrid_request(
    execution: SearchExecution,
    bound: BoundMilvusTarget,
    filter_expression: str,
    limit: int,
) -> MilvusHybridRequest:
    return MilvusHybridRequest(
        collection_name=bound.physical_collection,
        channels=(
            MilvusChannelRequest(
                kind="bm25",
                query=execution.plan.sanitized_query,
                filter_expression=filter_expression,
                limit=limit,
            ),
            MilvusChannelRequest(
                kind="dense",
                query=execution.query_vector,
                filter_expression=filter_expression,
                limit=limit,
            ),
        ),
        output_fields=MILVUS_OUTPUT_FIELDS,
        limit=limit,
    )


def _resource_matches_policy(
    resource: ResolvedResourceRef,
    grants: tuple[ResourceGrant, ...],
) -> bool:
    if not isinstance(resource, ResolvedResourceRef):
        return False
    matching = next(
        (
            grant
            for grant in grants
            if grant.family == resource.family.value
            and grant.source_id == resource.source_id
            and grant.revision_kind == resource.revision_kind.value
            and grant.revision == resource.revision
            and grant.source_content_hash == resource.source_content_hash
        ),
        None,
    )
    if matching is None:
        return False
    if resource.anchor is None:
        return resource.subtree is None
    key = anchor_authorization_key(resource.anchor)
    if not matching.allow_all_anchors and key not in matching.allowed_anchor_keys:
        return False
    expected_subtree = next(
        (subtree for subtree in matching.subtree_grants if subtree.anchor_key == key),
        None,
    )
    if resource.subtree is None:
        return expected_subtree is None and resource.mode is not ResourceMode.SCOPE
    return expected_subtree is not None and resource.subtree == FilterableSubtree(
        root_ids=expected_subtree.root_ids,
        parent_ids=expected_subtree.parent_ids,
        logical_chunk_ids=expected_subtree.logical_chunk_ids,
    )


def _provider_request_ids(hits: tuple[SearchHit, ...]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            hit.provider_request_id for hit in hits if hit.provider_request_id is not None
        )
    )


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _audit_string(value: object) -> str:
    return value if isinstance(value, str) and value else "unbound"
