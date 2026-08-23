"""Bounded Azure AI Search REST adapter with mandatory server-side ACL filters."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, cast
from urllib.parse import quote
from uuid import uuid4

import httpx

from tap.modules.access.domain.policy import Classification, ResourceGrant
from tap.modules.knowledge.domain.models import (
    BddAnchor,
    CodeAnchor,
    ContentRole,
    DocumentAnchor,
    FailureAnchor,
    FilterableSubtree,
    IndexRevision,
    OpenApiAnchor,
    QueryPlan,
    ResolvedResourceRef,
    ResourceMode,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
    StructuralAnchor,
    anchor_authorization_key,
    context_snapshot_binds_query_plan,
)
from tap.modules.knowledge.ports.models import SearchExecution, SearchHit

AZURE_SEARCH_API_VERSION = "2026-04-01"
SEARCH_SELECT_FIELDS = (
    "indexFamily,chunkId,logicalChunkId,rootId,parentId,title,content,sourceId,sourceType,"
    "sourceRevision,anchorJson,sourceContentHash,chunkContentHash,contentRole,"
    "derivedFromChunkIds,corpusVersion,schemaVersion,embeddingModelVersion"
)
CLASSIFICATION_ORDER = (
    Classification.PUBLIC,
    Classification.INTERNAL,
    Classification.CONFIDENTIAL,
    Classification.RESTRICTED,
)
CHUNK_ID_PATTERN = re.compile(r"^h_[0-9a-f]{64}$")
SAFE_INDEX_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class SearchBoundsExceeded(Exception):
    """The selected execution would exceed a configured search bound."""


class SearchUnavailable(Exception):
    """A selected index could not return one complete, authorized result page."""


class AzureBearerTokenProvider(Protocol):
    async def get_token(self) -> str: ...


class _RetryableSearchError(Exception):
    pass


class _SearchTransportError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _SearchResult:
    rows: tuple[Mapping[str, Any], ...]
    request_id: str | None
    partial: bool


class _SearchClient(Protocol):
    async def search(self, **kwargs: Any) -> Any: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class AzureIndexTarget:
    query_index: str
    physical_index: str
    schema_version: str
    embedding_model_id: str
    vector_dimension: int

    def __post_init__(self) -> None:
        for name in ("query_index", "physical_index"):
            value = getattr(self, name)
            if not isinstance(value, str) or not SAFE_INDEX_PATTERN.fullmatch(value):
                raise ValueError(f"{name} must be an explicit safe Azure index identity")
        for name in ("schema_version", "embedding_model_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 128:
                raise ValueError(f"{name} must be a bounded server identifier")
        _strict_int("vector_dimension", self.vector_dimension, minimum=1, maximum=4_096)


@dataclass(frozen=True, slots=True)
class AzureSearchConfig:
    endpoint: str
    indexes: Mapping[SourceFamily, AzureIndexTarget]
    bearer_token_provider: AzureBearerTokenProvider | None = field(default=None, repr=False)
    query_api_key: str | None = field(default=None, repr=False)
    allow_query_key_auth: bool = False
    max_fan_out: int = 4
    per_index_candidates: int = 50
    max_connections: int = 4
    deadline_seconds: float = 8
    max_retries: int = 1
    connect_timeout_seconds: float = 2
    read_timeout_seconds: float = 5
    max_rows: int = 50
    max_request_bytes: int = 262_144
    max_response_bytes: int = 4_194_304
    max_content_chars: int = 100_000
    max_derived_ids: int = 64
    max_anchor_bytes: int = 16_384
    max_identifier_chars: int = 512

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, str) or not self.endpoint.startswith("https://"):
            raise ValueError("Azure Search endpoint must use HTTPS")
        if type(self.allow_query_key_auth) is not bool:
            raise TypeError("allow_query_key_auth must be a boolean")
        has_bearer = self.bearer_token_provider is not None
        has_query_key = isinstance(self.query_api_key, str) and bool(self.query_api_key)
        if has_bearer == has_query_key:
            raise ValueError("configure exactly one Azure Search credential mode")
        if has_query_key and not self.allow_query_key_auth:
            raise ValueError("query-key authentication requires an explicit compatibility opt-in")
        if has_bearer and not callable(getattr(self.bearer_token_provider, "get_token", None)):
            raise TypeError("bearer token provider must expose async get_token")

        _strict_int("max_fan_out", self.max_fan_out, minimum=1, maximum=len(SourceFamily))
        _strict_int(
            "per_index_candidates",
            self.per_index_candidates,
            minimum=1,
            maximum=100,
        )
        _strict_int("max_connections", self.max_connections, minimum=1, maximum=16)
        _strict_int("max_retries", self.max_retries, minimum=0, maximum=2)
        _strict_int("max_rows", self.max_rows, minimum=1, maximum=100)
        _strict_int("max_request_bytes", self.max_request_bytes, minimum=128, maximum=2_097_152)
        _strict_int(
            "max_response_bytes",
            self.max_response_bytes,
            minimum=256,
            maximum=16_777_216,
        )
        _strict_int(
            "max_content_chars",
            self.max_content_chars,
            minimum=1,
            maximum=1_000_000,
        )
        _strict_int("max_derived_ids", self.max_derived_ids, minimum=0, maximum=256)
        _strict_int("max_anchor_bytes", self.max_anchor_bytes, minimum=128, maximum=65_536)
        _strict_int(
            "max_identifier_chars",
            self.max_identifier_chars,
            minimum=64,
            maximum=2_048,
        )
        _finite_duration("deadline_seconds", self.deadline_seconds, maximum=30)
        _finite_duration("connect_timeout_seconds", self.connect_timeout_seconds, maximum=10)
        _finite_duration("read_timeout_seconds", self.read_timeout_seconds, maximum=30)

        if not isinstance(self.indexes, Mapping) or not self.indexes or len(self.indexes) > 4:
            raise ValueError("Azure Search must configure one to four index targets")
        if not all(
            isinstance(family, SourceFamily) and isinstance(target, AzureIndexTarget)
            for family, target in self.indexes.items()
        ):
            raise TypeError("Azure Search targets must use closed source families")
        object.__setattr__(self, "indexes", MappingProxyType(dict(self.indexes)))


class _AzureRestClient:
    """httpx transport details contained entirely inside the Azure adapter."""

    def __init__(
        self,
        config: AzureSearchConfig,
        index: str,
        *,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._index = index
        transport = http_transport or httpx.AsyncHTTPTransport(retries=0)
        self._client = httpx.AsyncClient(
            base_url=config.endpoint.rstrip("/") + "/",
            headers={"content-type": "application/json"},
            timeout=httpx.Timeout(
                connect=min(config.connect_timeout_seconds, config.deadline_seconds),
                read=min(config.read_timeout_seconds, config.deadline_seconds),
                write=min(config.read_timeout_seconds, config.deadline_seconds),
                pool=min(config.connect_timeout_seconds, config.deadline_seconds),
            ),
            limits=httpx.Limits(
                max_connections=config.max_connections,
                max_keepalive_connections=config.max_connections,
            ),
            transport=transport,
        )

    async def search(self, **kwargs: Any) -> _SearchResult:
        encoded = _encoded_search_payload(kwargs)
        if len(encoded) > self._config.max_request_bytes:
            raise SearchBoundsExceeded("Azure Search request exceeds the byte bound")
        headers = {
            "x-ms-client-request-id": _required_runtime_string(
                kwargs["client_request_id"], maximum=128
            ),
            "return-client-request-id": "true",
        }
        if self._config.bearer_token_provider is not None:
            token = await self._config.bearer_token_provider.get_token()
            if not isinstance(token, str) or not token or len(token) > 16_384:
                raise _SearchTransportError("Azure bearer token provider returned no usable token")
            headers["authorization"] = f"Bearer {token}"
        else:
            assert self._config.query_api_key is not None
            headers["api-key"] = self._config.query_api_key

        async with self._client.stream(
            "POST",
            f"indexes/{quote(self._index, safe='')}/docs/search",
            params={"api-version": AZURE_SEARCH_API_VERSION},
            headers=headers,
            content=encoded,
        ) as response:
            if response.status_code in {408, 429} or response.status_code >= 500:
                raise _RetryableSearchError(f"Azure Search returned HTTP {response.status_code}")
            if response.is_error:
                raise _SearchTransportError(f"Azure Search returned HTTP {response.status_code}")
            raw = await _read_bounded(response, self._config.max_response_bytes)
            request_id = _first_header(
                response,
                ("request-id", "x-ms-request-id", "x-ms-client-request-id"),
            )
        try:
            body = json.loads(raw, parse_constant=_reject_json_constant)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            raise _SearchTransportError("Azure Search returned malformed JSON") from error
        if not isinstance(body, dict):
            raise _SearchTransportError("Azure Search returned a malformed result page")
        rows = body.get("value")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise _SearchTransportError("Azure Search returned a malformed result page")
        if len(rows) > self._config.max_rows:
            raise _SearchTransportError("Azure Search result row count exceeds the bound")
        partial = False
        if "@odata.nextLink" in body:
            next_link = body["@odata.nextLink"]
            if not isinstance(next_link, str) or not next_link or len(next_link) > 8_192:
                raise _SearchTransportError("Azure Search returned a malformed pagination marker")
            partial = True
        return _SearchResult(
            rows=tuple(cast(Mapping[str, Any], row) for row in rows),
            request_id=request_id,
            partial=partial,
        )

    async def close(self) -> None:
        await self._client.aclose()


class AzureAISearchAdapter:
    """Execute one strict bounded fan-out without accepting caller-authored filters."""

    def __init__(
        self,
        config: AzureSearchConfig,
        *,
        client_factory: Callable[[str], _SearchClient] | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        factory = client_factory or (
            lambda index: _AzureRestClient(
                config,
                index,
                http_transport=http_transport,
            )
        )
        self._clients = {
            family: factory(target.query_index) for family, target in config.indexes.items()
        }
        self._connection_slots = asyncio.Semaphore(config.max_connections)

    async def search(self, execution: SearchExecution) -> tuple[SearchHit, ...]:
        loop = asyncio.get_running_loop()
        deadline_at = loop.time() + self._config.deadline_seconds
        try:
            async with asyncio.timeout_at(deadline_at):
                self._validate_execution(execution)
                plan = execution.plan
                if len(plan.source_families) > self._config.max_fan_out:
                    raise SearchBoundsExceeded(
                        "selected source fan-out exceeds the configured bound"
                    )
                if any(family not in self._clients for family in plan.source_families):
                    raise SearchUnavailable("a selected source family has no configured index")
                filters = {
                    family: self._security_filter(execution, family)
                    for family in plan.source_families
                }
                if loop.time() >= deadline_at:
                    raise TimeoutError
                tasks = tuple(
                    asyncio.create_task(
                        self._query_family(
                            execution,
                            family,
                            filters[family],
                            deadline_at=deadline_at,
                        )
                    )
                    for family in plan.source_families
                )
                try:
                    family_hits = await asyncio.gather(*tasks)
                except BaseException:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise
                if loop.time() >= deadline_at:
                    raise TimeoutError
        except TimeoutError as error:
            raise SearchUnavailable("Azure Search exceeded the outer deadline") from error
        return tuple(hit for hits in family_hits for hit in hits)

    async def close(self) -> None:
        await asyncio.gather(*(client.close() for client in self._clients.values()))

    def _validate_execution(self, execution: SearchExecution) -> None:
        if not isinstance(execution, SearchExecution):
            raise SearchUnavailable("Azure Search requires a bound execution")
        policy = execution.policy
        plan = execution.plan
        snapshot = execution.context_snapshot
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
        if (
            not plan.source_families
            or len(set(plan.source_families)) != len(plan.source_families)
            or not {family.value for family in plan.source_families}
            <= policy.allowed_source_families
        ):
            raise SearchUnavailable("execution source family no longer matches current policy")
        if type(plan.candidate_limit) is not int or not 1 <= plan.candidate_limit <= 100:
            raise SearchBoundsExceeded("candidate limit is outside the execution bound")
        if len(plan.resources) > 20:
            raise SearchBoundsExceeded("resource scope exceeds the execution bound")
        if not all(
            self._resource_matches_policy(resource, policy.resource_grants)
            for resource in plan.resources
        ):
            raise SearchUnavailable("execution contains a resource absent from current policy")
        scoped = tuple(
            resource for resource in plan.resources if resource.mode is ResourceMode.SCOPE
        )
        if scoped and any(
            not any(resource.family is family for resource in scoped)
            for family in plan.source_families
        ):
            raise SearchUnavailable("global scope does not cover every selected family")
        if (
            not isinstance(execution.query_vector, tuple)
            or len(execution.query_vector) != plan.embedding_dimension
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in execution.query_vector
            )
        ):
            raise SearchBoundsExceeded("query vector is outside the execution bound")
        for family in plan.source_families:
            target = self._config.indexes.get(family)
            if target is None:
                raise SearchUnavailable("selected family has no server index target")
            if (
                target.embedding_model_id != plan.embedding_model_id
                or target.vector_dimension != plan.embedding_dimension
            ):
                raise SearchUnavailable("query and index vector spaces do not match")

    @staticmethod
    def _resource_matches_policy(
        resource: ResolvedResourceRef,
        grants: tuple[ResourceGrant, ...],
    ) -> bool:
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

    async def _query_family(
        self,
        execution: SearchExecution,
        family: SourceFamily,
        security_filter: str,
        *,
        deadline_at: float,
    ) -> tuple[SearchHit, ...]:
        plan = execution.plan
        limit = min(plan.candidate_limit, self._config.per_index_candidates)
        vector_queries: list[dict[str, object]] = []
        if execution.query_vector:
            vector_queries.append(
                {
                    "kind": "vector",
                    "vector": list(execution.query_vector),
                    "fields": "contentVector",
                    "k": limit,
                }
            )
        arguments = {
            "search_text": plan.sanitized_query,
            "filter": security_filter,
            "vector_filter_mode": "preFilter",
            "vector_queries": vector_queries,
            "select": SEARCH_SELECT_FIELDS,
            "top": limit,
        }
        if len(_encoded_search_payload(arguments)) > self._config.max_request_bytes:
            raise SearchBoundsExceeded("Azure Search request exceeds the byte bound")

        client = self._clients[family]
        client_request_id = str(uuid4())
        for attempt in range(self._config.max_retries + 1):
            try:
                async with self._connection_slots:
                    if asyncio.get_running_loop().time() >= deadline_at:
                        raise TimeoutError
                    result = await client.search(
                        **arguments,
                        client_request_id=client_request_id,
                    )
                normalized = await self._normalize_result(result)
                if normalized.partial:
                    raise SearchUnavailable("Azure Search returned a partial result page")
                if len(normalized.rows) > limit:
                    raise SearchUnavailable(
                        "Azure Search returned more rows than the bounded request top"
                    )
                target = self._config.indexes[family]
                hits = tuple(
                    self._map_hit(
                        row,
                        family,
                        target,
                        plan,
                        normalized.request_id,
                        local_rank,
                    )
                    for local_rank, row in enumerate(normalized.rows, start=1)
                )
                if not all(self._hit_matches_scope(hit, execution, family) for hit in hits):
                    raise SearchUnavailable("Azure Search returned a hit outside bound scope")
                return hits
            except (SearchBoundsExceeded, SearchUnavailable):
                raise
            except (_RetryableSearchError, httpx.TransportError, TimeoutError) as error:
                if attempt == self._config.max_retries:
                    raise SearchUnavailable("Azure Search retry budget exhausted") from error
            except _SearchTransportError as error:
                raise SearchUnavailable("Azure Search rejected the bounded query") from error
        raise SearchUnavailable("Azure Search retry budget exhausted")

    async def _normalize_result(self, result: Any) -> _SearchResult:
        if isinstance(result, _SearchResult):
            if len(result.rows) > self._config.max_rows:
                raise SearchUnavailable("Azure Search result row count exceeds the bound")
            return result
        rows: list[Mapping[str, Any]] = []
        async for row in result:
            if not isinstance(row, Mapping):
                raise _SearchTransportError("search result row is not a mapping")
            if len(rows) >= self._config.max_rows:
                raise SearchUnavailable("Azure Search result row count exceeds the bound")
            rows.append(cast(Mapping[str, Any], row))
        request_id_value = getattr(result, "request_id", None)
        request_id = (
            request_id_value
            if isinstance(request_id_value, str)
            and request_id_value
            and len(request_id_value) <= 256
            else None
        )
        partial_value = getattr(result, "partial", False)
        if type(partial_value) is not bool:
            raise SearchUnavailable("Azure Search partial-page marker is malformed")
        return _SearchResult(rows=tuple(rows), request_id=request_id, partial=partial_value)

    @staticmethod
    def _hit_matches_scope(
        hit: SearchHit,
        execution: SearchExecution,
        family: SourceFamily,
    ) -> bool:
        scope_resources = tuple(
            resource
            for resource in execution.plan.resources
            if resource.family is family and resource.mode is ResourceMode.SCOPE
        )
        if not scope_resources:
            return True
        for resource in scope_resources:
            if not (
                hit.source.source_id == resource.source_id
                and hit.source.revision_kind is resource.revision_kind
                and hit.source.revision == resource.revision
                and hit.source.source_content_hash == resource.source_content_hash
            ):
                continue
            if resource.subtree is None or _hit_in_subtree(hit, resource.subtree):
                return True
        return False

    @staticmethod
    def _security_filter(execution: SearchExecution, family: SourceFamily) -> str:
        policy = execution.policy
        plan = execution.plan
        classifications = tuple(
            item.value for item in CLASSIFICATION_ORDER if item in policy.allowed_classifications
        )
        environments: tuple[str, ...] = ("global",)
        if plan.effective_environment is not None:
            environments += (plan.effective_environment,)
        clauses = [
            f"tenantId eq {_odata_literal(policy.tenant_id)}",
            f"projectId eq {_odata_literal(policy.project_id)}",
            "allowedGroupIds/any(g: search.in(g, "
            f"{_search_in_literal(tuple(sorted(policy.actor.allowed_group_ids)))}, '|'))",
            f"search.in(classification, {_search_in_literal(classifications)}, '|')",
            f"search.in(environment, {_search_in_literal(environments)}, '|')",
            f"corpusVersion eq {_odata_literal(plan.corpus_version)}",
        ]
        scope_resources = tuple(
            resource
            for resource in plan.resources
            if resource.family is family and resource.mode is ResourceMode.SCOPE
        )
        if scope_resources:
            resource_clauses = []
            for resource in scope_resources:
                parts = [
                    f"sourceId eq {_odata_literal(resource.source_id)}",
                    f"sourceRevision eq {_odata_literal(resource.revision)}",
                    f"sourceContentHash eq {_odata_literal(resource.source_content_hash)}",
                ]
                if resource.subtree is not None:
                    locators = [
                        *(
                            f"rootId eq {_odata_literal(value)}"
                            for value in resource.subtree.root_ids
                        ),
                        *(
                            f"parentId eq {_odata_literal(value)}"
                            for value in resource.subtree.parent_ids
                        ),
                        *(
                            f"logicalChunkId eq {_odata_literal(value)}"
                            for value in resource.subtree.logical_chunk_ids
                        ),
                    ]
                    parts.append("(" + " or ".join(locators) + ")")
                resource_clauses.append("(" + " and ".join(parts) + ")")
            clauses.append("(" + " or ".join(resource_clauses) + ")")
        return " and ".join(clauses)

    def _map_hit(
        self,
        row: Mapping[str, Any],
        family: SourceFamily,
        target: AzureIndexTarget,
        plan: QueryPlan,
        provider_request_id: str | None,
        local_rank: int,
    ) -> SearchHit:
        try:
            index_family = _required_string(row, "indexFamily", maximum=16)
            if index_family != family.value:
                raise ValueError("row family does not match selected index")
            chunk_id = _chunk_id(row, "chunkId")
            logical_chunk_id = _chunk_id(row, "logicalChunkId")
            root_id = _optional_string(row, "rootId", maximum=self._config.max_identifier_chars)
            parent_id = _optional_string(
                row,
                "parentId",
                maximum=self._config.max_identifier_chars,
            )
            title = _optional_string(row, "title", maximum=1_024)
            content = _required_string(
                row,
                "content",
                maximum=self._config.max_content_chars,
            )
            source_id = _required_string(
                row,
                "sourceId",
                maximum=self._config.max_identifier_chars,
            )
            source_type = _required_string(row, "sourceType", maximum=128)
            source_revision = _required_string(
                row,
                "sourceRevision",
                maximum=self._config.max_identifier_chars,
            )
            source_hash = _required_string(
                row,
                "sourceContentHash",
                maximum=self._config.max_identifier_chars,
            )
            chunk_hash = _required_string(
                row,
                "chunkContentHash",
                maximum=self._config.max_identifier_chars,
            )
            corpus_version = _required_string(row, "corpusVersion", maximum=128)
            schema_version = _required_string(row, "schemaVersion", maximum=128)
            embedding_version = _required_string(
                row,
                "embeddingModelVersion",
                maximum=128,
            )
            if (
                corpus_version != plan.corpus_version
                or schema_version != target.schema_version
                or embedding_version != target.embedding_model_id
                or embedding_version != plan.embedding_model_id
            ):
                raise ValueError("row index/vector provenance does not match execution")
            anchor = _parse_anchor(row.get("anchorJson"), self._config.max_anchor_bytes)
            if not _anchor_matches_family(anchor, family):
                raise ValueError("row anchor is incompatible with selected family")
            derived = row.get("derivedFromChunkIds")
            if not isinstance(derived, list) or len(derived) > self._config.max_derived_ids:
                raise ValueError("derived chunk identifiers exceed the bound")
            derived_ids = tuple(_validated_chunk_id(value) for value in derived)
            score_value = row.get("@search.rerankerScore", row.get("@search.score"))
            if (
                isinstance(score_value, bool)
                or not isinstance(score_value, (int, float))
                or not math.isfinite(score_value)
            ):
                raise ValueError("Azure score must be finite")
            source = SourceRevisionRef(
                source_id=source_id,
                source_type=source_type,
                revision_kind=_revision_kind(family),
                revision=source_revision,
                source_content_hash=source_hash,
                anchor=anchor,
            )
            return SearchHit(
                family=family,
                chunk_id=chunk_id,
                logical_chunk_id=logical_chunk_id,
                root_id=root_id,
                parent_id=parent_id,
                title=title,
                content=content,
                source=source,
                chunk_content_hash=chunk_hash,
                content_role=ContentRole(_required_string(row, "contentRole", maximum=64)),
                derived_from_chunk_ids=derived_ids,
                index_revision=IndexRevision(
                    physical_index=target.physical_index,
                    schema_version=schema_version,
                    corpus_version=corpus_version,
                ),
                embedding_model_version=embedding_version,
                score=float(score_value),
                local_rank=local_rank,
                provider_request_id=provider_request_id,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise SearchUnavailable(
                "Azure Search result lacks strict immutable provenance"
            ) from error


def _encoded_search_payload(kwargs: Mapping[str, Any]) -> bytes:
    payload: dict[str, object] = {
        "search": kwargs["search_text"],
        "filter": kwargs["filter"],
        "vectorFilterMode": kwargs["vector_filter_mode"],
        "select": kwargs["select"],
        "top": kwargs["top"],
    }
    vector_queries = kwargs.get("vector_queries")
    if vector_queries:
        payload["vectorQueries"] = vector_queries
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise SearchBoundsExceeded("Azure Search request cannot be serialized safely") from error


async def _read_bounded(response: httpx.Response, maximum: int) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > maximum:
            raise _SearchTransportError("Azure Search response exceeds the byte bound")
        body.extend(chunk)
    return bytes(body)


def _first_header(response: httpx.Response, names: tuple[str, ...]) -> str | None:
    for name in names:
        value = response.headers.get(name)
        if isinstance(value, str) and value and len(value) <= 256:
            return value
    return None


def _required_runtime_string(value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise _SearchTransportError("Azure Search request identity is malformed")
    return value


def _required_string(
    row: Mapping[str, Any],
    name: str,
    *,
    maximum: int,
) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be a bounded string")
    return value


def _optional_string(row: Mapping[str, Any], name: str, *, maximum: int) -> str | None:
    value = row.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be null or a bounded non-empty string")
    return value


def _chunk_id(row: Mapping[str, Any], name: str) -> str:
    return _validated_chunk_id(row.get(name))


def _validated_chunk_id(value: object) -> str:
    if not isinstance(value, str) or not CHUNK_ID_PATTERN.fullmatch(value):
        raise ValueError("chunk identity is malformed")
    return value


def _strict_int(name: str, value: object, *, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")


def _finite_duration(name: str, value: object, *, maximum: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 < value <= maximum
    ):
        raise ValueError(f"{name} must be finite, positive, and at most {maximum} seconds")


def _odata_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _search_in_literal(values: tuple[str, ...]) -> str:
    if not values or any(not value or "|" in value for value in values):
        raise SearchUnavailable("policy contains an unsafe search.in value")
    return _odata_literal("|".join(values))


def _revision_kind(family: SourceFamily) -> RevisionKind:
    if family in {SourceFamily.CODE, SourceFamily.BDD}:
        return RevisionKind.GIT_COMMIT
    if family is SourceFamily.DOC:
        return RevisionKind.BLOB_VERSION
    return RevisionKind.MYSQL_VERSION


def _parse_anchor(raw: object, maximum_bytes: int) -> StructuralAnchor:
    if isinstance(raw, str):
        if len(raw.encode("utf-8")) > maximum_bytes:
            raise ValueError("anchor exceeds the byte bound")
        value = json.loads(raw, parse_constant=_reject_json_constant)
    else:
        try:
            encoded = json.dumps(raw, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValueError("anchor is not serializable") from error
        if len(encoded) > maximum_bytes:
            raise ValueError("anchor exceeds the byte bound")
        value = raw
    if not isinstance(value, dict):
        raise ValueError("anchor must be a JSON object")
    anchor_type = value.get("type")
    allowed_fields = {
        "document": {
            "type",
            "headingPath",
            "page",
            "bbox",
            "startOffset",
            "endOffset",
        },
        "code": {"type", "repo", "path", "symbol", "lineStart", "lineEnd"},
        "bdd": {"type", "featureId", "scenarioId", "stepId"},
        "openapi": {"type", "method", "path", "jsonPointer"},
        "failure": {"type", "incidentId", "runId", "timeStart", "timeEnd"},
    }
    if (
        not isinstance(anchor_type, str)
        or anchor_type not in allowed_fields
        or not set(value) <= allowed_fields[anchor_type]
    ):
        raise ValueError("anchor uses an unsupported or widened schema")
    if anchor_type == "document":
        headings_value = value.get("headingPath", [])
        bbox_value = value.get("bbox", [])
        if (
            not isinstance(headings_value, list)
            or len(headings_value) > 32
            or any(
                not isinstance(item, str) or not item or len(item) > 512 for item in headings_value
            )
            or not isinstance(bbox_value, list)
            or len(bbox_value) > 8
            or any(
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(item)
                for item in bbox_value
            )
        ):
            raise ValueError("document anchor is malformed")
        return DocumentAnchor(
            heading_path=tuple(headings_value),
            page=_optional_int(value, "page", minimum=1),
            bbox=tuple(float(item) for item in bbox_value),
            start_offset=_optional_int(value, "startOffset", minimum=0),
            end_offset=_optional_int(value, "endOffset", minimum=0),
        )
    if anchor_type == "code":
        return CodeAnchor(
            repo=_anchor_string(value, "repo", maximum=512),
            path=_anchor_string(value, "path", maximum=2_048),
            symbol=_anchor_optional_string(value, "symbol", maximum=512),
            line_start=_anchor_int(value, "lineStart", minimum=1),
            line_end=_anchor_int(value, "lineEnd", minimum=1),
        )
    if anchor_type == "bdd":
        return BddAnchor(
            feature_id=_anchor_string(value, "featureId", maximum=512),
            scenario_id=_anchor_optional_string(value, "scenarioId", maximum=512),
            step_id=_anchor_optional_string(value, "stepId", maximum=512),
        )
    if anchor_type == "openapi":
        return OpenApiAnchor(
            method=_anchor_string(value, "method", maximum=16),
            path=_anchor_string(value, "path", maximum=2_048),
            json_pointer=_anchor_string(value, "jsonPointer", maximum=2_048),
        )
    if anchor_type == "failure":
        return FailureAnchor(
            incident_id=_anchor_string(value, "incidentId", maximum=512),
            run_id=_anchor_optional_string(value, "runId", maximum=512),
            time_start=_anchor_optional_string(value, "timeStart", maximum=128),
            time_end=_anchor_optional_string(value, "timeEnd", maximum=128),
        )
    raise ValueError("anchor has an unsupported discriminator")


def _anchor_string(value: Mapping[str, Any], name: str, *, maximum: int) -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item or len(item) > maximum:
        raise ValueError(f"anchor {name} must be a bounded string")
    return item


def _anchor_optional_string(
    value: Mapping[str, Any],
    name: str,
    *,
    maximum: int,
) -> str | None:
    item = value.get(name)
    if item is None:
        return None
    return _anchor_string(value, name, maximum=maximum)


def _anchor_int(value: Mapping[str, Any], name: str, *, minimum: int) -> int:
    item = value.get(name)
    if type(item) is not int or item < minimum or item > 10_000_000:
        raise ValueError(f"anchor {name} must be a strict bounded integer")
    return item


def _optional_int(value: Mapping[str, Any], name: str, *, minimum: int) -> int | None:
    if value.get(name) is None:
        return None
    return _anchor_int(value, name, minimum=minimum)


def _anchor_matches_family(anchor: StructuralAnchor, family: SourceFamily) -> bool:
    return (
        (family is SourceFamily.CODE and isinstance(anchor, CodeAnchor))
        or (family is SourceFamily.BDD and isinstance(anchor, BddAnchor))
        or (family is SourceFamily.FAILURE and isinstance(anchor, FailureAnchor))
        or (family is SourceFamily.DOC and isinstance(anchor, (DocumentAnchor, OpenApiAnchor)))
    )


def _hit_in_subtree(hit: SearchHit, subtree: FilterableSubtree) -> bool:
    return (
        (hit.root_id is not None and hit.root_id in subtree.root_ids)
        or (hit.parent_id is not None and hit.parent_id in subtree.parent_ids)
        or hit.logical_chunk_id in subtree.logical_chunk_ids
    )


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value} is forbidden")
