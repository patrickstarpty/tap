"""HTTP-only adapter from TAP AI to the generated TAP Insights contract."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from tap.modules.ai.ports.insights import (
    AuthorizedInsightsScope,
    FactWatermark,
    InsightsAuthorizationChanged,
    InsightsQueryUnavailable,
    MetricFact,
    MetricQuery,
    MetricResult,
)

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class ServiceIdentity:
    authorization: str = field(repr=False)
    audience: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.authorization, str) or not 16 <= len(self.authorization) <= 4096:
            raise ValueError("service identity requires a bounded authorization")
        if self.audience != "tap-insights":
            raise ValueError("service identity audience must be tap-insights")
        if not isinstance(self.expires_at, datetime) or self.expires_at.utcoffset() is None:
            raise ValueError("service identity expiry must be timezone-aware")


@dataclass(frozen=True, slots=True)
class TapInsightsConfig:
    base_url: str
    timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
            or (
                parsed.scheme != "https"
                and not (
                    parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                )
            )
        ):
            raise ValueError("TAP Insights requires HTTPS or loopback HTTP")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 30
        ):
            raise ValueError("TAP Insights timeout must be bounded")


class TapInsightsAdapter:
    """Consume TAP's HTTP artifact without importing the TAP backend package."""

    def __init__(
        self,
        config: TapInsightsConfig,
        *,
        service_identity: Callable[[], ServiceIdentity],
        clock: Callable[[], datetime],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._service_identity = service_identity
        self._clock = clock
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def query_insights(
        self, scope: AuthorizedInsightsScope, query: MetricQuery
    ) -> MetricResult:
        raw = await self._request(
            scope,
            "POST",
            f"/api/v1/projects/{quote(scope.context.project_id, safe='')}/insights/queries",
            json=query.to_contract(),
        )
        result = _parse_result(raw)
        if result.query != query:
            raise InsightsQueryUnavailable("TAP Insights contract changed query scope")
        return result

    async def get_insights(self, scope: AuthorizedInsightsScope, query_id: str) -> MetricResult:
        if _IDENTIFIER.fullmatch(query_id) is None:
            raise InsightsQueryUnavailable("TAP Insights contract has an invalid query ID")
        raw = await self._request(
            scope,
            "GET",
            "/api/v1/projects/"
            f"{quote(scope.context.project_id, safe='')}/insights/queries/"
            f"{quote(query_id, safe='')}",
        )
        result = _parse_result(raw)
        if result.query_id != query_id:
            raise InsightsQueryUnavailable("TAP Insights contract changed query ID")
        return result

    async def _request(
        self,
        scope: AuthorizedInsightsScope,
        method: str,
        path: str,
        *,
        json: dict[str, object] | None = None,
    ) -> Mapping[str, object]:
        identity = self._service_identity()
        if identity.expires_at <= self._clock():
            raise InsightsAuthorizationChanged("TAP Insights service identity expired")
        try:
            response = await self._client.request(
                method,
                self._config.base_url.rstrip("/") + path,
                json=json,
                headers={
                    "Authorization": f"Bearer {scope.user_authorization}",
                    "X-TAP-Service-Authorization": f"Bearer {identity.authorization}",
                    "X-TAP-Authorization-Version": scope.authorization_version,
                },
                timeout=self._config.timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise InsightsQueryUnavailable("TAP Insights API unavailable") from exc
        if response.status_code in {401, 403}:
            raise InsightsAuthorizationChanged("TAP Insights authorization changed")
        if response.status_code not in {200, 201}:
            raise InsightsQueryUnavailable("TAP Insights query unavailable")
        try:
            value = response.json()
        except ValueError as exc:
            raise InsightsQueryUnavailable("TAP Insights contract response is invalid") from exc
        if not isinstance(value, Mapping):
            raise InsightsQueryUnavailable("TAP Insights contract response is invalid")
        return value


def _closed(value: object, fields: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise InsightsQueryUnavailable(f"TAP Insights contract {name} is invalid")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise InsightsQueryUnavailable(f"TAP Insights contract {name} is invalid")
    return value


def _string_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise InsightsQueryUnavailable(f"TAP Insights contract {name} is invalid")
    return tuple(value)


def _parse_datetime(value: object, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_text(value, name).replace("Z", "+00:00"))
    except ValueError as exc:
        raise InsightsQueryUnavailable(f"TAP Insights contract {name} is invalid") from exc
    if parsed.utcoffset() is None:
        raise InsightsQueryUnavailable(f"TAP Insights contract {name} is invalid")
    return parsed


def _parse_result(raw: Mapping[str, object]) -> MetricResult:
    try:
        body = _closed(
            raw,
            {
                "queryId",
                "metricVersion",
                "filters",
                "from",
                "to",
                "timezone",
                "asOf",
                "createdAt",
                "factWatermark",
                "metrics",
                "trends",
            },
            "response",
        )
        filters = _closed(
            body["filters"],
            {
                "sourceIds",
                "runIds",
                "buildIds",
                "branches",
                "environments",
                "configurations",
            },
            "filters",
        )
        watermark = _closed(
            body["factWatermark"],
            {"projectionVersion", "visibleDataVersion"},
            "watermark",
        )
        metric_rows = body["metrics"]
        if not isinstance(metric_rows, list):
            raise InsightsQueryUnavailable("TAP Insights contract metrics are invalid")
        metrics: list[MetricFact] = []
        for row in metric_rows:
            item = _closed(
                row,
                {
                    "metricId",
                    "numerator",
                    "denominator",
                    "value",
                    "completeness",
                    "missingReasons",
                    "evidenceRefs",
                },
                "metric",
            )
            metrics.append(
                MetricFact(
                    metric_id=_text(item["metricId"], "metric ID"),  # type: ignore[arg-type]
                    numerator=item["numerator"],  # type: ignore[arg-type]
                    denominator=item["denominator"],  # type: ignore[arg-type]
                    value=item["value"],  # type: ignore[arg-type]
                    completeness=_text(item["completeness"], "completeness"),  # type: ignore[arg-type]
                    missing_reasons=_string_tuple(item["missingReasons"], "missing reasons"),
                    evidence_refs=_string_tuple(item["evidenceRefs"], "evidence refs"),
                )
            )
        query = MetricQuery(
            metric_ids=tuple(item.metric_id for item in metrics),
            source_ids=_string_tuple(filters["sourceIds"], "source IDs"),
            run_ids=_string_tuple(filters["runIds"], "run IDs"),
            build_ids=_string_tuple(filters["buildIds"], "build IDs"),
            branches=_string_tuple(filters["branches"], "branches"),
            environments=_string_tuple(filters["environments"], "environments"),
            configurations=_string_tuple(filters["configurations"], "configurations"),
            from_date=_text(body["from"], "from"),
            to_date=_text(body["to"], "to"),
            timezone=_text(body["timezone"], "timezone"),
            as_of=_parse_datetime(body["asOf"], "asOf"),
        )
        _parse_datetime(body["createdAt"], "createdAt")
        if not isinstance(body["trends"], list):
            raise InsightsQueryUnavailable("TAP Insights contract trends are invalid")
        return MetricResult(
            query_id=_text(body["queryId"], "query ID"),
            metric_version=_text(body["metricVersion"], "metric version"),
            query=query,
            fact_watermark=FactWatermark(
                _text(watermark["projectionVersion"], "projection version"),
                watermark["visibleDataVersion"],  # type: ignore[arg-type]
            ),
            metrics=tuple(metrics),
        )
    except InsightsQueryUnavailable:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise InsightsQueryUnavailable("TAP Insights contract response is invalid") from exc
