"""Public TAP Insights HTTP contracts and domain mappings."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tap_platform.insights.application.queries import (
    AttemptDetail,
    FailureDetail,
    MetricQuery,
    QueryFilters,
    QueryRecord,
    RunSummary,
    TrendPoint,
)
from tap_platform.insights.domain.metrics import METRIC_CATALOG, MetricId, MetricValue


class ContractModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class MetricFiltersContract(ContractModel):
    source_ids: tuple[str, ...] = Field(default=(), alias="sourceIds", max_length=50)
    run_ids: tuple[str, ...] = Field(default=(), alias="runIds", max_length=100)
    environments: tuple[str, ...] = Field(default=(), max_length=20)
    configurations: tuple[str, ...] = Field(default=(), max_length=20)


class MetricQueryRequest(ContractModel):
    metric_ids: tuple[MetricId, ...] = Field(
        alias="metricIds", min_length=1, max_length=len(MetricId)
    )
    filters: MetricFiltersContract = MetricFiltersContract()
    from_date: str = Field(alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$")
    to_date: str = Field(alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$")
    timezone: str = Field(min_length=1, max_length=64)
    as_of: datetime = Field(alias="asOf")

    def to_domain(self) -> MetricQuery:
        return MetricQuery(
            metric_ids=self.metric_ids,
            filters=QueryFilters(
                source_ids=self.filters.source_ids,
                run_ids=self.filters.run_ids,
                environments=self.filters.environments,
                configurations=self.filters.configurations,
            ),
            from_date=self.from_date,
            to_date=self.to_date,
            timezone=self.timezone,
            as_of=self.as_of,
        )


class MetricCatalogItemContract(ContractModel):
    metric_id: MetricId = Field(alias="metricId")
    label: str
    unit: Literal["ratio", "count"]
    definition: str
    metric_version: str = Field(alias="metricVersion")


class MetricCatalogContract(ContractModel):
    items: list[MetricCatalogItemContract]


class FactWatermarkContract(ContractModel):
    projection_version: str = Field(alias="projectionVersion")
    visible_data_version: int = Field(alias="visibleDataVersion", ge=0)


class MetricResultContract(ContractModel):
    metric_id: MetricId = Field(alias="metricId")
    numerator: int | None
    denominator: int | None
    value: float | None
    completeness: Literal["complete", "empty", "unavailable"]
    missing_reasons: list[str] = Field(alias="missingReasons")
    evidence_refs: list[str] = Field(alias="evidenceRefs")


class TrendPointContract(ContractModel):
    local_date: str = Field(alias="localDate")
    metrics: list[MetricResultContract]


class MetricQueryResponse(ContractModel):
    query_id: str = Field(alias="queryId")
    metric_version: str = Field(alias="metricVersion")
    filters: MetricFiltersContract
    from_date: str = Field(alias="from")
    to_date: str = Field(alias="to")
    timezone: str
    as_of: datetime = Field(alias="asOf")
    created_at: datetime = Field(alias="createdAt")
    fact_watermark: FactWatermarkContract = Field(alias="factWatermark")
    metrics: list[MetricResultContract]
    trends: list[TrendPointContract]


class RunSummaryContract(ContractModel):
    run_id: str = Field(alias="runId")
    source_id: str = Field(alias="sourceId")
    environment: str
    configuration: str
    started_at: datetime | None = Field(alias="startedAt")
    instance_count: int = Field(alias="instanceCount")
    evidence_refs: list[str] = Field(alias="evidenceRefs")


class RunPageContract(ContractModel):
    query_id: str = Field(alias="queryId")
    items: list[RunSummaryContract]
    next_cursor: str | None = Field(alias="nextCursor")


class FailureDetailContract(ContractModel):
    fact_key: str = Field(alias="factKey")
    run_id: str = Field(alias="runId")
    source_id: str = Field(alias="sourceId")
    stable_test_id: str | None = Field(alias="stableTestId")
    source_test_identity: str = Field(alias="sourceTestIdentity")
    data_row: str | None = Field(alias="dataRow")
    result: Literal["fail", "error"]
    configuration: str
    evidence_refs: list[str] = Field(alias="evidenceRefs")


class FailurePageContract(ContractModel):
    query_id: str = Field(alias="queryId")
    items: list[FailureDetailContract]
    next_cursor: str | None = Field(alias="nextCursor")


class AttemptDetailContract(ContractModel):
    fact_key: str = Field(alias="factKey")
    stable_test_id: str | None = Field(alias="stableTestId")
    source_test_identity: str = Field(alias="sourceTestIdentity")
    data_row: str | None = Field(alias="dataRow")
    attempt: int | None
    result: str
    duration_seconds: float | None = Field(alias="durationSeconds")
    evidence_refs: list[str] = Field(alias="evidenceRefs")


class AttemptPageContract(ContractModel):
    query_id: str = Field(alias="queryId")
    run_id: str = Field(alias="runId")
    items: list[AttemptDetailContract]
    next_cursor: str | None = Field(alias="nextCursor")


def catalog_contract() -> MetricCatalogContract:
    return MetricCatalogContract(
        items=[
            MetricCatalogItemContract(
                metricId=item.metric_id,
                label=item.label,
                unit=item.unit,
                definition=item.definition,
                metricVersion="insights-metrics-v1",
            )
            for item in METRIC_CATALOG
        ]
    )


def query_contract(record: QueryRecord) -> MetricQueryResponse:
    return MetricQueryResponse(
        queryId=record.query_id,
        metricVersion=record.metric_version,
        filters=MetricFiltersContract(
            sourceIds=record.query.filters.source_ids,
            runIds=record.query.filters.run_ids,
            environments=record.query.filters.environments,
            configurations=record.query.filters.configurations,
        ),
        **{
            "from": record.query.from_date,
            "to": record.query.to_date,
        },
        timezone=record.query.timezone,
        asOf=record.query.as_of,
        createdAt=record.created_at,
        factWatermark=FactWatermarkContract(
            projectionVersion=record.snapshot.projection_version,
            visibleDataVersion=record.snapshot.visible_data_version,
        ),
        metrics=[_metric_contract(value) for value in record.metrics],
        trends=[_trend_contract(point) for point in record.trends],
    )


def run_page_contract(
    record: QueryRecord, *, cursor: int, limit: int
) -> RunPageContract:
    page = record.runs[cursor : cursor + limit]
    next_cursor = str(cursor + limit) if cursor + limit < len(record.runs) else None
    return RunPageContract(
        queryId=record.query_id,
        items=[_run_contract(item) for item in page],
        nextCursor=next_cursor,
    )


def failure_page_contract(
    record: QueryRecord, *, cursor: int, limit: int
) -> FailurePageContract:
    page = record.failures[cursor : cursor + limit]
    next_cursor = str(cursor + limit) if cursor + limit < len(record.failures) else None
    return FailurePageContract(
        queryId=record.query_id,
        items=[_failure_contract(item) for item in page],
        nextCursor=next_cursor,
    )


def attempt_page_contract(
    record: QueryRecord, *, run_id: str, cursor: int, limit: int
) -> AttemptPageContract:
    matching = tuple(item for item in record.attempts if item.run_id == run_id)
    page = matching[cursor : cursor + limit]
    next_cursor = str(cursor + limit) if cursor + limit < len(matching) else None
    return AttemptPageContract(
        queryId=record.query_id,
        runId=run_id,
        items=[_attempt_contract(item) for item in page],
        nextCursor=next_cursor,
    )


def _metric_contract(value: MetricValue) -> MetricResultContract:
    return MetricResultContract(
        metricId=value.metric_id,
        numerator=value.numerator,
        denominator=value.denominator,
        value=value.value,
        completeness=value.completeness,
        missingReasons=list(value.missing_reasons),
        evidenceRefs=list(value.evidence_refs),
    )


def _trend_contract(value: TrendPoint) -> TrendPointContract:
    return TrendPointContract(
        localDate=value.local_date,
        metrics=[_metric_contract(metric) for metric in value.metrics],
    )


def _run_contract(value: RunSummary) -> RunSummaryContract:
    return RunSummaryContract(
        runId=value.run_id,
        sourceId=value.source_id,
        environment=value.environment,
        configuration=value.configuration,
        startedAt=value.started_at,
        instanceCount=value.instance_count,
        evidenceRefs=list(value.evidence_refs),
    )


def _failure_contract(value: FailureDetail) -> FailureDetailContract:
    return FailureDetailContract(
        factKey=value.fact_key,
        runId=value.run_id,
        sourceId=value.source_id,
        stableTestId=value.stable_test_id,
        sourceTestIdentity=value.source_test_identity,
        dataRow=value.data_row,
        result=value.result,
        configuration=value.configuration,
        evidenceRefs=list(value.evidence_refs),
    )


def _attempt_contract(value: AttemptDetail) -> AttemptDetailContract:
    return AttemptDetailContract(
        factKey=value.fact_key,
        stableTestId=value.stable_test_id,
        sourceTestIdentity=value.source_test_identity,
        dataRow=value.data_row,
        attempt=value.attempt,
        result=value.result,
        durationSeconds=value.duration_seconds,
        evidenceRefs=list(value.evidence_refs),
    )
