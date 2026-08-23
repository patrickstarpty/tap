"""Bounded Azure AI Search REST adapter with mandatory server-side ACL filters."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, cast
from urllib.parse import quote
from uuid import uuid4

import httpx

from tap.modules.access.domain.policy import Classification
from tap.modules.knowledge.domain.models import (
    BddAnchor,
    CodeAnchor,
    ContentRole,
    DocumentAnchor,
    FailureAnchor,
    IndexRevision,
    OpenApiAnchor,
    ResourceMode,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
    StructuralAnchor,
    anchor_authorization_key,
)
from tap.modules.knowledge.ports.models import SearchExecution, SearchHit

AZURE_SEARCH_API_VERSION = "2026-04-01"
SEARCH_SELECT_FIELDS = (
    "chunkId,logicalChunkId,title,content,sourceId,sourceType,sourceRevision,"
    "anchorJson,sourceContentHash,chunkContentHash,contentRole,derivedFromChunkIds,"
    "corpusVersion,schemaVersion,embeddingModelVersion"
)
CLASSIFICATION_ORDER = (
    Classification.PUBLIC,
    Classification.INTERNAL,
    Classification.CONFIDENTIAL,
    Classification.RESTRICTED,
)


class SearchBoundsExceeded(Exception):
    """The selected execution would exceed a configured search bound."""


class SearchUnavailable(Exception):
    """A selected index could not return one complete, authorized result page."""


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
class AzureSearchConfig:
    endpoint: str
    api_key: str = field(repr=False)
    index_aliases: Mapping[SourceFamily, str]
    physical_indexes: Mapping[SourceFamily, str] | None = None
    max_fan_out: int = 4
    per_index_candidates: int = 50
    max_connections: int = 4
    deadline_seconds: float = 8
    max_retries: int = 1
    connect_timeout_seconds: float = 2
    read_timeout_seconds: float = 5

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, str) or not self.endpoint.startswith("https://"):
            raise ValueError("Azure Search endpoint must use HTTPS")
        if not isinstance(self.api_key, str) or not self.api_key:
            raise ValueError("Azure Search credential must not be empty")
        _strict_int("max_fan_out", self.max_fan_out, minimum=1, maximum=len(SourceFamily))
        _strict_int(
            "per_index_candidates",
            self.per_index_candidates,
            minimum=1,
            maximum=100,
        )
        _strict_int("max_connections", self.max_connections, minimum=1, maximum=16)
        _strict_int("max_retries", self.max_retries, minimum=0, maximum=2)
        _finite_duration("deadline_seconds", self.deadline_seconds, maximum=30)
        _finite_duration("connect_timeout_seconds", self.connect_timeout_seconds, maximum=10)
        _finite_duration("read_timeout_seconds", self.read_timeout_seconds, maximum=30)
        if not self.index_aliases or len(self.index_aliases) > len(SourceFamily):
            raise ValueError("Azure Search must configure one to four index targets")
        for family, index in self.index_aliases.items():
            if (
                not isinstance(family, SourceFamily)
                or not isinstance(index, str)
                or not index
                or "/" in index
            ):
                raise ValueError("Azure Search index targets must be closed, safe names")
        if self.physical_indexes is not None:
            if set(self.physical_indexes) != set(self.index_aliases):
                raise ValueError("physical index identities must match configured query targets")
            for family, index in self.physical_indexes.items():
                if (
                    not isinstance(family, SourceFamily)
                    or not isinstance(index, str)
                    or not index
                    or "/" in index
                ):
                    raise ValueError("physical index identities must be closed, safe names")


class _AzureRestClient:
    """httpx transport details contained entirely inside the Azure adapter."""

    def __init__(self, config: AzureSearchConfig, index: str) -> None:
        self._index = index
        self._client = httpx.AsyncClient(
            base_url=config.endpoint.rstrip("/") + "/",
            headers={"api-key": config.api_key, "content-type": "application/json"},
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
            transport=httpx.AsyncHTTPTransport(retries=0),
        )

    async def search(self, **kwargs: Any) -> _SearchResult:
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
        response = await self._client.post(
            f"indexes/{quote(self._index, safe='')}/docs/search",
            params={"api-version": AZURE_SEARCH_API_VERSION},
            headers={
                "x-ms-client-request-id": str(kwargs["client_request_id"]),
                "return-client-request-id": "true",
            },
            json=payload,
        )
        if response.status_code in {408, 429} or response.status_code >= 500:
            raise _RetryableSearchError(f"Azure Search returned HTTP {response.status_code}")
        if response.is_error:
            raise _SearchTransportError(f"Azure Search returned HTTP {response.status_code}")
        body = response.json()
        rows = body.get("value")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise _SearchTransportError("Azure Search returned a malformed result page")
        request_id = (
            response.headers.get("request-id")
            or response.headers.get("x-ms-request-id")
            or response.headers.get("x-ms-client-request-id")
        )
        return _SearchResult(
            rows=tuple(cast(Mapping[str, Any], row) for row in rows),
            request_id=request_id,
            partial=bool(body.get("@odata.nextLink") or body.get("@odata.nextLink@odata.count")),
        )

    async def close(self) -> None:
        await self._client.aclose()


class AzureAISearchAdapter:
    """Execute strict, bounded fan-out without accepting a caller-authored filter."""

    def __init__(
        self,
        config: AzureSearchConfig,
        *,
        client_factory: Callable[[str], _SearchClient] | None = None,
    ) -> None:
        self._config = config
        factory = client_factory or (lambda index: _AzureRestClient(config, index))
        self._clients = {family: factory(index) for family, index in config.index_aliases.items()}
        self._physical_indexes = dict(config.physical_indexes or config.index_aliases)
        self._connection_slots = asyncio.Semaphore(config.max_connections)

    async def search(self, execution: SearchExecution) -> tuple[SearchHit, ...]:
        self._validate_execution(execution)
        if len(execution.source_families) > self._config.max_fan_out:
            raise SearchBoundsExceeded("selected source fan-out exceeds the configured bound")
        if any(family not in self._clients for family in execution.source_families):
            raise SearchUnavailable("a selected source family has no configured index")

        filters = {
            family: self._security_filter(execution, family) for family in execution.source_families
        }
        try:
            async with asyncio.timeout(self._config.deadline_seconds):
                family_hits = await asyncio.gather(
                    *(
                        self._query_family(execution, family, filters[family])
                        for family in execution.source_families
                    )
                )
        except TimeoutError as error:
            raise SearchUnavailable("Azure Search exceeded the outer deadline") from error
        return tuple(hit for hits in family_hits for hit in hits)

    async def close(self) -> None:
        await asyncio.gather(*(client.close() for client in self._clients.values()))

    def _validate_execution(self, execution: SearchExecution) -> None:
        policy = execution.policy
        if not policy.actor.allowed_group_ids or not policy.allowed_classifications:
            raise SearchUnavailable("policy has no authorized ACL values")
        if execution.corpus_version != policy.active_corpus_version:
            raise SearchUnavailable("execution corpus no longer matches current policy")
        if (
            execution.effective_environment is not None
            and execution.effective_environment not in policy.allowed_environments
        ):
            raise SearchUnavailable("execution environment no longer matches current policy")
        if not execution.source_families:
            raise SearchBoundsExceeded("at least one source family is required")
        if len(set(execution.source_families)) != len(execution.source_families):
            raise SearchBoundsExceeded("source family fan-out must not contain duplicates")
        if (
            not {family.value for family in execution.source_families}
            <= policy.allowed_source_families
        ):
            raise SearchUnavailable("execution source family no longer matches current policy")
        try:
            _strict_int("candidate_limit", execution.candidate_limit, minimum=1, maximum=100)
        except ValueError as error:
            raise SearchBoundsExceeded("candidate limit is outside the execution bound") from error
        if (
            not isinstance(execution.query, str)
            or not execution.query.strip()
            or len(execution.query) > 8_000
        ):
            raise SearchBoundsExceeded("query is outside the execution text bound")
        if len(execution.resources) > 20:
            raise SearchBoundsExceeded("resource scope exceeds the execution bound")
        if (
            not isinstance(execution.query_vector, tuple)
            or len(execution.query_vector) > 4_096
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in execution.query_vector
            )
        ):
            raise SearchBoundsExceeded("query vector is outside the execution bound")

    async def _query_family(
        self,
        execution: SearchExecution,
        family: SourceFamily,
        security_filter: str,
    ) -> tuple[SearchHit, ...]:
        limit = min(execution.candidate_limit, self._config.per_index_candidates)
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
        client = self._clients[family]
        client_request_id = str(uuid4())
        for attempt in range(self._config.max_retries + 1):
            try:
                async with self._connection_slots:
                    result = await client.search(
                        search_text=execution.query,
                        filter=security_filter,
                        vector_filter_mode="preFilter",
                        vector_queries=vector_queries,
                        select=SEARCH_SELECT_FIELDS,
                        top=limit,
                        client_request_id=client_request_id,
                    )
                normalized = await self._normalize_result(result)
                if normalized.partial:
                    raise SearchUnavailable("Azure Search returned a partial result page")
                index = self._physical_indexes[family]
                hits = tuple(
                    self._map_hit(
                        row,
                        family,
                        index,
                        normalized.request_id,
                        local_rank,
                    )
                    for local_rank, row in enumerate(normalized.rows, start=1)
                )
                return tuple(hit for hit in hits if self._hit_matches_scope(hit, execution, family))
            except SearchUnavailable:
                raise
            except (_RetryableSearchError, httpx.TransportError, TimeoutError) as error:
                if attempt == self._config.max_retries:
                    raise SearchUnavailable("Azure Search retry budget exhausted") from error
            except _SearchTransportError as error:
                raise SearchUnavailable("Azure Search rejected the bounded query") from error
        raise SearchUnavailable("Azure Search retry budget exhausted")

    @staticmethod
    async def _normalize_result(result: Any) -> _SearchResult:
        if isinstance(result, _SearchResult):
            return result
        rows: list[Mapping[str, Any]] = []
        async for row in result:
            if not isinstance(row, Mapping):
                raise _SearchTransportError("search result row is not a mapping")
            rows.append(cast(Mapping[str, Any], row))
        request_id_value = getattr(result, "request_id", None)
        request_id = (
            request_id_value if isinstance(request_id_value, str) and request_id_value else None
        )
        return _SearchResult(rows=tuple(rows), request_id=request_id, partial=False)

    @staticmethod
    def _hit_matches_scope(
        hit: SearchHit,
        execution: SearchExecution,
        family: SourceFamily,
    ) -> bool:
        scope_resources = tuple(
            resource
            for resource in execution.resources
            if resource.family is family and resource.mode is ResourceMode.SCOPE
        )
        if not scope_resources:
            return True
        for resource in scope_resources:
            if not (
                hit.source.source_id == resource.source_id
                and hit.source.revision == resource.revision
                and hit.source.source_content_hash == resource.source_content_hash
            ):
                continue
            if resource.anchor is None or (
                anchor_authorization_key(hit.source.anchor)
                == anchor_authorization_key(resource.anchor)
            ):
                return True
        return False

    @staticmethod
    def _security_filter(execution: SearchExecution, family: SourceFamily) -> str:
        policy = execution.policy
        classifications = tuple(
            item.value for item in CLASSIFICATION_ORDER if item in policy.allowed_classifications
        )
        environments: tuple[str, ...] = ("global",)
        if execution.effective_environment is not None:
            environments += (execution.effective_environment,)
        clauses = [
            f"tenantId eq {_odata_literal(policy.tenant_id)}",
            f"projectId eq {_odata_literal(policy.project_id)}",
            "allowedGroupIds/any(g: search.in(g, "
            f"{_search_in_literal(tuple(sorted(policy.actor.allowed_group_ids)))}, '|'))",
            f"search.in(classification, {_search_in_literal(classifications)}, '|')",
            f"search.in(environment, {_search_in_literal(environments)}, '|')",
            f"corpusVersion eq {_odata_literal(execution.corpus_version)}",
        ]
        scope_resources = tuple(
            resource
            for resource in execution.resources
            if resource.family is family and resource.mode is ResourceMode.SCOPE
        )
        if scope_resources:
            resource_clauses = [
                "(sourceId eq "
                f"{_odata_literal(resource.source_id)} and sourceRevision eq "
                f"{_odata_literal(resource.revision)})"
                for resource in scope_resources
            ]
            clauses.append("(" + " or ".join(resource_clauses) + ")")
        return " and ".join(clauses)

    @staticmethod
    def _map_hit(
        row: Mapping[str, Any],
        family: SourceFamily,
        physical_index: str,
        provider_request_id: str | None,
        local_rank: int,
    ) -> SearchHit:
        try:
            anchor = _parse_anchor(row["anchorJson"])
            source = SourceRevisionRef(
                source_id=str(row["sourceId"]),
                source_type=str(row["sourceType"]),
                revision_kind=_revision_kind(family),
                revision=str(row["sourceRevision"]),
                source_content_hash=str(row["sourceContentHash"]),
                anchor=anchor,
            )
            return SearchHit(
                family=family,
                chunk_id=str(row["chunkId"]),
                logical_chunk_id=str(row["logicalChunkId"]),
                title=str(row["title"]) if row.get("title") is not None else None,
                content=str(row["content"]),
                source=source,
                chunk_content_hash=str(row["chunkContentHash"]),
                content_role=ContentRole(str(row["contentRole"])),
                derived_from_chunk_ids=tuple(
                    str(item) for item in row.get("derivedFromChunkIds") or ()
                ),
                index_revision=IndexRevision(
                    physical_index=physical_index,
                    schema_version=str(row["schemaVersion"]),
                    corpus_version=str(row["corpusVersion"]),
                ),
                embedding_model_version=str(row["embeddingModelVersion"]),
                score=float(row.get("@search.rerankerScore", row.get("@search.score", 0))),
                local_rank=local_rank,
                provider_request_id=provider_request_id,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise SearchUnavailable("Azure Search result lacks immutable provenance") from error


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


def _parse_anchor(raw: object) -> StructuralAnchor:
    value = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(value, dict):
        raise ValueError("anchor must be a JSON object")
    anchor_type = value.get("type")
    if anchor_type == "document":
        return DocumentAnchor(
            heading_path=tuple(str(item) for item in value.get("headingPath") or ()),
            page=int(value["page"]) if value.get("page") is not None else None,
            bbox=tuple(float(item) for item in value.get("bbox") or ()),
            start_offset=(
                int(value["startOffset"]) if value.get("startOffset") is not None else None
            ),
            end_offset=(int(value["endOffset"]) if value.get("endOffset") is not None else None),
        )
    if anchor_type == "code":
        return CodeAnchor(
            repo=str(value["repo"]),
            path=str(value["path"]),
            symbol=str(value["symbol"]) if value.get("symbol") is not None else None,
            line_start=int(value["lineStart"]),
            line_end=int(value["lineEnd"]),
        )
    if anchor_type == "bdd":
        return BddAnchor(
            feature_id=str(value["featureId"]),
            scenario_id=(str(value["scenarioId"]) if value.get("scenarioId") is not None else None),
            step_id=str(value["stepId"]) if value.get("stepId") is not None else None,
        )
    if anchor_type == "openapi":
        return OpenApiAnchor(
            method=str(value["method"]),
            path=str(value["path"]),
            json_pointer=str(value["jsonPointer"]),
        )
    if anchor_type == "failure":
        return FailureAnchor(
            incident_id=str(value["incidentId"]),
            run_id=str(value["runId"]) if value.get("runId") is not None else None,
            time_start=(str(value["timeStart"]) if value.get("timeStart") is not None else None),
            time_end=str(value["timeEnd"]) if value.get("timeEnd") is not None else None,
        )
    raise ValueError("anchor has an unsupported discriminator")
