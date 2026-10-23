"""Agent team view HTTP surface (spec 2026-10-22-agent-team-view-design.md,
section 5): team-wide reads, per-Agent/per-person detail, the org chart,
the printable report, the CSV zip export, and the three personnel writes.
All logic lives in ``AgentTeamService`` (Task 7) and ``build_export`` (Task
9); this module only shapes the public contract.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response

from tap.contracts.http import (
    AgentOwnerTransferRequest,
    AgentProfileUpdateRequest,
    AgentTeamFeedbackResolveRequest,
    AgentTeamHistoryEvent,
    AgentTeamHistoryPage,
    AgentTeamMemberRow,
    AgentTeamMembersView,
    AgentTeamMemberView,
    AgentTeamOrgChart,
    AgentTeamOrgChartEdge,
    AgentTeamOrgChartView,
    AgentTeamPendingFeedbackItem,
    AgentTeamPeopleView,
    AgentTeamPeriod,
    AgentTeamPeriodReport,
    AgentTeamPersonDetail,
    AgentTeamPersonRow,
    AgentTeamPersonView,
    AgentTeamReport,
    AgentTeamReportView,
    AgentTeamResolvedFeedback,
    AgentTeamServedActor,
    AgentTeamSummary,
    AgentTeamSummaryView,
    AgentTeamTrendPoint,
)
from tap.interfaces.http.dependencies import agent_team_service
from tap.interfaces.http.problems import problem_response_metadata, skill_problem_response_metadata
from tap.interfaces.http.scope import project_authorization
from tap.modules.agent_team.application.export import build_export
from tap.modules.agent_team.application.service import AgentTeamRejected, PersonDetail, TeamReport
from tap.modules.agent_team.domain.models import (
    AgentKind,
    MaintenanceEvent,
    MemberRow,
    MemberStatus,
    OrgChart,
    Period,
    PeriodInvalid,
    PersonRow,
    TeamSummary,
    trailing_30_days,
)
from tap.modules.agent_team.ports import PendingFeedback, ResolvedFeedback, ServedActor, TrendPoint

router = APIRouter(prefix="/ai", tags=["ai"])

_READ = Depends(project_authorization("ai.agent_team.read"))
_WRITE = Depends(project_authorization("ai.agents.write"))

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: skill_problem_response_metadata("Agent team request rejected"),
    403: skill_problem_response_metadata("Built-in or Tapper-default agent is read-only"),
    404: skill_problem_response_metadata("Agent or feedback not found"),
    422: problem_response_metadata("Request validation failed"),
}


# -- query parsing ----------------------------------------------------------


def _parse_period(from_: str, to: str) -> Period:
    try:
        start = date.fromisoformat(from_)
        end = date.fromisoformat(to)
    except ValueError as exc:
        raise AgentTeamRejected("period_invalid", "from/to must be ISO dates") from exc
    try:
        return Period.parse(start, end)
    except PeriodInvalid as exc:
        raise AgentTeamRejected("period_invalid", str(exc)) from exc


def _period_query(
    from_: str = Query(alias="from"),
    to: str = Query(alias="to"),
) -> Period:
    return _parse_period(from_, to)


# -- serialization ------------------------------------------------------


def _period_dto(period: Period) -> AgentTeamPeriod:
    return AgentTeamPeriod.model_validate({"from": period.start, "to": period.end})


def _served_dto(actor: ServedActor) -> AgentTeamServedActor:
    return AgentTeamServedActor(actor_id=actor.actor_id, runs=actor.runs)


def _trend_dto(point: TrendPoint) -> AgentTeamTrendPoint:
    return AgentTeamTrendPoint(
        bucket_start=point.bucket_start,
        agent_key=point.agent_key,
        runs=point.runs,
        failures=point.failures,
        latency_total_ms=point.latency_total_ms,
        cost_usd=point.cost_usd,
    )


def _member_dto(row: MemberRow) -> AgentTeamMemberRow:
    return AgentTeamMemberRow(
        agent_key=row.agent_key,
        name=row.name,
        kind=row.kind,
        job_title=row.job_title,
        owner_actor_id=row.owner_actor_id,
        joined_at=row.joined_at,
        status=row.status,
        runs=row.runs,
        failures=row.failures,
        abstained=row.abstained,
        canceled=row.canceled,
        latency_total_ms=row.latency_total_ms,
        latency_p50_ms=row.latency_p50_ms,
        cost_usd=row.cost_usd,
        served_count=row.served_count,
        top_served=[_served_dto(actor) for actor in row.top_served],
        last_run_at=row.last_run_at,
        satisfaction=row.satisfaction,
        satisfaction_sample=row.satisfaction_sample,
        last_maintenance_at=row.last_maintenance_at,
        pending_feedback=row.pending_feedback,
        maintenance={str(kind): count for kind, count in row.maintenance.items()},
        trend=[_trend_dto(point) for point in row.trend],
    )


def _summary_dto(summary: TeamSummary) -> AgentTeamSummary:
    return AgentTeamSummary(
        agent_total=summary.agent_total,
        active=summary.active,
        idle=summary.idle,
        owners_avg=summary.owners_avg,
        served_avg=summary.served_avg,
        runs=summary.runs,
        latency_total_ms=summary.latency_total_ms,
        cost_usd=summary.cost_usd,
        maintenance_total=summary.maintenance_total,
    )


def _person_row_dto(row: PersonRow) -> AgentTeamPersonRow:
    return AgentTeamPersonRow(
        actor_id=row.actor_id,
        owned_agents=row.owned_agents,
        owned_runs=row.owned_runs,
        served_by_agents=row.served_by_agents,
        runs=row.runs,
        maintenance=row.maintenance,
    )


def _org_chart_dto(chart: OrgChart) -> AgentTeamOrgChart:
    return AgentTeamOrgChart(
        people=list(chart.people),
        agents=list(chart.agents),
        edges=[
            AgentTeamOrgChartEdge.model_validate({"from": edge[0], "to": edge[1], "kind": edge[2]})
            for edge in chart.edges
        ],
    )


def _pending_feedback_dto(item: PendingFeedback) -> AgentTeamPendingFeedbackItem:
    return AgentTeamPendingFeedbackItem(
        feedback_id=item.feedback_id,
        agent_key=item.agent_key,
        turn_id=item.turn_id,
        conversation_id=item.conversation_id,
        actor_id=item.actor_id,
        rating=item.rating,
        comment=item.comment,
        updated_at=item.updated_at,
    )


def _resolved_feedback_dto(item: ResolvedFeedback) -> AgentTeamResolvedFeedback:
    return AgentTeamResolvedFeedback(
        feedback_id=item.feedback_id,
        agent_key=item.agent_key,
        turn_id=item.turn_id,
        conversation_id=item.conversation_id,
        actor_id=item.actor_id,
        rating=item.rating,
        comment=item.comment,
        updated_at=item.updated_at,
        resolved_at=item.resolved_at,
        resolved_by=item.resolved_by,
        resolution_note=item.resolution_note,
    )


def _person_detail_dto(detail: PersonDetail) -> AgentTeamPersonDetail:
    return AgentTeamPersonDetail(
        person=_person_row_dto(detail.person),
        owned=[_member_dto(row) for row in detail.owned],
        served=[_member_dto(row) for row in detail.served],
        pending_feedback=[_pending_feedback_dto(item) for item in detail.pending_feedback],
    )


def _history_event_dto(event: MaintenanceEvent) -> AgentTeamHistoryEvent:
    return AgentTeamHistoryEvent(
        event_id=event.event_id,
        agent_id=event.agent_id,
        actor_id=event.actor_id,
        kind=event.kind,
        revision_id=event.revision_id,
        feedback_id=event.feedback_id,
        note=event.note,
        occurred_at=event.occurred_at,
    )


def _period_report_dto(report) -> AgentTeamPeriodReport:
    return AgentTeamPeriodReport(
        summary=_summary_dto(report.summary),
        members=[_member_dto(row) for row in report.members],
        people=[_person_row_dto(row) for row in report.people],
    )


def _team_report_dto(report: TeamReport) -> AgentTeamReport:
    return AgentTeamReport(
        current=_period_report_dto(report.current),
        previous=_period_report_dto(report.previous),
    )


def _envelope_kwargs(view) -> dict:
    return {
        "runtime_status": view.runtime_status,
        "computed_at": view.computed_at,
        "timezone": view.timezone,
        "period": _period_dto(view.period),
        "granularity": view.granularity,
    }


# -- team-wide reads ----------------------------------------------------


@router.get(
    "/agent-team/summary",
    operation_id="agent_team_get_summary",
    response_model=AgentTeamSummaryView,
    dependencies=[_READ],
    responses=_ERROR_RESPONSES,
)
async def get_summary(
    request: Request, period: Period = Depends(_period_query)
) -> AgentTeamSummaryView:
    view = await agent_team_service(request).summary(period)
    return AgentTeamSummaryView(**_envelope_kwargs(view), data=_summary_dto(view.data))


@router.get(
    "/agent-team/members",
    operation_id="agent_team_list_members",
    response_model=AgentTeamMembersView,
    dependencies=[_READ],
    responses=_ERROR_RESPONSES,
)
async def list_members(
    request: Request,
    period: Period = Depends(_period_query),
    owner: str | None = Query(default=None),
    status: MemberStatus | None = Query(default=None),
    kind: AgentKind | None = Query(default=None),
    sort: str = Query(default="runs"),
    order: str = Query(default="desc"),
) -> AgentTeamMembersView:
    view = await agent_team_service(request).members(
        period,
        owner=owner,
        status=status,
        kind=kind,
        sort=sort,
        order=order,
    )
    return AgentTeamMembersView(
        **_envelope_kwargs(view), data=[_member_dto(row) for row in view.data]
    )


@router.get(
    "/agent-team/members/{agent_key}",
    operation_id="agent_team_get_member",
    response_model=AgentTeamMemberView,
    dependencies=[_READ],
    responses=_ERROR_RESPONSES,
)
async def get_member(
    request: Request, agent_key: str, period: Period = Depends(_period_query)
) -> AgentTeamMemberView:
    view = await agent_team_service(request).member(agent_key, period)
    return AgentTeamMemberView(**_envelope_kwargs(view), data=_member_dto(view.data))


@router.get(
    "/agent-team/members/{agent_key}/history",
    operation_id="agent_team_get_member_history",
    response_model=AgentTeamHistoryPage,
    dependencies=[_READ],
    responses=_ERROR_RESPONSES,
)
async def get_member_history(
    request: Request,
    agent_key: str,
    cursor: str | None = Query(default=None, min_length=1, max_length=2048),
) -> AgentTeamHistoryPage:
    page = await agent_team_service(request).history(agent_key, cursor)
    return AgentTeamHistoryPage(
        events=[_history_event_dto(event) for event in page.events],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/agent-team/people",
    operation_id="agent_team_list_people",
    response_model=AgentTeamPeopleView,
    dependencies=[_READ],
    responses=_ERROR_RESPONSES,
)
async def list_people(
    request: Request,
    period: Period = Depends(_period_query),
    sort: str = Query(default="runs"),
    order: str = Query(default="desc"),
) -> AgentTeamPeopleView:
    view = await agent_team_service(request).people(period, sort, order)
    return AgentTeamPeopleView(
        **_envelope_kwargs(view), data=[_person_row_dto(row) for row in view.data]
    )


@router.get(
    "/agent-team/people/{actor_id}",
    operation_id="agent_team_get_person",
    response_model=AgentTeamPersonView,
    dependencies=[_READ],
    responses=_ERROR_RESPONSES,
)
async def get_person(
    request: Request, actor_id: str, period: Period = Depends(_period_query)
) -> AgentTeamPersonView:
    view = await agent_team_service(request).person(actor_id, period)
    return AgentTeamPersonView(**_envelope_kwargs(view), data=_person_detail_dto(view.data))


@router.get(
    "/agent-team/org-chart",
    operation_id="agent_team_get_org_chart",
    response_model=AgentTeamOrgChartView,
    dependencies=[_READ],
    responses=_ERROR_RESPONSES,
)
async def get_org_chart(
    request: Request, period: Period = Depends(_period_query)
) -> AgentTeamOrgChartView:
    view = await agent_team_service(request).org_chart(period)
    return AgentTeamOrgChartView(**_envelope_kwargs(view), data=_org_chart_dto(view.data))


@router.get(
    "/agent-team/report",
    operation_id="agent_team_get_report",
    response_model=AgentTeamReportView,
    dependencies=[_READ],
    responses=_ERROR_RESPONSES,
)
async def get_report(
    request: Request, period: Period = Depends(_period_query)
) -> AgentTeamReportView:
    view = await agent_team_service(request).report(period)
    return AgentTeamReportView(**_envelope_kwargs(view), data=_team_report_dto(view.data))


@router.get(
    "/agent-team/export",
    operation_id="agent_team_export",
    dependencies=[_READ],
    responses={
        **_ERROR_RESPONSES,
        200: {
            "description": "CSV zip export (roster, people, trend detail)",
            "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
        },
    },
)
async def export_agent_team(request: Request, period: Period = Depends(_period_query)) -> Response:
    view = await agent_team_service(request).export_data(period)
    content = build_export(
        view.data.report,
        list(view.data.trend),
        period=period,
        runtime_status=view.runtime_status,
    )
    filename = f"agent-team-{period.start.isoformat()}-{period.end.isoformat()}.zip"
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# -- personnel writes -----------------------------------------------------


@router.put(
    "/agents/{agent_id}/profile",
    operation_id="agent_team_update_agent_profile",
    response_model=AgentTeamMemberRow,
    dependencies=[_WRITE],
    responses=_ERROR_RESPONSES,
)
async def update_agent_profile(
    request: Request, agent_id: str, body: AgentProfileUpdateRequest
) -> AgentTeamMemberRow:
    await agent_team_service(request).update_job_title(agent_id, body.job_title)
    return _member_dto(await _member_row_for(request, agent_id))


@router.post(
    "/agents/{agent_id}/owner",
    operation_id="agent_team_transfer_agent_owner",
    response_model=AgentTeamMemberRow,
    dependencies=[_WRITE],
    responses=_ERROR_RESPONSES,
)
async def transfer_agent_owner(
    request: Request, agent_id: str, body: AgentOwnerTransferRequest
) -> AgentTeamMemberRow:
    await agent_team_service(request).transfer_owner(agent_id, body.new_owner_actor_id)
    return _member_dto(await _member_row_for(request, agent_id))


@router.post(
    "/feedback/{feedback_id}/resolve",
    operation_id="agent_team_resolve_feedback",
    response_model=AgentTeamResolvedFeedback,
    dependencies=[_WRITE],
    responses=_ERROR_RESPONSES,
)
async def resolve_feedback(
    request: Request, feedback_id: str, body: AgentTeamFeedbackResolveRequest
) -> AgentTeamResolvedFeedback:
    resolved = await agent_team_service(request).resolve_feedback(feedback_id, body.note)
    return _resolved_feedback_dto(resolved)


async def _member_row_for(request: Request, agent_id: str) -> MemberRow:
    """`update_job_title`/`transfer_owner` write only personnel fields on the
    MySQL `AgentRecord`, but the public contract returns the full `MemberRow`
    (personnel fields merged with runtime metrics), consistent with every
    other Agent team response. Neither write takes a period, so the trailing
    30-day window already used to classify "active"/"idle" (spec 1.4) is the
    one period both writes re-read the roster entry with."""

    view = await agent_team_service(request).member(
        agent_id, trailing_30_days(datetime.now(UTC).date())
    )
    return view.data
