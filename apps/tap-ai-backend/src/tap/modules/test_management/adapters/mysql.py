"""Project-scoped MySQL Test Plan repository."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKeyConstraint,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    delete,
    func,
    insert,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.mysql import DATETIME, JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.contracts.events import ProjectEventEnvelope
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.ai.adapters.mysql import ai_agent_revision, skill_revision
from tap.modules.ai.domain.assets import AssetRevisionStatus
from tap.modules.ai.domain.models import ModelGatewayUnavailable
from tap.modules.chat.adapters.mysql import chat_event, chat_turn
from tap.modules.chat.adapters.mysql_conversations import (
    conversation,
    turn_answer_evidence_snapshot,
    turn_artifact_link,
    turn_input_snapshot,
)
from tap.modules.governance.adapters.mysql_audit import MysqlProjectAudit
from tap.modules.governance.domain.audit import (
    AuditAction,
    AuditOutcome,
    AuditResource,
    SafeAuditMetadata,
)
from tap.modules.test_management.domain.models import (
    BddKeyword,
    CitationOrigin,
    GapSeverity,
    GenerationJobStatus,
    IdentityOrigin,
    RequirementScopeItem,
    RequirementScopeSnapshot,
    ReviewDisposition,
    RevisionStatus,
    TestCase,
    TestPlanAssumption,
    TestPlanCitation,
    TestPlanCoverageGap,
    TestPlanGenerationJob,
    TestPlanGenerationRequest,
    TestPlanReviewDecision,
    TestPlanReviewSummary,
    TestPlanRevision,
    TestPlanStep,
    TestPlanUnknown,
    TestScenario,
)
from tap.modules.test_management.domain.validation import RevisionConflict, RevisionImmutable
from tap.modules.test_management.ports.generation import (
    ClaimedTestDesignJob,
    GenerationResponseUnknown,
    TestDesignContext,
    TestDesignGenerator,
)
from tap.platform.db.project_scope import require_project_scope, scope_predicates, scope_values
from tap.platform.db.schema import metadata
from tap.platform.messaging.mysql_outbox import scoped_outbox_id, write_project_event


def _scope_constraints(name: str):
    return (
        ForeignKeyConstraint(
            ["enterprise_id", "project_id"],
            ["project.enterprise_id", "project.project_id"],
            name=f"fk_{name}_scope_project",
        ),
        ForeignKeyConstraint(
            ["enterprise_id", "actor_id"],
            ["actor_principal.enterprise_id", "actor_principal.actor_id"],
            name=f"fk_{name}_scope_actor",
        ),
    )


def _scoped(name: str, *columns, uniques=(), parents=()):
    return Table(
        name,
        metadata,
        *columns,
        Column("enterprise_id", String(128), nullable=False),
        Column("project_id", String(128), primary_key=True),
        Column("actor_id", String(128), nullable=False),
        Column("identity_mode", String(16), nullable=False),
        Column("identity_origin", String(16), nullable=False),
        *(UniqueConstraint("project_id", *fields, name=name_) for fields, name_ in uniques),
        *(
            ForeignKeyConstraint(
                ["project_id", *source],
                [f"{target}.project_id", *[f"{target}.{item}" for item in destination]],
                name=name_,
            )
            for source, target, destination, name_ in parents
        ),
        *_scope_constraints(name),
    )


test_plan = _scoped(
    "test_plan",
    Column("test_plan_id", String(64), primary_key=True),
    Column("title", String(512), nullable=False),
    Column("active_published_revision_id", String(64)),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("updated_at", DATETIME(fsp=6), nullable=False),
    uniques=((("test_plan_id",), "uq_test_plan_project_pk"),),
)
test_plan_revision = _scoped(
    "test_plan_revision",
    Column("revision_id", String(64), primary_key=True),
    Column("test_plan_id", String(64), nullable=False),
    Column("version", Integer, nullable=False),
    Column("row_version", Integer, nullable=False, server_default="1"),
    Column("status", String(16), nullable=False),
    Column("origin", String(16), nullable=False),
    Column("adopted_from_revision_id", String(64)),
    Column("title", String(512), nullable=False),
    Column("objective", Text, nullable=False),
    Column("scope_items", JSON, nullable=False),
    Column("prerequisites", JSON, nullable=False),
    Column("risks", JSON, nullable=False),
    Column("content_digest", String(71), nullable=False),
    Column("validation_digest", String(71)),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("published_at", DATETIME(fsp=6)),
    Column("requirement_scope_id", String(128)),
    Column("requirement_scope_version", Integer),
    Column("requirement_scope_digest", String(71)),
    Column("requirement_ids", JSON, nullable=False),
    Column("approved_knowledge_revision_ids", JSON, nullable=False),
    Column("model_revision_id", String(128)),
    Column("agent_revision_id", String(128)),
    Column("skill_revision_ids", JSON, nullable=False),
    Column("author_actor_id", String(128)),
    Column("strict_review_required", Boolean, nullable=False, server_default="0"),
    Column("generated_content_digest", String(71)),
    Column("needs_review", Boolean, nullable=False, server_default="0"),
    Column("needs_review_reason", String(512)),
    uniques=(
        (("revision_id",), "uq_test_plan_revision_project_pk"),
        (("test_plan_id", "version"), "uq_test_plan_revision_version"),
    ),
    parents=((("test_plan_id",), "test_plan", ("test_plan_id",), "fk_test_plan_revision_plan"),),
)
test_plan.append_constraint(
    ForeignKeyConstraint(
        ["project_id", "active_published_revision_id"],
        ["test_plan_revision.project_id", "test_plan_revision.revision_id"],
        name="fk_test_plan_active_revision",
        use_alter=True,
    )
)
test_case = _scoped(
    "test_case",
    Column("case_id", String(128), primary_key=True),
    Column("revision_id", String(64), primary_key=True),
    Column("ordinal", Integer, nullable=False),
    Column("title", String(512), nullable=False),
    Column("objective", Text, nullable=False),
    Column("critical", Boolean, nullable=False),
    Column("covered_requirement_ids", JSON, nullable=False),
    uniques=(
        (("revision_id", "case_id"), "uq_test_case_revision_pk"),
        (("revision_id", "ordinal"), "uq_test_case_ordinal"),
    ),
    parents=((("revision_id",), "test_plan_revision", ("revision_id",), "fk_test_case_revision"),),
)
test_scenario = _scoped(
    "test_scenario",
    Column("scenario_id", String(128), primary_key=True),
    Column("revision_id", String(64), primary_key=True),
    Column("case_id", String(128), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("title", String(512), nullable=False),
    uniques=(
        (("revision_id", "scenario_id"), "uq_test_scenario_revision_pk"),
        (("case_id", "ordinal"), "uq_test_scenario_ordinal"),
    ),
    parents=(
        (("revision_id",), "test_plan_revision", ("revision_id",), "fk_test_scenario_revision"),
        (
            ("revision_id", "case_id"),
            "test_case",
            ("revision_id", "case_id"),
            "fk_test_scenario_case",
        ),
    ),
)
test_plan_step = _scoped(
    "test_plan_step",
    Column("step_id", String(128), primary_key=True),
    Column("revision_id", String(64), primary_key=True),
    Column("scenario_id", String(128), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("keyword", String(8), nullable=False),
    Column("text", Text, nullable=False),
    Column("expected_result", Text),
    Column("critical", Boolean, nullable=False),
    Column("citation_ids", JSON, nullable=False),
    Column("unknown_ids", JSON, nullable=False),
    uniques=(
        (("revision_id", "step_id"), "uq_test_plan_step_revision_pk"),
        (("scenario_id", "ordinal"), "uq_test_plan_step_ordinal"),
    ),
    parents=(
        (("revision_id",), "test_plan_revision", ("revision_id",), "fk_test_plan_step_revision"),
        (
            ("revision_id", "scenario_id"),
            "test_scenario",
            ("revision_id", "scenario_id"),
            "fk_test_plan_step_scenario",
        ),
    ),
)


def _child(name: str, identity: str, *columns):
    return _scoped(
        name,
        Column(identity, String(128), primary_key=True),
        Column("revision_id", String(64), primary_key=True),
        *columns,
        uniques=((("revision_id", identity), f"uq_{name}_revision_pk"),),
        parents=(
            (("revision_id",), "test_plan_revision", ("revision_id",), f"fk_{name}_revision"),
        ),
    )


test_plan_citation = _child(
    "test_plan_citation",
    "citation_id",
    Column("source_revision_id", String(128), nullable=False),
    Column("document_revision_id", String(128), nullable=False),
    Column("chunk_id", String(128), nullable=False),
    Column("content_digest", String(71), nullable=False),
    Column("claim_text", Text, nullable=False),
    Column("origin", String(32), nullable=False),
)
test_plan_assumption = _child(
    "test_plan_assumption",
    "assumption_id",
    Column("text", Text, nullable=False),
    Column("graph_edge_id", String(128)),
)
test_plan_unknown = _child("test_plan_unknown", "unknown_id", Column("text", Text, nullable=False))
test_plan_coverage_gap = _child(
    "test_plan_coverage_gap",
    "gap_id",
    Column("requirement_ref", String(512), nullable=False),
    Column("reason", Text, nullable=False),
    Column("severity", String(16), nullable=False),
)
test_plan_generation_job = _scoped(
    "test_plan_generation_job",
    Column("job_id", String(64), primary_key=True),
    Column("test_plan_id", String(64), nullable=False),
    Column("revision_id", String(64), nullable=False),
    Column("conversation_id", String(64), nullable=False),
    Column("turn_id", String(64), nullable=False),
    Column("input_snapshot_digest", String(71), nullable=False),
    Column("answer_evidence_snapshot_digest", String(71), nullable=False),
    Column("requirement_scope", JSON),
    Column("approved_knowledge_revision_ids", JSON, nullable=False),
    Column("model_alias", String(128), nullable=False),
    Column("model_revision_id", String(128)),
    Column("retry_idempotency_key", String(128)),
    Column("agent_revision_id", String(128), nullable=False),
    Column("skill_revision_ids", JSON, nullable=False),
    Column("objective", Text, nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("request_digest", String(71), nullable=False),
    Column("status", String(16), nullable=False),
    Column("lease_owner", String(128)),
    Column("lease_token", String(64)),
    Column("lease_expires_at", DATETIME(fsp=6)),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    Column("failure_code", String(64)),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("updated_at", DATETIME(fsp=6), nullable=False),
    uniques=(
        (("job_id",), "uq_test_plan_generation_job_project_pk"),
        (("request_digest",), "uq_test_plan_generation_request"),
        (("idempotency_key",), "uq_test_plan_generation_idempotency"),
    ),
    parents=(
        (
            ("conversation_id",),
            "conversation",
            ("conversation_id",),
            "fk_test_plan_generation_conversation",
        ),
        (("turn_id",), "chat_turn", ("turn_id",), "fk_test_plan_generation_turn"),
    ),
)
test_plan_review_decision = _scoped(
    "test_plan_review_decision",
    Column("decision_id", String(128), primary_key=True),
    Column("revision_id", String(64), nullable=False),
    Column("disposition", String(32), nullable=False),
    Column("reason", Text, nullable=False),
    Column("review_actor_id", String(128), nullable=False),
    Column("reviewed_content_digest", String(71), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("decision_digest", String(71), nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    uniques=(
        (("decision_id",), "uq_test_plan_review_decision_project_pk"),
        (("idempotency_key",), "uq_test_plan_review_decision_idempotency"),
    ),
    parents=(
        (
            ("revision_id",),
            "test_plan_revision",
            ("revision_id",),
            "fk_test_plan_review_decision_revision",
        ),
    ),
)
test_plan_source_impact = _scoped(
    "test_plan_source_impact",
    Column("impact_id", String(128), primary_key=True),
    Column("revision_id", String(64), nullable=False),
    Column("source_revision_id", String(128), nullable=False),
    Column("reason", String(512), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    uniques=(
        (("impact_id",), "uq_test_plan_source_impact_project_pk"),
        (("idempotency_key",), "uq_test_plan_source_impact_idempotency"),
    ),
    parents=(
        (
            ("revision_id",),
            "test_plan_revision",
            ("revision_id",),
            "fk_test_plan_source_impact_revision",
        ),
    ),
)
test_design_model_call = _scoped(
    "test_design_model_call",
    Column("call_id", String(64), primary_key=True),
    Column("job_id", String(64), nullable=False),
    Column("request_digest", String(71), nullable=False),
    Column("status", String(16), nullable=False),
    Column("result", JSON),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("updated_at", DATETIME(fsp=6), nullable=False),
    uniques=(
        (("call_id",), "uq_test_design_model_call_project_pk"),
        (("request_digest",), "uq_test_design_model_call_request"),
    ),
    parents=((("job_id",), "test_plan_generation_job", ("job_id",), "fk_test_design_call_job"),),
)

TEST_MANAGEMENT_TABLES = (
    test_plan,
    test_plan_revision,
    test_case,
    test_scenario,
    test_plan_step,
    test_plan_citation,
    test_plan_assumption,
    test_plan_unknown,
    test_plan_coverage_gap,
    test_plan_generation_job,
    test_design_model_call,
    test_plan_review_decision,
    test_plan_source_impact,
)


def _naive(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _requirement_scope_value(
    snapshot: RequirementScopeSnapshot | None,
) -> dict[str, object] | None:
    if snapshot is None:
        return None
    return {
        "scopeId": snapshot.scope_id,
        "version": snapshot.version,
        "contentDigest": snapshot.content_digest,
        "requirements": [
            {
                "requirementId": item.requirement_id,
                "sourceRevisionId": item.source_revision_id,
                "locator": item.locator,
            }
            for item in snapshot.requirements
        ],
    }


def _requirement_scope(raw: object) -> RequirementScopeSnapshot | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("requirements"), list):
        raise ValueError("stored Requirement Scope Snapshot is malformed")
    requirements = tuple(
        RequirementScopeItem(
            str(item["requirementId"]),
            str(item["sourceRevisionId"]),
            str(item["locator"]),
        )
        for item in raw["requirements"]
        if isinstance(item, dict)
    )
    return RequirementScopeSnapshot(
        str(raw["scopeId"]),
        int(raw["version"]),
        requirements,
        str(raw["contentDigest"]),
    )


class MysqlReconciledTestDesign:
    """Persist model-call intent/result so an unknown response is never blindly reissued."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        scope: ProjectScopeContext,
        delegate: TestDesignGenerator,
    ) -> None:
        self._sessions = sessions
        self._scope = require_project_scope(scope)
        self._delegate = delegate

    async def generate(self, context: TestDesignContext) -> TestPlanRevision:
        if context.scope != self._scope:
            raise ValueError("model-call ledger is bound to a different Project")
        from tap.modules.test_management.application.generation import (
            _revision_checkpoint,
            _revision_from_checkpoint,
        )

        call_id = hashlib.sha256(
            f"{self._scope.project_id}:{context.request.request_digest}".encode()
        ).hexdigest()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with self._sessions() as session, session.begin():
            existing = (
                (
                    await session.execute(
                        select(test_design_model_call)
                        .where(
                            *scope_predicates(test_design_model_call, self._scope),
                            test_design_model_call.c.call_id == call_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if existing["request_digest"] != context.request.request_digest:
                    raise RevisionConflict("model-call idempotency conflict")
                if existing["status"] == "SUCCEEDED":
                    return _revision_from_checkpoint(existing["result"])
                if existing["status"] in {"STARTED", "UNKNOWN"}:
                    await session.execute(
                        update(test_design_model_call)
                        .where(
                            *scope_predicates(test_design_model_call, self._scope),
                            test_design_model_call.c.call_id == call_id,
                        )
                        .values(status="UNKNOWN", updated_at=now)
                    )
                    raise GenerationResponseUnknown("provider response requires reconciliation")
                raise RuntimeError("previous model call failed before provider acceptance")
            await session.execute(
                insert(test_design_model_call).values(
                    **scope_values(self._scope),
                    call_id=call_id,
                    job_id=context.request.job_id,
                    request_digest=context.request.request_digest,
                    status="STARTED",
                    result=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        try:
            revision = await self._delegate.generate(context)
        except (ModelGatewayUnavailable, asyncio.CancelledError) as error:
            async with self._sessions() as session, session.begin():
                await session.execute(
                    update(test_design_model_call)
                    .where(
                        *scope_predicates(test_design_model_call, self._scope),
                        test_design_model_call.c.call_id == call_id,
                        test_design_model_call.c.status == "STARTED",
                    )
                    .values(
                        status="UNKNOWN", updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
                    )
                )
            if isinstance(error, asyncio.CancelledError):
                raise
            raise GenerationResponseUnknown("provider response requires reconciliation") from error
        except Exception:
            async with self._sessions() as session, session.begin():
                await session.execute(
                    update(test_design_model_call)
                    .where(
                        *scope_predicates(test_design_model_call, self._scope),
                        test_design_model_call.c.call_id == call_id,
                        test_design_model_call.c.status == "STARTED",
                    )
                    .values(
                        status="FAILED", updated_at=datetime.now(timezone.utc).replace(tzinfo=None)
                    )
                )
            raise
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(test_design_model_call)
                .where(
                    *scope_predicates(test_design_model_call, self._scope),
                    test_design_model_call.c.call_id == call_id,
                    test_design_model_call.c.status == "STARTED",
                )
                .values(
                    status="SUCCEEDED",
                    result=_revision_checkpoint(revision),
                    updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            )
            if result.rowcount != 1:
                raise GenerationResponseUnknown("model result settlement lost ownership")
        return revision


class MysqlTestPlanRepository:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, scope: ProjectScopeContext
    ) -> None:
        self._sessions = sessions
        self.scope = require_project_scope(scope)

    def graph_checkpointer(self, claim: ClaimedTestDesignJob):
        from tap.modules.ai.adapters.mysql_checkpointer import GraphFence, MysqlGraphCheckpointer

        return MysqlGraphCheckpointer(
            self._sessions,
            scope=self.scope,
            defer_completion=True,
            budget={"maxModelCalls": 1, "maxSeconds": 60, "maxCostMicros": 0},
            fence=GraphFence(
                table=test_plan_generation_job,
                identity_column=test_plan_generation_job.c.job_id,
                identity=claim.job.request.job_id,
                token_column=test_plan_generation_job.c.lease_token,
                token=claim.lease_token,
                lease_until_column=test_plan_generation_job.c.lease_expires_at,
                status_column=test_plan_generation_job.c.status,
                running_status=GenerationJobStatus.RUNNING.value,
                lease_owner=claim.job.lease_owner or "test-design-worker",
                attempt_count_column=test_plan_generation_job.c.attempt_count,
            ),
        )

    async def _generation_stream_event(
        self,
        session: AsyncSession,
        request: TestPlanGenerationRequest,
        *,
        event_type: str,
        payload: dict[str, object],
        now: datetime,
    ) -> None:
        await session.execute(
            select(conversation.c.conversation_id)
            .where(
                *scope_predicates(conversation, self.scope),
                conversation.c.conversation_id == request.conversation_id,
            )
            .with_for_update()
        )
        latest = await session.scalar(
            select(func.max(chat_turn.c.last_sequence)).where(
                *scope_predicates(chat_turn, self.scope),
                chat_turn.c.chat_id == request.conversation_id,
            )
        )
        sequence = (0 if latest is None else int(latest)) + 1
        await session.execute(
            insert(chat_event).values(
                **scope_values(self.scope),
                event_id=uuid4().hex,
                turn_id=request.turn_id,
                sequence=sequence,
                stream_sequence=sequence,
                event_type=event_type,
                payload=payload,
                schema_version=1,
                occurred_at=_naive(now),
            )
        )
        await session.execute(
            update(chat_turn)
            .where(
                *scope_predicates(chat_turn, self.scope),
                chat_turn.c.turn_id == request.turn_id,
            )
            .values(last_sequence=sequence)
        )
        await session.execute(
            update(conversation)
            .where(
                *scope_predicates(conversation, self.scope),
                conversation.c.conversation_id == request.conversation_id,
            )
            .values(updated_at=_naive(now))
        )

    async def create_draft(
        self,
        scope: ProjectScopeContext,
        revision: TestPlanRevision,
        *,
        now: datetime,
    ) -> TestPlanRevision:
        scope = require_project_scope(scope)
        if scope != self.scope:
            raise ValueError("repository is bound to a different Project")
        if revision.status is not RevisionStatus.DRAFT:
            raise RevisionImmutable("only drafts can be created")
        async with self._sessions() as session, session.begin():
            existing = await self._load_revision(session, revision.revision_id)
            if existing is not None:
                if (
                    existing.test_plan_id != revision.test_plan_id
                    or existing.version != revision.version
                    or existing.content_digest != revision.content_digest
                ):
                    raise RevisionConflict("immutable revision identity conflict")
                return existing
            plan = await session.scalar(
                select(test_plan.c.test_plan_id).where(
                    *scope_predicates(test_plan, self.scope),
                    test_plan.c.test_plan_id == revision.test_plan_id,
                )
            )
            values = scope_values(self.scope)
            if plan is None:
                await session.execute(
                    insert(test_plan).values(
                        **values,
                        test_plan_id=revision.test_plan_id,
                        title=revision.title,
                        active_published_revision_id=None,
                        created_at=_naive(now),
                        updated_at=_naive(now),
                    )
                )
            await self._insert_revision(session, revision, now=_naive(now))
        return replace(revision, created_at=_naive(now))

    async def get_revision(
        self, scope: ProjectScopeContext, test_plan_id: str, revision_id: str
    ) -> TestPlanRevision:
        scope = require_project_scope(scope)
        if scope != self.scope:
            raise ValueError("repository is bound to a different Project")
        async with self._sessions() as session:
            revision = await self._load_revision(session, revision_id)
        if revision is None or revision.test_plan_id != test_plan_id:
            raise LookupError("test plan revision not found")
        return revision

    async def replace_draft(
        self,
        scope: ProjectScopeContext,
        revision: TestPlanRevision,
        expected_version: int,
        *,
        now: datetime,
    ) -> TestPlanRevision:
        scope = self._matching_scope(scope)
        if revision.status is not RevisionStatus.DRAFT:
            raise RevisionImmutable("only Draft revisions can be edited")
        async with self._sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        select(test_plan_revision)
                        .where(
                            *scope_predicates(test_plan_revision, scope),
                            test_plan_revision.c.revision_id == revision.revision_id,
                            test_plan_revision.c.test_plan_id == revision.test_plan_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise LookupError("test plan revision not found")
            if row["status"] != RevisionStatus.DRAFT.value:
                raise RevisionImmutable("published and superseded revisions are immutable")
            if row["row_version"] != expected_version:
                raise RevisionConflict("revision version changed")
            if revision.version != row["version"] or revision.origin.value != row["origin"]:
                raise RevisionConflict("revision identity cannot be edited")
            for table in (
                test_plan_step,
                test_scenario,
                test_case,
                test_plan_citation,
                test_plan_assumption,
                test_plan_unknown,
                test_plan_coverage_gap,
            ):
                await session.execute(
                    delete(table).where(
                        *scope_predicates(table, scope),
                        table.c.revision_id == revision.revision_id,
                    )
                )
            result = await session.execute(
                update(test_plan_revision)
                .where(
                    *scope_predicates(test_plan_revision, scope),
                    test_plan_revision.c.revision_id == revision.revision_id,
                    test_plan_revision.c.row_version == expected_version,
                )
                .values(
                    title=revision.title,
                    objective=revision.objective,
                    scope_items=list(revision.scope_items),
                    prerequisites=list(revision.prerequisites),
                    risks=list(revision.risks),
                    content_digest=revision.content_digest,
                    row_version=expected_version + 1,
                )
            )
            if result.rowcount != 1:
                raise RevisionConflict("revision version changed")
            await self._insert_revision_children(session, revision)
            await session.execute(
                update(test_plan)
                .where(
                    *scope_predicates(test_plan, scope),
                    test_plan.c.test_plan_id == revision.test_plan_id,
                )
                .values(title=revision.title, updated_at=_naive(now))
            )
            updated = await self._load_revision(session, revision.revision_id)
            assert updated is not None
            return updated

    async def publish_revision(
        self,
        scope: ProjectScopeContext,
        revision_id: str,
        expected_version: int,
        validation_digest: str,
    ) -> TestPlanRevision:
        scope = require_project_scope(scope)
        if scope != self.scope:
            raise ValueError("repository is bound to a different Project")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with self._sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        select(test_plan_revision)
                        .where(
                            *scope_predicates(test_plan_revision, scope),
                            test_plan_revision.c.revision_id == revision_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise LookupError("test plan revision not found")
            if row["status"] not in {RevisionStatus.DRAFT.value, RevisionStatus.VALIDATING.value}:
                raise RevisionImmutable("published and superseded revisions are immutable")
            if row["row_version"] != expected_version:
                raise RevisionConflict("revision version changed")
            plan_id = row["test_plan_id"]
            previous = await session.scalar(
                select(test_plan.c.active_published_revision_id)
                .where(
                    *scope_predicates(test_plan, scope),
                    test_plan.c.test_plan_id == plan_id,
                )
                .with_for_update()
            )
            if previous is not None and previous != revision_id:
                await session.execute(
                    update(test_plan_revision)
                    .where(
                        *scope_predicates(test_plan_revision, scope),
                        test_plan_revision.c.revision_id == previous,
                        test_plan_revision.c.status == RevisionStatus.PUBLISHED.value,
                    )
                    .values(
                        status=RevisionStatus.SUPERSEDED.value,
                        row_version=test_plan_revision.c.row_version + 1,
                    )
                )
            await session.execute(
                update(test_plan_revision)
                .where(
                    *scope_predicates(test_plan_revision, scope),
                    test_plan_revision.c.revision_id == revision_id,
                    test_plan_revision.c.row_version == expected_version,
                )
                .values(
                    status=RevisionStatus.PUBLISHED.value,
                    validation_digest=validation_digest,
                    published_at=now,
                    row_version=expected_version + 1,
                )
            )
            await session.execute(
                update(test_plan)
                .where(
                    *scope_predicates(test_plan, scope),
                    test_plan.c.test_plan_id == plan_id,
                )
                .values(active_published_revision_id=revision_id, updated_at=now)
            )
            envelope = ProjectEventEnvelope(
                event_id=scoped_outbox_id(scope, kind="test-plan-published", identity=revision_id),
                event_type="test-plan.revision.published",
                schema_version=1,
                occurred_at=now.replace(tzinfo=timezone.utc),
                scope_kind="PROJECT",
                enterprise_id=scope.enterprise_id,
                project_id=scope.project_id,
                actor_id=scope.actor_id,
                identity_mode=scope.identity_mode.value,
                aggregate_type="TestPlanRevision",
                aggregate_id=revision_id,
                aggregate_version=row["version"],
                correlation_id=plan_id,
                causation_id=None,
                idempotency_key=f"test-plan-publish:{revision_id}",
                payload={
                    "revisionId": revision_id,
                    "contentDigest": row["content_digest"],
                    "validationDigest": validation_digest,
                },
            )
            await write_project_event(session, scope=scope, envelope=envelope)
            await MysqlProjectAudit(await session.connection(), scope=scope).append(
                scope,
                AuditAction.TEST_PLAN_REVISION_PUBLISHED,
                AuditResource.TEST_PLAN_REVISION,
                AuditOutcome.COMPLETED,
                SafeAuditMetadata(
                    {"content_digest": str(row["content_digest"]).removeprefix("sha256:")}
                ),
                correlation_id=plan_id,
                idempotency_key=f"audit:test-plan-publish:{revision_id}",
                resource_id=revision_id,
            )
            published = await self._load_revision(session, revision_id)
            assert published is not None
            return published

    async def record_review(
        self,
        scope: ProjectScopeContext,
        test_plan_id: str,
        revision_id: str,
        *,
        disposition: ReviewDisposition,
        reason: str,
        expected_version: int,
        idempotency_key: str,
        now: datetime,
    ) -> TestPlanRevision:
        scope = self._matching_scope(scope)
        if not reason.strip() or len(reason) > 4096:
            raise ValueError("review reason must be bounded nonblank text")
        material = json.dumps(
            {
                "testPlanId": test_plan_id,
                "revisionId": revision_id,
                "disposition": disposition.value,
                "reason": reason,
                "expectedVersion": expected_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        decision_digest = "sha256:" + hashlib.sha256(material.encode()).hexdigest()
        decision_id = (
            "tprd_"
            + hashlib.sha256(f"{scope.project_id}:{idempotency_key}".encode()).hexdigest()[:32]
        )
        instant = _naive(now)
        async with self._sessions() as session, session.begin():
            replay = (
                (
                    await session.execute(
                        select(test_plan_review_decision).where(
                            *scope_predicates(test_plan_review_decision, scope),
                            test_plan_review_decision.c.idempotency_key == idempotency_key,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if replay is not None:
                if replay["decision_digest"] != decision_digest:
                    raise RevisionConflict("review idempotency conflict")
                replayed = await self._load_revision(session, revision_id)
                if replayed is None:
                    raise RevisionConflict("review result no longer exists")
                return replayed
            row = (
                (
                    await session.execute(
                        select(test_plan_revision)
                        .where(
                            *scope_predicates(test_plan_revision, scope),
                            test_plan_revision.c.test_plan_id == test_plan_id,
                            test_plan_revision.c.revision_id == revision_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise LookupError("test plan revision not found")
            if row["status"] != RevisionStatus.DRAFT.value:
                raise RevisionImmutable("only Draft revisions can be reviewed")
            if row["row_version"] != expected_version:
                raise RevisionConflict("revision version changed")
            unchanged = row["content_digest"] == row["generated_content_digest"]
            if disposition is ReviewDisposition.ACCEPTED_UNCHANGED and not unchanged:
                raise ValueError("edited generated content requires modified acceptance")
            if disposition is ReviewDisposition.ACCEPTED_MODIFIED and unchanged:
                raise ValueError("unchanged generated content requires unchanged acceptance")
            await session.execute(
                insert(test_plan_review_decision).values(
                    **scope_values(scope),
                    decision_id=decision_id,
                    revision_id=revision_id,
                    disposition=disposition.value,
                    reason=reason,
                    review_actor_id=scope.actor_id,
                    reviewed_content_digest=row["content_digest"],
                    idempotency_key=idempotency_key,
                    decision_digest=decision_digest,
                    created_at=instant,
                )
            )
            await session.execute(
                update(test_plan_revision)
                .where(
                    *scope_predicates(test_plan_revision, scope),
                    test_plan_revision.c.revision_id == revision_id,
                    test_plan_revision.c.row_version == expected_version,
                )
                .values(
                    row_version=expected_version + 1,
                    needs_review=False,
                    needs_review_reason=None,
                )
            )
            reviewed = await self._load_revision(session, revision_id)
            assert reviewed is not None
            return reviewed

    async def review_summary(self, scope: ProjectScopeContext) -> TestPlanReviewSummary:
        scope = self._matching_scope(scope)
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(test_plan_review_decision)
                        .where(*scope_predicates(test_plan_review_decision, scope))
                        .order_by(
                            test_plan_review_decision.c.created_at,
                            test_plan_review_decision.c.decision_id,
                        )
                    )
                )
                .mappings()
                .all()
            )
        current = {row["revision_id"]: ReviewDisposition(row["disposition"]) for row in rows}
        reviewed = [value for value in current.values() if value is not ReviewDisposition.PENDING]
        return TestPlanReviewSummary(
            len(reviewed),
            reviewed.count(ReviewDisposition.ACCEPTED_UNCHANGED),
            reviewed.count(ReviewDisposition.ACCEPTED_MODIFIED),
            reviewed.count(ReviewDisposition.REJECTED),
        )

    async def mark_source_changed(
        self,
        scope: ProjectScopeContext,
        source_revision_id: str,
        *,
        reason: str,
        idempotency_key: str,
        now: datetime,
    ) -> tuple[str, ...]:
        scope = self._matching_scope(scope)
        if not reason.strip() or len(reason) > 512:
            raise ValueError("source impact reason must be bounded nonblank text")
        instant = _naive(now)
        async with self._sessions() as session, session.begin():
            revision_ids = tuple(
                (
                    await session.scalars(
                        select(test_plan_citation.c.revision_id)
                        .where(
                            *scope_predicates(test_plan_citation, scope),
                            test_plan_citation.c.source_revision_id == source_revision_id,
                        )
                        .distinct()
                        .order_by(test_plan_citation.c.revision_id)
                    )
                ).all()
            )
            for revision_id in revision_ids:
                impact_key = hashlib.sha256(
                    f"{scope.project_id}:{idempotency_key}:{revision_id}".encode()
                ).hexdigest()
                impact_id = "tpsi_" + impact_key[:32]
                existing = await session.scalar(
                    select(test_plan_source_impact.c.impact_id).where(
                        *scope_predicates(test_plan_source_impact, scope),
                        test_plan_source_impact.c.impact_id == impact_id,
                    )
                )
                if existing is None:
                    await session.execute(
                        insert(test_plan_source_impact).values(
                            **scope_values(scope),
                            impact_id=impact_id,
                            revision_id=revision_id,
                            source_revision_id=source_revision_id,
                            reason=reason,
                            idempotency_key=impact_key,
                            created_at=instant,
                        )
                    )
                    await session.execute(
                        update(test_plan_revision)
                        .where(
                            *scope_predicates(test_plan_revision, scope),
                            test_plan_revision.c.revision_id == revision_id,
                        )
                        .values(
                            needs_review=True,
                            needs_review_reason=reason,
                            row_version=test_plan_revision.c.row_version + 1,
                        )
                    )
        return revision_ids

    async def request_generation(
        self,
        scope: ProjectScopeContext,
        request: TestPlanGenerationRequest,
        *,
        now: datetime,
    ) -> TestPlanGenerationJob:
        scope = self._matching_scope(scope)
        if request.project_id != self.scope.project_id:
            raise ValueError("generation request is outside Project scope")
        async with self._sessions() as session, session.begin():
            existing = (
                (
                    await session.execute(
                        select(test_plan_generation_job)
                        .where(
                            *scope_predicates(test_plan_generation_job, self.scope),
                            test_plan_generation_job.c.idempotency_key == request.idempotency_key,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if existing["request_digest"] != request.request_digest:
                    raise RevisionConflict("generation idempotency conflict")
                return self._job(existing)
            snapshot = (
                (
                    await session.execute(
                        select(
                            chat_turn.c.turn_id,
                            chat_turn.c.state,
                            turn_input_snapshot.c.snapshot_digest.label("input_digest"),
                            turn_input_snapshot.c.snapshot.label("input_snapshot"),
                            turn_answer_evidence_snapshot.c.snapshot_digest.label("answer_digest"),
                            turn_answer_evidence_snapshot.c.snapshot.label("answer_snapshot"),
                            turn_answer_evidence_snapshot.c.input_snapshot_digest.label(
                                "answer_input_digest"
                            ),
                        )
                        .select_from(chat_turn)
                        .join(
                            conversation,
                            (conversation.c.project_id == chat_turn.c.project_id)
                            & (conversation.c.conversation_id == chat_turn.c.chat_id),
                        )
                        .join(
                            turn_input_snapshot,
                            (turn_input_snapshot.c.project_id == chat_turn.c.project_id)
                            & (turn_input_snapshot.c.turn_id == chat_turn.c.turn_id),
                        )
                        .join(
                            turn_answer_evidence_snapshot,
                            (turn_answer_evidence_snapshot.c.project_id == chat_turn.c.project_id)
                            & (turn_answer_evidence_snapshot.c.turn_id == chat_turn.c.turn_id),
                        )
                        .where(
                            *scope_predicates(chat_turn, self.scope),
                            *scope_predicates(conversation, self.scope),
                            *scope_predicates(turn_input_snapshot, self.scope),
                            *scope_predicates(turn_answer_evidence_snapshot, self.scope),
                            chat_turn.c.turn_id == request.turn_id,
                            chat_turn.c.chat_id == request.conversation_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if snapshot is None or snapshot["state"] not in {"completed", "abstained"}:
                raise ValueError("completed Turn snapshot binding was not found")
            if (
                snapshot["input_digest"] != request.input_snapshot_digest
                or snapshot["answer_digest"] != request.answer_evidence_snapshot_digest
                or snapshot["answer_input_digest"] != request.input_snapshot_digest
            ):
                raise ValueError("generation snapshot digest binding is invalid")
            if (
                request.requirement_scope is None
                or not request.approved_knowledge_revision_ids
                or request.model_revision_id is None
            ):
                raise ValueError("generation governance versions are incomplete")
            approved_versions = set(request.approved_knowledge_revision_ids)
            if any(
                item.source_revision_id not in approved_versions
                for item in request.requirement_scope.requirements
            ):
                raise ValueError("Requirement Scope uses an unapproved knowledge version")
            answer_snapshot = snapshot["answer_snapshot"]
            evidence: list[Any] = (
                answer_snapshot.get("authorizedEvidence", answer_snapshot.get("citations", []))
                if isinstance(answer_snapshot, dict)
                else []
            ) or []
            evidence_versions = {
                item.get("sourceRevisionId")
                for item in evidence
                if isinstance(item, dict) and isinstance(item.get("sourceRevisionId"), str)
            }
            if evidence_versions and not evidence_versions <= approved_versions:
                raise ValueError("Answer Evidence uses an unapproved knowledge version")
            input_snapshot = snapshot["input_snapshot"]
            snapshot_skill_ids = (
                input_snapshot.get("skill_revision_ids")
                if isinstance(input_snapshot, dict)
                else None
            )
            if not isinstance(input_snapshot, dict) or (
                input_snapshot.get("model_alias") != request.model_alias
                or input_snapshot.get("agent_revision_id") != request.agent_revision_id
                or not isinstance(snapshot_skill_ids, list)
                or tuple(snapshot_skill_ids) != request.skill_revision_ids
            ):
                raise ValueError("generation governance binding is invalid")
            enabled_agent = await session.scalar(
                select(func.count())
                .select_from(ai_agent_revision)
                .where(
                    *scope_predicates(ai_agent_revision, self.scope),
                    ai_agent_revision.c.revision_id == request.agent_revision_id,
                    ai_agent_revision.c.status == AssetRevisionStatus.ENABLED.value,
                )
            )
            enabled_skills = await session.scalar(
                select(func.count())
                .select_from(skill_revision)
                .where(
                    *scope_predicates(skill_revision, self.scope),
                    skill_revision.c.revision_id.in_(request.skill_revision_ids),
                    skill_revision.c.status == AssetRevisionStatus.ENABLED.value,
                )
            )
            if int(enabled_agent or 0) != 1 or int(enabled_skills or 0) != len(
                request.skill_revision_ids
            ):
                raise ValueError("generation requires enabled Agent and Skill revisions")
            values = scope_values(self.scope)
            await session.execute(
                insert(test_plan_generation_job).values(
                    **values,
                    job_id=request.job_id,
                    test_plan_id=request.test_plan_id,
                    revision_id=request.revision_id,
                    conversation_id=request.conversation_id,
                    turn_id=request.turn_id,
                    input_snapshot_digest=request.input_snapshot_digest,
                    answer_evidence_snapshot_digest=request.answer_evidence_snapshot_digest,
                    requirement_scope=_requirement_scope_value(request.requirement_scope),
                    approved_knowledge_revision_ids=list(request.approved_knowledge_revision_ids),
                    model_alias=request.model_alias,
                    model_revision_id=request.model_revision_id,
                    agent_revision_id=request.agent_revision_id,
                    skill_revision_ids=list(request.skill_revision_ids),
                    objective=request.objective,
                    idempotency_key=request.idempotency_key,
                    request_digest=request.request_digest,
                    status=GenerationJobStatus.PENDING.value,
                    attempt_count=0,
                    created_at=_naive(now),
                    updated_at=_naive(now),
                )
            )
            row = (
                (
                    await session.execute(
                        select(test_plan_generation_job).where(
                            *scope_predicates(test_plan_generation_job, self.scope),
                            test_plan_generation_job.c.job_id == request.job_id,
                        )
                    )
                )
                .mappings()
                .one()
            )
            job = self._job(row)
            event_key = f"test-plan-generation:{request.job_id}"
            occurred_at = _naive(now).replace(tzinfo=timezone.utc)
            await write_project_event(
                session,
                scope=self.scope,
                envelope=ProjectEventEnvelope(
                    event_id=scoped_outbox_id(
                        self.scope,
                        kind="test-plan-generation-requested",
                        identity=request.revision_id,
                    ),
                    event_type="test-plan.generation.requested",
                    schema_version=1,
                    occurred_at=occurred_at,
                    scope_kind="PROJECT",
                    enterprise_id=self.scope.enterprise_id,
                    project_id=self.scope.project_id,
                    actor_id=self.scope.actor_id,
                    identity_mode=self.scope.identity_mode.value,
                    aggregate_type="TestPlanRevision",
                    aggregate_id=request.revision_id,
                    aggregate_version=1,
                    correlation_id=request.conversation_id,
                    causation_id=request.turn_id,
                    idempotency_key=event_key,
                    payload={
                        "revisionId": request.revision_id,
                        "inputSnapshotDigest": request.input_snapshot_digest,
                        "answerEvidenceSnapshotDigest": (request.answer_evidence_snapshot_digest),
                        "requestDigest": request.request_digest,
                    },
                ),
            )
            await MysqlProjectAudit(await session.connection(), scope=self.scope).append(
                self.scope,
                AuditAction.TEST_PLAN_GENERATION_REQUESTED,
                AuditResource.TEST_PLAN_REVISION,
                AuditOutcome.COMPLETED,
                SafeAuditMetadata(
                    {"content_digest": request.request_digest.removeprefix("sha256:")}
                ),
                correlation_id=request.conversation_id,
                idempotency_key=f"audit:{event_key}",
                resource_id=request.revision_id,
            )
            return job

    async def list_revisions(
        self, scope: ProjectScopeContext, *, limit: int = 50
    ) -> tuple[TestPlanRevision, ...]:
        scope = self._matching_scope(scope)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("test plan list limit must be between 1 and 50")
        async with self._sessions() as session:
            revision_ids = tuple(
                (
                    await session.scalars(
                        select(test_plan_revision.c.revision_id)
                        .where(*scope_predicates(test_plan_revision, scope))
                        .order_by(test_plan_revision.c.created_at.desc())
                        .limit(limit)
                    )
                ).all()
            )
            revisions = []
            for revision_id in revision_ids:
                revision = await self._load_revision(session, revision_id)
                assert revision is not None
                revisions.append(revision)
        return tuple(revisions)

    async def get_generation_job(
        self, scope: ProjectScopeContext, job_id: str
    ) -> TestPlanGenerationJob:
        scope = self._matching_scope(scope)
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(test_plan_generation_job).where(
                            *scope_predicates(test_plan_generation_job, scope),
                            test_plan_generation_job.c.job_id == job_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError("test design generation job not found")
        return self._job(row)

    async def cancel_generation(
        self, scope: ProjectScopeContext, job_id: str, *, now: datetime
    ) -> TestPlanGenerationJob:
        scope = self._matching_scope(scope)
        instant = _naive(now)
        async with self._sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        select(test_plan_generation_job)
                        .where(
                            *scope_predicates(test_plan_generation_job, scope),
                            test_plan_generation_job.c.job_id == job_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise LookupError("test design generation job not found")
            if row["status"] in {
                GenerationJobStatus.DRAFT_READY.value,
                GenerationJobStatus.FAILED.value,
                GenerationJobStatus.CANCELED.value,
            }:
                return self._job(row)
            await session.execute(
                update(test_plan_generation_job)
                .where(
                    *scope_predicates(test_plan_generation_job, scope),
                    test_plan_generation_job.c.job_id == job_id,
                )
                .values(
                    status=GenerationJobStatus.CANCELED.value,
                    failure_code="canceled-by-user",
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    updated_at=instant,
                )
            )
            await self._generation_stream_event(
                session,
                self._job(row).request,
                event_type="test-plan.generation.canceled",
                payload={"jobId": job_id, "reason": "canceled-by-user"},
                now=instant,
            )
            from tap.modules.ai.adapters.mysql_checkpointer import (
                graph_run,
                graph_settlement,
            )

            graph = (
                (
                    await session.execute(
                        select(graph_run)
                        .where(
                            *scope_predicates(graph_run, scope),
                            graph_run.c.run_id == job_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if graph is not None and graph["status"] not in {
                "SUCCEEDED",
                "FAILED",
                "CANCELLED",
            }:
                if graph["current_checkpoint_id"]:
                    await session.execute(
                        insert(graph_settlement).values(
                            **scope_values(scope),
                            run_id=job_id,
                            checkpoint_id=graph["current_checkpoint_id"],
                            outcome="CANCELLED",
                            created_at=instant,
                        )
                    )
                await session.execute(
                    update(graph_run)
                    .where(
                        *scope_predicates(graph_run, scope),
                        graph_run.c.run_id == job_id,
                    )
                    .values(
                        status="CANCELLED",
                        waiting_reason=None,
                        lease_owner=None,
                        lease_token=None,
                        lease_until=None,
                        updated_at=instant,
                    )
                )
            canceled = dict(row)
            canceled.update(
                status=GenerationJobStatus.CANCELED.value,
                failure_code="canceled-by-user",
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
                updated_at=instant,
            )
            return self._job(canceled)

    async def retry_generation(
        self,
        scope: ProjectScopeContext,
        job_id: str,
        *,
        idempotency_key: str,
        now: datetime,
    ) -> TestPlanGenerationJob:
        scope = self._matching_scope(scope)
        instant = _naive(now)
        async with self._sessions() as session, session.begin():
            row = (
                (
                    await session.execute(
                        select(test_plan_generation_job)
                        .where(
                            *scope_predicates(test_plan_generation_job, scope),
                            test_plan_generation_job.c.job_id == job_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise LookupError("test design generation job not found")
            if row["retry_idempotency_key"] == idempotency_key:
                return self._job(row)
            if row["status"] != GenerationJobStatus.FAILED.value:
                raise ValueError("only failed generation jobs can be retried")
            await session.execute(
                update(test_plan_generation_job)
                .where(
                    *scope_predicates(test_plan_generation_job, scope),
                    test_plan_generation_job.c.job_id == job_id,
                    test_plan_generation_job.c.status == GenerationJobStatus.FAILED.value,
                )
                .values(
                    status=GenerationJobStatus.PENDING.value,
                    failure_code=None,
                    retry_idempotency_key=idempotency_key,
                    updated_at=instant,
                )
            )
            retried = dict(row)
            retried.update(
                status=GenerationJobStatus.PENDING.value,
                failure_code=None,
                retry_idempotency_key=idempotency_key,
                updated_at=instant,
            )
            return self._job(retried)

    async def is_authorized(self, scope: ProjectScopeContext, citation: TestPlanCitation) -> bool:
        scope = self._matching_scope(scope)
        async with self._sessions() as session:
            row = await session.scalar(
                text(
                    "SELECT 1 FROM knowledge_citation_snapshot "
                    "WHERE enterprise_id=:enterprise_id AND project_id=:project_id "
                    "AND revision_id=:source_revision_id AND revision_id=:revision_id "
                    "AND chunk_id=:chunk_id "
                    "AND chunk_content_hash=:content_digest LIMIT 1"
                ),
                {
                    "enterprise_id": scope.enterprise_id,
                    "project_id": scope.project_id,
                    "source_revision_id": citation.source_revision_id,
                    "revision_id": citation.document_revision_id,
                    "chunk_id": citation.chunk_id,
                    "content_digest": citation.content_digest,
                },
            )
        return row == 1

    async def is_requirement_scope_current(
        self, scope: ProjectScopeContext, revision: TestPlanRevision
    ) -> bool:
        self._matching_scope(scope)
        if revision.requirement_scope_id is None:
            return True
        return (
            revision.requirement_scope_version is not None
            and revision.requirement_scope_digest is not None
            and bool(revision.requirement_ids)
            and not revision.needs_review
        )

    async def are_knowledge_versions_current(
        self, scope: ProjectScopeContext, revision: TestPlanRevision
    ) -> bool:
        scope = self._matching_scope(scope)
        approved = set(revision.approved_knowledge_revision_ids)
        if approved and any(
            citation.source_revision_id not in approved for citation in revision.citations
        ):
            return False
        if revision.needs_review:
            return False
        if revision.agent_revision_id is None and not revision.skill_revision_ids:
            return True
        async with self._sessions() as session:
            agent_enabled = (
                revision.agent_revision_id is None
                or await session.scalar(
                    select(func.count())
                    .select_from(ai_agent_revision)
                    .where(
                        *scope_predicates(ai_agent_revision, scope),
                        ai_agent_revision.c.revision_id == revision.agent_revision_id,
                        ai_agent_revision.c.status == AssetRevisionStatus.ENABLED.value,
                    )
                )
                == 1
            )
            skill_count = await session.scalar(
                select(func.count())
                .select_from(skill_revision)
                .where(
                    *scope_predicates(skill_revision, scope),
                    skill_revision.c.revision_id.in_(revision.skill_revision_ids),
                    skill_revision.c.status == AssetRevisionStatus.ENABLED.value,
                )
            )
        return agent_enabled and int(skill_count or 0) == len(revision.skill_revision_ids)

    async def claim_generation_jobs(
        self,
        scope: ProjectScopeContext,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[ClaimedTestDesignJob, ...]:
        scope = self._matching_scope(scope)
        if not worker_id or not timedelta(0) < lease_duration <= timedelta(minutes=15):
            raise ValueError("test design worker lease is invalid")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("test design claim limit must be between 1 and 50")
        claims: list[ClaimedTestDesignJob] = []
        instant = _naive(now)
        async with self._sessions() as session, session.begin():
            rows = (
                (
                    await session.execute(
                        select(test_plan_generation_job)
                        .where(
                            *scope_predicates(test_plan_generation_job, scope),
                            or_(
                                test_plan_generation_job.c.status
                                == GenerationJobStatus.PENDING.value,
                                (
                                    test_plan_generation_job.c.status
                                    == GenerationJobStatus.RUNNING.value
                                )
                                & (test_plan_generation_job.c.lease_expires_at < instant),
                            ),
                        )
                        .order_by(test_plan_generation_job.c.created_at)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                token = uuid4().hex
                await session.execute(
                    update(test_plan_generation_job)
                    .where(
                        *scope_predicates(test_plan_generation_job, scope),
                        test_plan_generation_job.c.job_id == row["job_id"],
                    )
                    .values(
                        status=GenerationJobStatus.RUNNING.value,
                        lease_owner=worker_id,
                        lease_token=token,
                        lease_expires_at=instant + lease_duration,
                        attempt_count=test_plan_generation_job.c.attempt_count + 1,
                        updated_at=instant,
                    )
                )
                claimed = dict(row)
                claimed.update(
                    status=GenerationJobStatus.RUNNING.value,
                    lease_owner=worker_id,
                    lease_token=token,
                    lease_expires_at=instant + lease_duration,
                    attempt_count=row["attempt_count"] + 1,
                    updated_at=instant,
                )
                claims.append(ClaimedTestDesignJob(self._job(claimed), token))
        return tuple(claims)

    async def generation_context(
        self, scope: ProjectScopeContext, claim: ClaimedTestDesignJob
    ) -> TestDesignContext:
        scope = self._matching_scope(scope)
        request = claim.job.request
        async with self._sessions() as session:
            row = (
                (
                    await session.execute(
                        select(
                            test_plan_generation_job.c.status,
                            test_plan_generation_job.c.lease_token,
                            test_plan_generation_job.c.lease_expires_at,
                            turn_input_snapshot.c.snapshot.label("input_snapshot"),
                            turn_answer_evidence_snapshot.c.snapshot.label("answer_snapshot"),
                        )
                        .select_from(test_plan_generation_job)
                        .join(
                            turn_input_snapshot,
                            (
                                turn_input_snapshot.c.project_id
                                == test_plan_generation_job.c.project_id
                            )
                            & (turn_input_snapshot.c.turn_id == test_plan_generation_job.c.turn_id),
                        )
                        .join(
                            turn_answer_evidence_snapshot,
                            (
                                turn_answer_evidence_snapshot.c.project_id
                                == test_plan_generation_job.c.project_id
                            )
                            & (
                                turn_answer_evidence_snapshot.c.turn_id
                                == test_plan_generation_job.c.turn_id
                            ),
                        )
                        .where(
                            *scope_predicates(test_plan_generation_job, scope),
                            test_plan_generation_job.c.job_id == request.job_id,
                            test_plan_generation_job.c.lease_expires_at >= func.utc_timestamp(6),
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if (
            row is None
            or row["status"] != GenerationJobStatus.RUNNING.value
            or row["lease_token"] != claim.lease_token
            or row["lease_expires_at"] is None
        ):
            raise RevisionConflict("test design worker lease was lost")
        answer_snapshot = dict(row["answer_snapshot"])
        citation_ids = tuple(
            item.get("citation_snapshot_id")
            for item in answer_snapshot.get("citations", [])
            if isinstance(item, dict) and isinstance(item.get("citation_snapshot_id"), str)
        )
        authorized_evidence: list[dict[str, object]] = []
        if citation_ids:
            async with self._sessions() as session:
                for citation_id in citation_ids:
                    evidence = (
                        (
                            await session.execute(
                                text(
                                    "SELECT source_id,revision_id,chunk_id,chunk_content_hash "
                                    "FROM knowledge_citation_snapshot "
                                    "WHERE enterprise_id=:enterprise_id AND project_id=:project_id "
                                    "AND citation_id=:citation_id"
                                ),
                                {
                                    "enterprise_id": scope.enterprise_id,
                                    "project_id": scope.project_id,
                                    "citation_id": citation_id,
                                },
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if evidence is not None:
                        authorized_evidence.append(
                            {
                                "citationSnapshotId": citation_id,
                                "sourceRevisionId": evidence["revision_id"],
                                "documentRevisionId": evidence["revision_id"],
                                "chunkId": evidence["chunk_id"],
                                "contentDigest": evidence["chunk_content_hash"],
                            }
                        )
        answer_snapshot["authorizedEvidence"] = authorized_evidence
        return TestDesignContext(
            scope,
            request,
            row["input_snapshot"],
            answer_snapshot,
        )

    async def generation_waiting_reason(
        self, scope: ProjectScopeContext, claim: ClaimedTestDesignJob
    ) -> str | None:
        await self.generation_context(scope, claim)
        return None

    async def renew_generation_job(
        self,
        scope: ProjectScopeContext,
        claim: ClaimedTestDesignJob,
        *,
        now: datetime,
        lease_duration: timedelta,
    ) -> None:
        scope = self._matching_scope(scope)
        if not timedelta(0) < lease_duration <= timedelta(minutes=15):
            raise ValueError("test design worker lease is invalid")
        instant = _naive(now)
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(test_plan_generation_job)
                .where(
                    *scope_predicates(test_plan_generation_job, scope),
                    test_plan_generation_job.c.job_id == claim.job.request.job_id,
                    test_plan_generation_job.c.status == GenerationJobStatus.RUNNING.value,
                    test_plan_generation_job.c.lease_token == claim.lease_token,
                    test_plan_generation_job.c.lease_expires_at >= instant,
                )
                .values(lease_expires_at=instant + lease_duration, updated_at=instant)
            )
            if result.rowcount != 1:
                raise RevisionConflict("test design worker lease was lost")
            from tap.modules.ai.adapters.mysql_checkpointer import graph_run

            await session.execute(
                update(graph_run)
                .where(
                    *scope_predicates(graph_run, scope),
                    graph_run.c.run_id == claim.job.request.job_id,
                    graph_run.c.status == "RUNNING",
                    graph_run.c.lease_token == claim.lease_token,
                )
                .values(
                    lease_until=instant + lease_duration,
                    updated_at=instant,
                )
            )

    async def complete_generation(
        self,
        scope: ProjectScopeContext,
        claim: ClaimedTestDesignJob,
        revision: TestPlanRevision,
        *,
        now: datetime,
    ) -> TestPlanRevision:
        scope = self._matching_scope(scope)
        request = claim.job.request
        if (
            revision.status is not RevisionStatus.DRAFT
            or revision.revision_id != request.revision_id
            or revision.test_plan_id != request.test_plan_id
        ):
            raise ValueError("generated draft identity or state is invalid")
        instant = _naive(now)
        async with self._sessions() as session, session.begin():
            job = (
                (
                    await session.execute(
                        select(test_plan_generation_job)
                        .where(
                            *scope_predicates(test_plan_generation_job, scope),
                            test_plan_generation_job.c.job_id == request.job_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                job is None
                or job["status"] != GenerationJobStatus.RUNNING.value
                or job["lease_token"] != claim.lease_token
                or job["lease_expires_at"] is None
                or job["lease_expires_at"] < instant
            ):
                raise RevisionConflict("test design worker lease was lost")
            if await self._load_revision(session, revision.revision_id) is not None:
                raise RevisionConflict("generated revision already exists")
            await session.execute(
                insert(test_plan).values(
                    **scope_values(scope),
                    test_plan_id=revision.test_plan_id,
                    title=revision.title,
                    active_published_revision_id=None,
                    created_at=instant,
                    updated_at=instant,
                )
            )
            await self._insert_revision(session, revision, now=instant)
            link_material = f"{scope.project_id}:{request.turn_id}:{revision.test_plan_id}"
            link_id = "tpl_" + hashlib.sha256(link_material.encode()).hexdigest()[:32]
            await session.execute(
                insert(turn_artifact_link).values(
                    **scope_values(scope),
                    link_id=link_id,
                    turn_id=request.turn_id,
                    artifact_kind="test-plan",
                    artifact_id=revision.test_plan_id,
                    artifact_digest=revision.content_digest,
                    created_at=instant,
                )
            )
            result = await session.execute(
                update(test_plan_generation_job)
                .where(
                    *scope_predicates(test_plan_generation_job, scope),
                    test_plan_generation_job.c.job_id == request.job_id,
                    test_plan_generation_job.c.lease_token == claim.lease_token,
                    test_plan_generation_job.c.lease_expires_at >= instant,
                )
                .values(
                    status=GenerationJobStatus.DRAFT_READY.value,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    updated_at=instant,
                )
            )
            if result.rowcount != 1:
                raise RevisionConflict("test design worker lease was lost")
            await self._generation_stream_event(
                session,
                request,
                event_type="test-plan.generation.result_ready",
                payload={
                    "jobId": request.job_id,
                    "testPlanId": request.test_plan_id,
                    "revisionId": request.revision_id,
                    "deepLink": (
                        f"/test-management/{request.test_plan_id}/revisions/{request.revision_id}"
                    ),
                },
                now=instant,
            )
            from tap.modules.ai.adapters.mysql_checkpointer import (
                graph_checkpoint,
                graph_run,
                graph_settlement,
            )

            graph = (
                (
                    await session.execute(
                        select(graph_run)
                        .where(
                            *scope_predicates(graph_run, scope),
                            graph_run.c.run_id == request.job_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if graph is not None:
                checkpoint_id = graph["current_checkpoint_id"]
                assert checkpoint_id is not None
                await session.execute(
                    insert(graph_settlement).values(
                        **scope_values(scope),
                        run_id=request.job_id,
                        checkpoint_id=checkpoint_id,
                        outcome="SUCCEEDED",
                        created_at=instant,
                    )
                )
                await session.execute(
                    update(graph_run)
                    .where(
                        *scope_predicates(graph_run, scope),
                        graph_run.c.run_id == request.job_id,
                    )
                    .values(
                        status="SUCCEEDED",
                        waiting_reason=None,
                        lease_owner=None,
                        lease_token=None,
                        lease_until=None,
                        updated_at=instant,
                    )
                )
                checkpoint_count = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(graph_checkpoint)
                        .where(
                            *scope_predicates(graph_checkpoint, scope),
                            graph_checkpoint.c.run_id == request.job_id,
                        )
                    )
                    or 1
                )
                await write_project_event(
                    session,
                    scope=scope,
                    envelope=ProjectEventEnvelope(
                        event_id=scoped_outbox_id(
                            scope,
                            kind="ai-graph-checkpoint",
                            identity=f"{request.job_id}:{checkpoint_id}",
                        ),
                        event_type="ai.graph-run.checkpointed",
                        schema_version=1,
                        occurred_at=(now if now.tzinfo else now.replace(tzinfo=timezone.utc)),
                        scope_kind="PROJECT",
                        enterprise_id=scope.enterprise_id,
                        project_id=scope.project_id,
                        actor_id=scope.actor_id,
                        identity_mode=scope.identity_mode.value,
                        aggregate_type="GraphRun",
                        aggregate_id=request.job_id,
                        aggregate_version=max(1, checkpoint_count),
                        correlation_id=request.job_id,
                        causation_id=None,
                        idempotency_key=f"graph-checkpoint:{checkpoint_id}",
                        payload={
                            "runId": request.job_id,
                            "checkpointId": checkpoint_id,
                            "graphVersion": graph["graph_version"],
                            "stateSchemaVersion": str(graph["state_schema_version"]),
                        },
                    ),
                )
        return replace(revision, created_at=instant)

    async def fail_generation(
        self,
        scope: ProjectScopeContext,
        claim: ClaimedTestDesignJob,
        *,
        failure_code: str,
        now: datetime,
    ) -> None:
        scope = self._matching_scope(scope)
        if not failure_code or len(failure_code) > 64:
            raise ValueError("test design failure code is invalid")
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(test_plan_generation_job)
                .where(
                    *scope_predicates(test_plan_generation_job, scope),
                    test_plan_generation_job.c.job_id == claim.job.request.job_id,
                    test_plan_generation_job.c.status == GenerationJobStatus.RUNNING.value,
                    test_plan_generation_job.c.lease_token == claim.lease_token,
                    test_plan_generation_job.c.lease_expires_at >= _naive(now),
                )
                .values(
                    status=GenerationJobStatus.FAILED.value,
                    failure_code=failure_code,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    updated_at=_naive(now),
                )
            )
            if result.rowcount != 1:
                raise RevisionConflict("test design worker lease was lost")
            await self._generation_stream_event(
                session,
                claim.job.request,
                event_type="test-plan.generation.failed",
                payload={
                    "jobId": claim.job.request.job_id,
                    "failureCode": failure_code,
                },
                now=_naive(now),
            )
            from tap.modules.ai.adapters.mysql_checkpointer import graph_run

            await session.execute(
                update(graph_run)
                .where(
                    *scope_predicates(graph_run, scope),
                    graph_run.c.run_id == claim.job.request.job_id,
                )
                .values(
                    status="FAILED",
                    waiting_reason=None,
                    lease_owner=None,
                    lease_token=None,
                    lease_until=None,
                    updated_at=_naive(now),
                )
            )

    async def wait_generation(
        self,
        scope: ProjectScopeContext,
        claim: ClaimedTestDesignJob,
        *,
        reason: str,
        now: datetime,
    ) -> None:
        scope = self._matching_scope(scope)
        if not reason.strip() or len(reason) > 128:
            raise ValueError("test design waiting reason is invalid")
        instant = _naive(now)
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(test_plan_generation_job)
                .where(
                    *scope_predicates(test_plan_generation_job, scope),
                    test_plan_generation_job.c.job_id == claim.job.request.job_id,
                    test_plan_generation_job.c.status == GenerationJobStatus.RUNNING.value,
                    test_plan_generation_job.c.lease_token == claim.lease_token,
                    test_plan_generation_job.c.lease_expires_at >= instant,
                )
                .values(
                    status=GenerationJobStatus.WAITING.value,
                    failure_code=reason,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    updated_at=instant,
                )
            )
            if result.rowcount != 1:
                raise RevisionConflict("test design worker lease was lost")
            await self._generation_stream_event(
                session,
                claim.job.request,
                event_type="test-plan.generation.waiting",
                payload={"jobId": claim.job.request.job_id, "reason": reason},
                now=instant,
            )
            from tap.modules.ai.adapters.mysql_checkpointer import graph_run

            await session.execute(
                update(graph_run)
                .where(
                    *scope_predicates(graph_run, scope),
                    graph_run.c.run_id == claim.job.request.job_id,
                    graph_run.c.status == "RUNNING",
                    graph_run.c.lease_token == claim.lease_token,
                )
                .values(
                    status="WAITING",
                    waiting_reason=reason,
                    lease_owner=None,
                    lease_token=None,
                    lease_until=None,
                    updated_at=instant,
                )
            )

    def _matching_scope(self, scope: ProjectScopeContext) -> ProjectScopeContext:
        scope = require_project_scope(scope)
        if scope != self.scope:
            raise ValueError("repository is bound to a different Project")
        return scope

    async def _insert_revision(
        self, session: AsyncSession, revision: TestPlanRevision, *, now: datetime
    ) -> None:
        values = scope_values(self.scope)
        await session.execute(
            insert(test_plan_revision).values(
                **values,
                revision_id=revision.revision_id,
                test_plan_id=revision.test_plan_id,
                version=revision.version,
                row_version=revision.row_version,
                status=revision.status.value,
                origin=revision.origin.value,
                adopted_from_revision_id=revision.adopted_from_revision_id,
                title=revision.title,
                objective=revision.objective,
                scope_items=list(revision.scope_items),
                prerequisites=list(revision.prerequisites),
                risks=list(revision.risks),
                content_digest=revision.content_digest,
                validation_digest=revision.validation_digest,
                created_at=now,
                published_at=revision.published_at,
                requirement_scope_id=revision.requirement_scope_id,
                requirement_scope_version=revision.requirement_scope_version,
                requirement_scope_digest=revision.requirement_scope_digest,
                requirement_ids=list(revision.requirement_ids),
                approved_knowledge_revision_ids=list(revision.approved_knowledge_revision_ids),
                model_revision_id=revision.model_revision_id,
                agent_revision_id=revision.agent_revision_id,
                skill_revision_ids=list(revision.skill_revision_ids),
                author_actor_id=revision.author_actor_id,
                strict_review_required=revision.strict_review_required,
                generated_content_digest=revision.generated_content_digest,
                needs_review=revision.needs_review,
                needs_review_reason=revision.needs_review_reason,
            )
        )
        await self._insert_revision_children(session, revision)

    async def _insert_revision_children(
        self, session: AsyncSession, revision: TestPlanRevision
    ) -> None:
        values = scope_values(self.scope)
        for case in revision.cases:
            await session.execute(
                insert(test_case).values(
                    **values,
                    case_id=case.case_id,
                    revision_id=revision.revision_id,
                    ordinal=case.ordinal,
                    title=case.title,
                    objective=case.objective,
                    critical=case.critical,
                    covered_requirement_ids=list(case.covered_requirement_ids),
                )
            )
            for scenario in case.scenarios:
                await session.execute(
                    insert(test_scenario).values(
                        **values,
                        scenario_id=scenario.scenario_id,
                        revision_id=revision.revision_id,
                        case_id=case.case_id,
                        ordinal=scenario.ordinal,
                        title=scenario.title,
                    )
                )
                for step in scenario.steps:
                    await session.execute(
                        insert(test_plan_step).values(
                            **values,
                            step_id=step.step_id,
                            revision_id=revision.revision_id,
                            scenario_id=scenario.scenario_id,
                            ordinal=step.ordinal,
                            keyword=step.keyword.value,
                            text=step.text,
                            expected_result=step.expected_result,
                            critical=step.critical,
                            citation_ids=list(step.citation_ids),
                            unknown_ids=list(step.unknown_ids),
                        )
                    )
        for citation in revision.citations:
            await session.execute(
                insert(test_plan_citation).values(
                    **values,
                    citation_id=citation.citation_id,
                    revision_id=revision.revision_id,
                    source_revision_id=citation.source_revision_id,
                    document_revision_id=citation.document_revision_id,
                    chunk_id=citation.chunk_id,
                    content_digest=citation.content_digest,
                    claim_text=citation.claim_text,
                    origin=citation.origin.value,
                )
            )
        for assumption in revision.assumptions:
            await session.execute(
                insert(test_plan_assumption).values(
                    **values,
                    assumption_id=assumption.assumption_id,
                    revision_id=revision.revision_id,
                    text=assumption.text,
                    graph_edge_id=assumption.graph_edge_id,
                )
            )
        for unknown in revision.unknowns:
            await session.execute(
                insert(test_plan_unknown).values(
                    **values,
                    unknown_id=unknown.unknown_id,
                    revision_id=revision.revision_id,
                    text=unknown.text,
                )
            )
        for gap in revision.coverage_gaps:
            await session.execute(
                insert(test_plan_coverage_gap).values(
                    **values,
                    gap_id=gap.gap_id,
                    revision_id=revision.revision_id,
                    requirement_ref=gap.requirement_ref,
                    reason=gap.reason,
                    severity=gap.severity.value,
                )
            )

    async def _load_revision(
        self, session: AsyncSession, revision_id: str
    ) -> TestPlanRevision | None:
        row = (
            (
                await session.execute(
                    select(test_plan_revision).where(
                        *scope_predicates(test_plan_revision, self.scope),
                        test_plan_revision.c.revision_id == revision_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        cases_rows = (
            (
                await session.execute(
                    select(test_case)
                    .where(
                        *scope_predicates(test_case, self.scope),
                        test_case.c.revision_id == revision_id,
                    )
                    .order_by(test_case.c.ordinal)
                )
            )
            .mappings()
            .all()
        )
        scenario_rows = (
            (
                await session.execute(
                    select(test_scenario)
                    .where(
                        *scope_predicates(test_scenario, self.scope),
                        test_scenario.c.revision_id == revision_id,
                    )
                    .order_by(test_scenario.c.case_id, test_scenario.c.ordinal)
                )
            )
            .mappings()
            .all()
        )
        step_rows = (
            (
                await session.execute(
                    select(test_plan_step)
                    .where(
                        *scope_predicates(test_plan_step, self.scope),
                        test_plan_step.c.revision_id == revision_id,
                    )
                    .order_by(test_plan_step.c.scenario_id, test_plan_step.c.ordinal)
                )
            )
            .mappings()
            .all()
        )
        scenarios = {
            item["case_id"]: tuple(
                TestScenario(
                    candidate["scenario_id"],
                    candidate["ordinal"],
                    candidate["title"],
                    tuple(
                        TestPlanStep(
                            step["step_id"],
                            step["ordinal"],
                            BddKeyword(step["keyword"]),
                            step["text"],
                            step["expected_result"],
                            bool(step["critical"]),
                            tuple(step["citation_ids"]),
                            tuple(step["unknown_ids"]),
                        )
                        for step in step_rows
                        if step["scenario_id"] == candidate["scenario_id"]
                    ),
                )
                for candidate in scenario_rows
                if candidate["case_id"] == item["case_id"]
            )
            for item in cases_rows
        }
        cases = tuple(
            TestCase(
                item["case_id"],
                item["ordinal"],
                item["title"],
                item["objective"],
                bool(item["critical"]),
                scenarios[item["case_id"]],
                tuple(item["covered_requirement_ids"]),
            )
            for item in cases_rows
        )

        async def rows(table):
            return (
                (
                    await session.execute(
                        select(table).where(
                            *scope_predicates(table, self.scope),
                            table.c.revision_id == revision_id,
                        )
                    )
                )
                .mappings()
                .all()
            )

        citations = tuple(
            TestPlanCitation(
                item["citation_id"],
                item["source_revision_id"],
                item["document_revision_id"],
                item["chunk_id"],
                item["content_digest"],
                item["claim_text"],
                CitationOrigin(item["origin"]),
            )
            for item in await rows(test_plan_citation)
        )
        assumptions = tuple(
            TestPlanAssumption(item["assumption_id"], item["text"], item["graph_edge_id"])
            for item in await rows(test_plan_assumption)
        )
        unknowns = tuple(
            TestPlanUnknown(item["unknown_id"], item["text"])
            for item in await rows(test_plan_unknown)
        )
        gaps = tuple(
            TestPlanCoverageGap(
                item["gap_id"],
                item["requirement_ref"],
                item["reason"],
                GapSeverity(item["severity"]),
            )
            for item in await rows(test_plan_coverage_gap)
        )
        decisions = tuple(
            TestPlanReviewDecision(
                item["decision_id"],
                ReviewDisposition(item["disposition"]),
                item["reason"],
                item["review_actor_id"],
                item["reviewed_content_digest"],
                item["created_at"],
            )
            for item in sorted(
                await rows(test_plan_review_decision), key=lambda value: value["created_at"]
            )
        )
        return TestPlanRevision(
            row["test_plan_id"],
            row["revision_id"],
            row["version"],
            row["title"],
            row["objective"],
            tuple(row["scope_items"]),
            tuple(row["prerequisites"]),
            tuple(row["risks"]),
            cases,
            citations,
            assumptions,
            unknowns,
            gaps,
            RevisionStatus(row["status"]),
            IdentityOrigin(row["origin"]),
            row["adopted_from_revision_id"],
            row["content_digest"],
            row["row_version"],
            row["validation_digest"],
            row["created_at"],
            row["published_at"],
            row["requirement_scope_id"],
            row["requirement_scope_version"],
            row["requirement_scope_digest"],
            tuple(row["requirement_ids"]),
            tuple(row["approved_knowledge_revision_ids"]),
            row["model_revision_id"],
            row["agent_revision_id"],
            tuple(row["skill_revision_ids"]),
            row["author_actor_id"],
            bool(row["strict_review_required"]),
            row["generated_content_digest"],
            decisions,
            bool(row["needs_review"]),
            row["needs_review_reason"],
        )

    @staticmethod
    def _job(row) -> TestPlanGenerationJob:
        request = TestPlanGenerationRequest(
            row["job_id"],
            row["test_plan_id"],
            row["revision_id"],
            row["project_id"],
            row["conversation_id"],
            row["turn_id"],
            row["input_snapshot_digest"],
            row["answer_evidence_snapshot_digest"],
            row["model_alias"],
            row["agent_revision_id"],
            tuple(row["skill_revision_ids"]),
            row["objective"],
            row["idempotency_key"],
            row["request_digest"],
            _requirement_scope(row["requirement_scope"]),
            tuple(row["approved_knowledge_revision_ids"]),
            row["model_revision_id"],
        )
        return TestPlanGenerationJob(
            request,
            GenerationJobStatus(row["status"]),
            row["created_at"],
            row["updated_at"],
            row["attempt_count"],
            row["lease_owner"],
            row["lease_token"],
            row["lease_expires_at"],
            row["failure_code"],
        )
