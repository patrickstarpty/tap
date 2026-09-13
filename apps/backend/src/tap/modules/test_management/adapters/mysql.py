"""Project-scoped MySQL Test Plan repository."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKeyConstraint,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.mysql import DATETIME, JSON
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tap.contracts.events import ProjectEventEnvelope
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.chat.adapters.mysql import chat_turn
from tap.modules.chat.adapters.mysql_conversations import (
    conversation,
    turn_answer_evidence_snapshot,
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
    RevisionStatus,
    TestCase,
    TestPlanAssumption,
    TestPlanCitation,
    TestPlanCoverageGap,
    TestPlanGenerationJob,
    TestPlanGenerationRequest,
    TestPlanRevision,
    TestPlanStep,
    TestPlanUnknown,
    TestScenario,
)
from tap.modules.test_management.domain.validation import RevisionConflict, RevisionImmutable
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
    Column("model_alias", String(128), nullable=False),
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
)


def _naive(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


class MysqlTestPlanRepository:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, scope: ProjectScopeContext
    ) -> None:
        self._sessions = sessions
        self.scope = require_project_scope(scope)

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

    async def request_generation(
        self, request: TestPlanGenerationRequest, *, now: datetime
    ) -> TestPlanGenerationJob:
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
                            turn_answer_evidence_snapshot.c.snapshot_digest.label("answer_digest"),
                            turn_answer_evidence_snapshot.c.input_snapshot_digest.label(
                                "answer_input_digest"
                            ),
                        )
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
                    model_alias=request.model_alias,
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
            )
        )
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
