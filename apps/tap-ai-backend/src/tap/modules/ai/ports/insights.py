"""Closed read-only TAP Insights boundary for authorized AI workflows."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, Mapping, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tap.modules.access.domain.context import ProjectScopeContext

MetricId = Literal[
    "first_pass_rate",
    "final_pass_rate",
    "retry_recovery_rate",
    "recovery_contribution_rate",
    "skipped_count",
    "p95_duration_seconds",
]
MetricCompleteness = Literal["complete", "empty", "unavailable"]

_METRIC_IDS = frozenset(
    {
        "first_pass_rate",
        "final_pass_rate",
        "retry_recovery_rate",
        "recovery_contribution_rate",
        "skipped_count",
        "p95_duration_seconds",
    }
)
_RATIO_METRIC_IDS = frozenset(
    {
        "first_pass_rate",
        "final_pass_rate",
        "retry_recovery_rate",
        "recovery_contribution_rate",
    }
)
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


def _identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} must be a bounded identifier")


def _identifiers(name: str, values: tuple[str, ...], maximum: int) -> None:
    if not isinstance(values, tuple) or len(values) > maximum or len(set(values)) != len(values):
        raise ValueError(f"{name} must be a bounded unique tuple")
    for value in values:
        _identifier(name, value)


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthorizedInsightsScope:
    """Server-produced user/project scope; credentials never enter model-visible input."""

    context: ProjectScopeContext
    user_authorization: str = field(repr=False)
    authorization_version: str
    authorized_resource_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.context) is not ProjectScopeContext:
            raise TypeError("insights scope requires a server-authorized project context")
        if (
            not isinstance(self.user_authorization, str)
            or not 16 <= len(self.user_authorization) <= 4096
        ):
            raise ValueError("insights scope requires a bounded delegated authorization")
        _identifier("authorization_version", self.authorization_version)
        _identifiers("authorized_resource_refs", self.authorized_resource_refs, 100)


@dataclass(frozen=True, slots=True, kw_only=True)
class MetricQuery:
    metric_ids: tuple[MetricId, ...]
    source_ids: tuple[str, ...]
    run_ids: tuple[str, ...]
    build_ids: tuple[str, ...]
    branches: tuple[str, ...]
    environments: tuple[str, ...]
    configurations: tuple[str, ...]
    from_date: str
    to_date: str
    timezone: str
    as_of: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.metric_ids, tuple)
            or not 1 <= len(self.metric_ids) <= len(_METRIC_IDS)
            or len(set(self.metric_ids)) != len(self.metric_ids)
            or not set(self.metric_ids) <= _METRIC_IDS
        ):
            raise ValueError("metric query requires allowlisted unique metrics")
        for name, values, maximum in (
            ("source_ids", self.source_ids, 50),
            ("run_ids", self.run_ids, 100),
            ("build_ids", self.build_ids, 100),
            ("branches", self.branches, 50),
            ("environments", self.environments, 20),
            ("configurations", self.configurations, 20),
        ):
            _identifiers(name, values, maximum)
        try:
            start = date.fromisoformat(self.from_date)
            end = date.fromisoformat(self.to_date)
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("metric query requires ISO dates and an IANA timezone") from exc
        if start >= end:
            raise ValueError("metric query start must precede end")
        if not isinstance(self.as_of, datetime) or self.as_of.utcoffset() is None:
            raise ValueError("metric query as_of must be timezone-aware")

    def to_contract(self) -> dict[str, object]:
        return {
            "metricIds": list(self.metric_ids),
            "filters": {
                "sourceIds": list(self.source_ids),
                "runIds": list(self.run_ids),
                "buildIds": list(self.build_ids),
                "branches": list(self.branches),
                "environments": list(self.environments),
                "configurations": list(self.configurations),
            },
            "from": self.from_date,
            "to": self.to_date,
            "timezone": self.timezone,
            "asOf": self.as_of.isoformat().replace("+00:00", "Z"),
        }


@dataclass(frozen=True, slots=True)
class FactWatermark:
    projection_version: str
    visible_data_version: int

    def __post_init__(self) -> None:
        _identifier("projection_version", self.projection_version)
        if type(self.visible_data_version) is not int or self.visible_data_version < 0:
            raise ValueError("visible data version must be nonnegative")


@dataclass(frozen=True, slots=True)
class MetricFact:
    metric_id: MetricId
    numerator: int | None
    denominator: int | None
    value: float | None
    completeness: MetricCompleteness
    missing_reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.metric_id not in _METRIC_IDS:
            raise ValueError("metric fact is not allowlisted")
        if self.completeness not in {"complete", "empty", "unavailable"}:
            raise ValueError("metric completeness is invalid")
        for count in (self.numerator, self.denominator):
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError("metric counts must be nonnegative integers")
        if self.value is not None and (
            isinstance(self.value, bool)
            or not isinstance(self.value, (int, float))
            or not math.isfinite(self.value)
        ):
            raise ValueError("metric value must be finite or unavailable")
        if not isinstance(self.missing_reasons, tuple) or len(self.missing_reasons) > 50:
            raise ValueError("metric missing reasons must be bounded")
        if any(not isinstance(item, str) or not item.strip() for item in self.missing_reasons):
            raise ValueError("metric missing reasons must be nonblank")
        _identifiers("evidence_refs", self.evidence_refs, 1000)
        if self.completeness != "complete" and self.value is not None:
            raise ValueError("incomplete metrics cannot carry a numeric value")
        if self.completeness == "complete" and self.missing_reasons:
            raise ValueError("complete metrics cannot carry missing reasons")
        if self.metric_id in _RATIO_METRIC_IDS and self.completeness == "complete":
            if (
                self.numerator is None
                or self.denominator is None
                or self.denominator <= 0
                or self.numerator > self.denominator
                or self.value is None
                or not math.isclose(
                    self.value,
                    self.numerator / self.denominator,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
            ):
                raise ValueError("rate value must match its numerator and denominator")
        if self.metric_id == "skipped_count" and self.completeness == "complete":
            if (
                self.numerator is None
                or self.denominator is not None
                or self.value != float(self.numerator)
            ):
                raise ValueError("count value must match its numerator")
        if self.metric_id == "p95_duration_seconds" and self.completeness == "complete":
            if (
                self.numerator is not None
                or self.denominator is None
                or self.denominator <= 0
                or self.value is None
                or self.value < 0
            ):
                raise ValueError("duration value requires a positive denominator")


@dataclass(frozen=True, slots=True)
class MetricResult:
    query_id: str
    metric_version: str
    query: MetricQuery
    fact_watermark: FactWatermark
    metrics: tuple[MetricFact, ...]

    def __post_init__(self) -> None:
        _identifier("query_id", self.query_id)
        _identifier("metric_version", self.metric_version)
        if type(self.query) is not MetricQuery or type(self.fact_watermark) is not FactWatermark:
            raise TypeError("metric result requires an immutable query and watermark")
        if (
            not isinstance(self.metrics, tuple)
            or len(self.metrics) != len(self.query.metric_ids)
            or tuple(item.metric_id for item in self.metrics) != self.query.metric_ids
        ):
            raise ValueError("metric result must match the requested metric order")


@dataclass(frozen=True, slots=True)
class AuthorizedEvidence:
    citation_id: str
    resource_ref: str
    excerpt: str

    def __post_init__(self) -> None:
        _identifier("citation_id", self.citation_id)
        _identifier("resource_ref", self.resource_ref)
        if (
            not isinstance(self.excerpt, str)
            or not self.excerpt.strip()
            or len(self.excerpt) > 20_000
        ):
            raise ValueError("evidence excerpt must be bounded and nonblank")


class InsightsAuthorizationChanged(PermissionError):
    pass


class InsightsQueryUnavailable(RuntimeError):
    pass


class InsightsBudgetExceeded(RuntimeError):
    pass


class InsightsPort(Protocol):
    async def query_insights(
        self, scope: AuthorizedInsightsScope, query: MetricQuery
    ) -> MetricResult: ...

    async def get_insights(self, scope: AuthorizedInsightsScope, query_id: str) -> MetricResult: ...


class StructuredInsightsTool:
    """Model-facing closed input surface with no project or mutation fields."""

    name = "insights.query"
    read_only = True

    def __init__(self, port: InsightsPort) -> None:
        self._port = port

    async def invoke(
        self, scope: AuthorizedInsightsScope, arguments: Mapping[str, object]
    ) -> MetricResult:
        expected = {"metricIds", "filters", "from", "to", "timezone", "asOf"}
        if set(arguments) != expected:
            raise ValueError("insights tool input must use the closed generated contract")
        filters = arguments["filters"]
        if not isinstance(filters, Mapping):
            raise ValueError("insights tool filters must be an object")
        filter_fields = {
            "sourceIds",
            "runIds",
            "buildIds",
            "branches",
            "environments",
            "configurations",
        }
        if set(filters) - filter_fields:
            raise ValueError("insights tool filters must use the closed generated contract")
        try:
            parsed = MetricQuery(
                metric_ids=tuple(arguments["metricIds"]),  # type: ignore[arg-type]
                source_ids=tuple(filters.get("sourceIds", ())),
                run_ids=tuple(filters.get("runIds", ())),
                build_ids=tuple(filters.get("buildIds", ())),
                branches=tuple(filters.get("branches", ())),
                environments=tuple(filters.get("environments", ())),
                configurations=tuple(filters.get("configurations", ())),
                from_date=str(arguments["from"]),
                to_date=str(arguments["to"]),
                timezone=str(arguments["timezone"]),
                as_of=datetime.fromisoformat(str(arguments["asOf"]).replace("Z", "+00:00")),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("insights tool input failed generated contract validation") from exc
        return await self._port.query_insights(scope, parsed)
