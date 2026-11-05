"""Skill Hub HTTP surface (spec 2026-11-01-skill-hub-design.md, section 5):
the library, a Skill's detail and usage trend, the library summary, the CSV
zip export, and the two Skill writes (`PUT /skills/{skillId}/profile`,
`POST /skills/{skillId}/owner`). All logic lives in `SkillHubService` and
`build_skill_export`; this module only shapes the public contract.
"""

from __future__ import annotations

from typing import Any, get_args

from fastapi import APIRouter, Depends, Query, Request, Response

from tap.contracts.http import (
    AgentTeamPeriod,
    SkillHubCatalogItem,
    SkillHubCatalogView,
    SkillHubDetail,
    SkillHubDetailView,
    SkillHubLibrarySummary,
    SkillHubModeCounts,
    SkillHubRankedKey,
    SkillHubRuntimeStats,
    SkillHubSkill,
    SkillHubSummaryView,
    SkillHubUsage,
    SkillHubUsagePoint,
    SkillHubUsageSeries,
    SkillHubUsageView,
    SkillOwnerTransferRequest,
    SkillProfileUpdateRequest,
)
from tap.interfaces.http.dependencies import skill_hub_service
from tap.interfaces.http.problems import problem_response_metadata, skill_problem_response_metadata
from tap.interfaces.http.routes.ai_skills import _ApplicableTask, _file_view, _summary
from tap.interfaces.http.scope import project_authorization
from tap.modules.ai.domain.skill_metadata import SKILL_CATEGORIES
from tap.modules.skill_hub.application.export import build_skill_export
from tap.modules.skill_hub.application.service import SkillHubRejected
from tap.modules.skill_hub.domain.models import (
    CatalogItem,
    HubView,
    LibrarySummary,
    ModeCounts,
    SkillDetail,
    SkillUsage,
    UsageSeries,
)
from tap.modules.skill_hub.ports import RankedKey, SkillRecord
from tap.platform.reporting.period import (
    PERIOD_PRESETS,
    Period,
    PeriodInvalid,
    parse_period_query,
    parse_preset,
)

router = APIRouter(prefix="/ai", tags=["ai"])

_READ = Depends(project_authorization("ai.skill_hub.read"))
_WRITE = Depends(project_authorization("ai.skills.write"))

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: skill_problem_response_metadata("Skill Hub request rejected"),
    403: skill_problem_response_metadata("Built-in Skill is read-only"),
    404: skill_problem_response_metadata("Skill not found"),
    422: problem_response_metadata("Request validation failed"),
}

_SOURCES = ("imported", "authored", "built_in")
_STATUSES = ("enabled", "disabled")
_SORTS = ("name", "last_used_at", "activations", "updated_at")
_ORDERS = ("asc", "desc")


# -- query parsing ----------------------------------------------------------


def _period_query(
    from_: str = Query(alias="from"),
    to: str = Query(alias="to"),
    # Accepted for parity with the Agent team view; validated only (the
    # Skill Hub has no previous-period comparison). Plain `str` so an
    # unknown value maps to 400 `period_invalid`, not 422.
    preset: str | None = Query(
        default=None,
        description="Period selector that produced from/to; unknown values are 400 period_invalid.",
        json_schema_extra={"enum": list(PERIOD_PRESETS)},
    ),
) -> Period:
    try:
        parse_preset(preset)
        return parse_period_query(from_, to)
    except PeriodInvalid as exc:
        raise SkillHubRejected("period_invalid", str(exc)) from exc


def _enum(values: tuple[str, ...]) -> dict[str, Any]:
    # Filters stay plain `str` so an unknown value reaches the service's
    # `filter_invalid`/`sort_invalid` 400 rather than FastAPI's 422; the
    # allowed values stay documented in the schema.
    return {"enum": list(values)}


# -- serialization ------------------------------------------------------


def _period_dto(period: Period) -> AgentTeamPeriod:
    return AgentTeamPeriod.model_validate({"from": period.start, "to": period.end})


def _envelope_kwargs(view: HubView[Any]) -> dict[str, Any]:
    return {
        "runtime_status": view.runtime_status,
        "computed_at": view.computed_at,
        "timezone": view.timezone,
        "period": _period_dto(view.period),
        "granularity": view.granularity,
    }


def _skill_dto(record: SkillRecord) -> SkillHubSkill:
    return SkillHubSkill.model_validate(
        {
            "skill_id": record.skill_id,
            "name": record.name,
            "description": record.description,
            "category": record.category,
            "version": record.version,
            "latest_revision_id": record.latest_revision_id,
            "revision_number": record.revision_number,
            "source_kind": record.source_kind,
            "origin_kind": record.origin_kind,
            "origin_ref": record.origin_ref,
            "origin_imported_at": record.origin_imported_at,
            "origin_actor_id": record.origin_actor_id,
            "owner_actor_id": record.owner_actor_id,
            "owner_assigned_at": record.owner_assigned_at,
            "status": record.status,
            "applicable_tasks": list(record.applicable_tasks),
            "compatibility": record.compatibility,
            "updated_at": record.updated_at,
            "built_in": record.built_in,
        }
    )


def _modes_dto(modes: ModeCounts) -> SkillHubModeCounts:
    return SkillHubModeCounts(
        manual=modes.get("manual", 0), pinned=modes.get("pinned", 0), auto=modes.get("auto", 0)
    )


def _catalog_item_dto(item: CatalogItem) -> SkillHubCatalogItem:
    return SkillHubCatalogItem(
        skill=_skill_dto(item.record),
        activations=item.activations,
        by_mode=_modes_dto(item.by_mode),
    )


def _usage_dto(usage: SkillUsage) -> SkillHubUsage:
    runtime = usage.runtime
    return SkillHubUsage(
        by_mode=_modes_dto(usage.by_mode),
        users=usage.users,
        satisfaction=usage.satisfaction,
        satisfaction_sample=usage.satisfaction_sample,
        last_used_at=usage.last_used_at,
        pinned_by_agents=usage.pinned_by_agents,
        runtime=None
        if runtime is None
        else SkillHubRuntimeStats(
            runs=runtime.runs,
            failures=runtime.failures,
            latency_total_ms=runtime.latency_total_ms,
            cost_usd=runtime.cost_usd,
        ),
    )


def _ranked_dto(item: RankedKey) -> SkillHubRankedKey:
    return SkillHubRankedKey(key=item.key, runs=item.runs)


def _detail_dto(detail: SkillDetail) -> SkillHubDetail:
    latest = detail.record.latest_revision_id
    return SkillHubDetail(
        skill=_skill_dto(detail.record),
        usage=_usage_dto(detail.usage),
        revisions=[
            _summary(revision, is_latest=revision.revision_id == latest)
            for revision in detail.revisions
        ],
        files=[_file_view(file) for file in detail.files],
        pinned_by=list(detail.pinned_by),
        top_agents=[_ranked_dto(item) for item in detail.top_agents],
        top_callers=[_ranked_dto(item) for item in detail.top_callers],
    )


def _usage_series_dto(series: UsageSeries) -> SkillHubUsageSeries:
    return SkillHubUsageSeries(
        points=[
            SkillHubUsagePoint(bucket_start=point.bucket_start, mode=point.mode, runs=point.runs)
            for point in series.points
        ],
        by_mode=_modes_dto(series.by_mode),
    )


def _summary_dto(summary: LibrarySummary) -> SkillHubLibrarySummary:
    return SkillHubLibrarySummary(
        skill_total=summary.skill_total,
        enabled=summary.enabled,
        used_in_period=summary.used_in_period,
        unused=summary.unused,
        auto_share=summary.auto_share,
        candidate_count=summary.candidate_count,
        candidate_limit=summary.candidate_limit,
    )


# -- reads --------------------------------------------------------------


@router.get(
    "/skill-hub/catalog",
    operation_id="skill_hub_list_catalog",
    response_model=SkillHubCatalogView,
    dependencies=[_READ],
    responses=_ERROR_RESPONSES,
)
async def list_catalog(
    request: Request,
    period: Period = Depends(_period_query),
    q: str | None = Query(default=None, json_schema_extra={"maxLength": 128}),
    category: str | None = Query(default=None, json_schema_extra=_enum(SKILL_CATEGORIES)),
    task: str | None = Query(default=None, json_schema_extra=_enum(get_args(_ApplicableTask))),
    source: str | None = Query(default=None, json_schema_extra=_enum(_SOURCES)),
    status: str | None = Query(default=None, json_schema_extra=_enum(_STATUSES)),
    sort: str = Query(default="activations", json_schema_extra=_enum(_SORTS)),
    order: str = Query(default="desc", json_schema_extra=_enum(_ORDERS)),
) -> SkillHubCatalogView:
    view = await skill_hub_service(request).catalog(
        period,
        q=q,
        category=category,
        task=task,
        source=source,
        status=status,
        sort=sort,
        order=order,
    )
    return SkillHubCatalogView(
        **_envelope_kwargs(view), data=[_catalog_item_dto(item) for item in view.data]
    )


@router.get(
    "/skill-hub/catalog/{skill_id}",
    operation_id="skill_hub_get_skill",
    response_model=SkillHubDetailView,
    dependencies=[_READ],
    responses=_ERROR_RESPONSES,
)
async def get_skill(
    request: Request, skill_id: str, period: Period = Depends(_period_query)
) -> SkillHubDetailView:
    view = await skill_hub_service(request).detail(skill_id, period)
    return SkillHubDetailView(**_envelope_kwargs(view), data=_detail_dto(view.data))


@router.get(
    "/skill-hub/catalog/{skill_id}/usage",
    operation_id="skill_hub_get_skill_usage",
    response_model=SkillHubUsageView,
    dependencies=[_READ],
    responses=_ERROR_RESPONSES,
)
async def get_skill_usage(
    request: Request, skill_id: str, period: Period = Depends(_period_query)
) -> SkillHubUsageView:
    view = await skill_hub_service(request).usage(skill_id, period)
    return SkillHubUsageView(**_envelope_kwargs(view), data=_usage_series_dto(view.data))


@router.get(
    "/skill-hub/summary",
    operation_id="skill_hub_get_summary",
    response_model=SkillHubSummaryView,
    dependencies=[_READ],
    responses=_ERROR_RESPONSES,
)
async def get_summary(
    request: Request, period: Period = Depends(_period_query)
) -> SkillHubSummaryView:
    view = await skill_hub_service(request).summary(period)
    return SkillHubSummaryView(**_envelope_kwargs(view), data=_summary_dto(view.data))


@router.get(
    "/skill-hub/export",
    operation_id="skill_hub_export",
    dependencies=[_READ],
    responses={
        **_ERROR_RESPONSES,
        200: {
            "description": "CSV zip export (library, usage detail)",
            "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
        },
    },
)
async def export_skill_hub(request: Request, period: Period = Depends(_period_query)) -> Response:
    view = await skill_hub_service(request).export_data(period)
    content = build_skill_export(
        view.data.items, view.data.usage, period=period, runtime_status=view.runtime_status
    )
    filename = f"skill-hub-{period.start.isoformat()}-{period.end.isoformat()}.zip"
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# -- Skill writes -----------------------------------------------------------


@router.put(
    "/skills/{skill_id}/profile",
    operation_id="skill_hub_update_skill_profile",
    response_model=SkillHubSkill,
    dependencies=[_WRITE],
    responses=_ERROR_RESPONSES,
)
async def update_skill_profile(
    request: Request, skill_id: str, body: SkillProfileUpdateRequest
) -> SkillHubSkill:
    record = await skill_hub_service(request).update_profile(skill_id, body.category)
    return _skill_dto(record)


@router.post(
    "/skills/{skill_id}/owner",
    operation_id="skill_hub_transfer_skill_owner",
    response_model=SkillHubSkill,
    dependencies=[_WRITE],
    responses=_ERROR_RESPONSES,
)
async def transfer_skill_owner(
    request: Request, skill_id: str, body: SkillOwnerTransferRequest
) -> SkillHubSkill:
    record = await skill_hub_service(request).transfer_owner(skill_id, body.new_owner_actor_id)
    return _skill_dto(record)
