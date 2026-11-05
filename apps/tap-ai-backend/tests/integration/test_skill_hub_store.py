"""`MysqlSkillHubStore` integration test (Task 8, 2026-11-01 Skill Hub phase
1): the MySQL read models (library records, activations by mode, users,
ratings, last use, pinned-by Agents, trend, top Agents/callers) and the two
writes (category, owner transfer), all scoped to one Project.

Requires `TAP_RUN_MYSQL_INTEGRATION=1`; uses the isolated `owned_project_mysql`
fixture (never the shared default MySQL service)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from scripts.migration_support import IsolatedMysql
from sqlalchemy.exc import IntegrityError

from tap.modules.access.adapters.mysql import project
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.agent_team.domain.models import TAPPER_DEFAULT_AGENT_KEY
from tap.modules.ai.adapters.maintenance import skill_maintenance_event
from tap.modules.ai.adapters.mysql import ai_agent, ai_agent_revision, skill, skill_revision
from tap.modules.ai.domain.maintenance import new_skill_event
from tap.modules.chat.adapters.mysql_skill_activations import (
    activation_id_for,
    turn_skill_activation,
)
from tap.modules.skill_hub.adapters.mysql import MysqlSkillHubStore
from tap.modules.skill_hub.ports import (
    ActivationCount,
    RankedKey,
    SkillRatings,
    SkillRecord,
    UsagePoint,
)
from tap.platform.db.session import create_engine_and_session_factory
from tap.platform.reporting.period import Period
from tests.integration.test_agent_team_store import (
    OTHER_SCOPE,
    _insert_actor,
    _insert_feedback,
    _insert_turn,
    _scope_values,
)
from tests.owned_mysql import owned_project_database_url

_T0 = datetime(2026, 10, 1, 9, 0, 0)  # a Thursday
_DAY0 = Period.parse(_T0.date(), _T0.date())


async def _insert_skill(
    connection,
    scope: ProjectScopeContext,
    skill_id: str,
    *,
    category: str = "other",
    owner_actor_id: str | None = None,
    created_at: datetime = _T0,
) -> None:
    await connection.execute(
        sa.insert(skill).values(
            skill_id=skill_id,
            display_name=skill_id,
            created_at=created_at,
            category=category,
            owner_actor_id=owner_actor_id or scope.actor_id,
            owner_assigned_at=created_at,
            **_scope_values(scope),
        )
    )
    await _insert_skill_revision(connection, scope, skill_id, revision_number=1)


async def _insert_skill_revision(
    connection,
    scope: ProjectScopeContext,
    skill_id: str,
    *,
    revision_number: int,
    display_name: str | None = None,
    description: str | None = None,
    source_kind: str = "custom",
    version_label: str | None = None,
    frontmatter: dict | None = None,
    applicable_tasks: list[str] | None = None,
    origin_kind: str = "unknown",
    origin_ref: str | None = None,
    origin_imported_at: datetime | None = None,
    status: str = "enabled",
    created_at: datetime = _T0,
    actor_id: str | None = None,
) -> None:
    scope_values = _scope_values(scope)
    if actor_id is not None:
        scope_values["actor_id"] = actor_id
    await connection.execute(
        sa.insert(skill_revision).values(
            revision_id=f"{skill_id}-r{revision_number}",
            skill_id=skill_id,
            revision_number=revision_number,
            display_name=display_name or skill_id,
            description=description,
            source_kind=source_kind,
            content_digest="sha256:" + uuid4().hex,
            instruction_template_digest="sha256:" + uuid4().hex,
            instruction_template="do the thing",
            frontmatter_json=frontmatter,
            applicable_tasks=applicable_tasks or ["knowledge.answer"],
            version_label=version_label,
            origin_kind=origin_kind,
            origin_ref=origin_ref,
            origin_imported_at=origin_imported_at,
            status=status,
            adopted_from_revision_id=None,
            created_at=created_at,
            **scope_values,
        )
    )


async def _insert_agent(
    connection,
    scope: ProjectScopeContext,
    agent_id: str,
    revisions: list[tuple[list[str] | None, str]],
) -> None:
    """`revisions`: `(pinned_skill_ids, status)` per revision, oldest first;
    revision `n` is `<agent_id>-r<n>`."""
    await connection.execute(
        sa.insert(ai_agent).values(
            agent_id=agent_id,
            display_name=agent_id,
            created_at=_T0,
            job_title=None,
            owner_actor_id=scope.actor_id,
            owner_assigned_at=_T0,
            **_scope_values(scope),
        )
    )
    for number, (pinned, status) in enumerate(revisions, start=1):
        await connection.execute(
            sa.insert(ai_agent_revision).values(
                revision_id=f"{agent_id}-r{number}",
                agent_id=agent_id,
                revision_number=number,
                display_name=agent_id,
                description=None,
                source_kind="custom",
                content_digest="sha256:" + uuid4().hex,
                system_instruction_digest="sha256:" + uuid4().hex,
                system_instruction="be helpful",
                tool_allowlist=[],
                output_schema_digest="sha256:" + uuid4().hex,
                output_schema_json={},
                skill_revision_ids=None,
                pinned_skill_ids=pinned,
                excluded_skill_ids=None,
                default_model_alias=None,
                status=status,
                adopted_from_revision_id=None,
                created_at=_T0 + timedelta(minutes=number),
                **_scope_values(scope),
            )
        )


async def _insert_activation(
    connection,
    scope: ProjectScopeContext,
    *,
    turn_id: str,
    skill_id: str,
    mode: str,
    activated_at: datetime,
) -> None:
    await connection.execute(
        sa.insert(turn_skill_activation).values(
            activation_id=activation_id_for(turn_id, skill_id),
            turn_id=turn_id,
            skill_id=skill_id,
            skill_revision_id=f"{skill_id}-r1",
            mode=mode,
            activated_at=activated_at,
            bytes=12,
            **_scope_values(scope),
        )
    )


async def _insert_used_turn(
    connection,
    scope: ProjectScopeContext,
    turn_id: str,
    uses: list[tuple[str, str]],
    *,
    created_at: datetime = _T0,
    activated_at: datetime | None = None,
    agent_revision_id: str | None = None,
    actor_id: str | None = None,
) -> None:
    """A Turn plus one activation per `(skill_id, mode)` in `uses`."""
    await _insert_turn(
        connection,
        scope,
        turn_id=turn_id,
        created_at=created_at,
        agent_revision_id=agent_revision_id,
        actor_id=actor_id,
    )
    for skill_id, mode in uses:
        await _insert_activation(
            connection,
            scope,
            turn_id=turn_id,
            skill_id=skill_id,
            mode=mode,
            activated_at=activated_at or created_at,
        )


def _run(owned_project_mysql: IsolatedMysql, seed, check) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await seed(connection)
            await check(sessions, MysqlSkillHubStore(sessions, scope=VALIDATION_SCOPE))
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_activation_counts_split_by_mode(owned_project_mysql: IsolatedMysql) -> None:
    async def seed(connection) -> None:
        for skill_id in ("s-a", "s-b", "s-unused"):
            await _insert_skill(connection, VALIDATION_SCOPE, skill_id)
        await _insert_used_turn(
            connection, VALIDATION_SCOPE, "t1", [("s-a", "manual"), ("s-b", "auto")]
        )
        await _insert_used_turn(connection, VALIDATION_SCOPE, "t2", [("s-a", "manual")])
        await _insert_used_turn(connection, VALIDATION_SCOPE, "t3", [("s-a", "pinned")])
        await _insert_used_turn(connection, VALIDATION_SCOPE, "t4", [("s-a", "auto")])

    async def check(sessions, store: MysqlSkillHubStore) -> None:
        counts = await store.activation_counts(_DAY0)
        assert sorted(counts, key=lambda c: (c.skill_id, c.mode)) == [
            ActivationCount("s-a", "auto", 1),
            ActivationCount("s-a", "manual", 2),
            ActivationCount("s-a", "pinned", 1),
            ActivationCount("s-b", "auto", 1),
        ]

    _run(owned_project_mysql, seed, check)


def test_counts_use_activated_at_period(owned_project_mysql: IsolatedMysql) -> None:
    async def seed(connection) -> None:
        await _insert_skill(connection, VALIDATION_SCOPE, "s-a")
        # Turn created the day before, but the Skill was activated inside the
        # period: counted.
        await _insert_used_turn(
            connection,
            VALIDATION_SCOPE,
            "t-in",
            [("s-a", "auto")],
            created_at=_T0 - timedelta(days=1),
            activated_at=_T0,
        )
        # Turn created inside the period, activated the next day: not counted.
        await _insert_used_turn(
            connection,
            VALIDATION_SCOPE,
            "t-out",
            [("s-a", "manual")],
            created_at=_T0,
            activated_at=_T0 + timedelta(days=1),
        )

    async def check(sessions, store: MysqlSkillHubStore) -> None:
        assert await store.activation_counts(_DAY0) == [ActivationCount("s-a", "auto", 1)]
        assert await store.distinct_users(_DAY0) == {"s-a": 1}
        actor = VALIDATION_SCOPE.actor_id
        assert await store.top_callers("s-a", _DAY0) == [RankedKey(actor, 1)]
        assert await store.top_agents("s-a", _DAY0) == [RankedKey(TAPPER_DEFAULT_AGENT_KEY, 1)]
        assert await store.usage_trend("s-a", _DAY0, "day") == [UsagePoint(_T0.date(), "auto", 1)]
        next_day = _T0.date() + timedelta(days=1)
        assert await store.activation_counts(Period.parse(next_day, next_day)) == [
            ActivationCount("s-a", "manual", 1)
        ]

    _run(owned_project_mysql, seed, check)


def test_distinct_users_count_turn_actors(owned_project_mysql: IsolatedMysql) -> None:
    async def seed(connection) -> None:
        for actor_id in ("alice", "bob"):
            await _insert_actor(connection, actor_id=actor_id)
        for skill_id in ("s-a", "s-b"):
            await _insert_skill(connection, VALIDATION_SCOPE, skill_id)
        # Every activation row carries the scope actor; only the Turn's own
        # `actor_id` identifies the user.
        await _insert_used_turn(
            connection, VALIDATION_SCOPE, "t1", [("s-a", "manual")], actor_id="alice"
        )
        await _insert_used_turn(
            connection, VALIDATION_SCOPE, "t2", [("s-a", "auto")], actor_id="alice"
        )
        await _insert_used_turn(
            connection,
            VALIDATION_SCOPE,
            "t3",
            [("s-a", "pinned"), ("s-b", "auto")],
            actor_id="bob",
        )

    async def check(sessions, store: MysqlSkillHubStore) -> None:
        assert await store.distinct_users(_DAY0) == {"s-a": 2, "s-b": 1}
        assert await store.top_callers("s-a", _DAY0) == [
            RankedKey("alice", 2),
            RankedKey("bob", 1),
        ]
        assert await store.top_callers("s-a", _DAY0, limit=1) == [RankedKey("alice", 2)]

    _run(owned_project_mysql, seed, check)


def test_ratings_use_turn_created_time(owned_project_mysql: IsolatedMysql) -> None:
    async def seed(connection) -> None:
        await _insert_actor(connection, actor_id="bob")
        for skill_id in ("s-a", "s-b"):
            await _insert_skill(connection, VALIDATION_SCOPE, skill_id)
        # Turn created in the period; the activation and both ratings land
        # later -- still counted in the Turn's period.
        await _insert_used_turn(
            connection,
            VALIDATION_SCOPE,
            "t-in",
            [("s-a", "manual")],
            created_at=_T0,
            activated_at=_T0 + timedelta(days=2),
        )
        late = _T0 + timedelta(days=30)
        for feedback_id, actor_id, rating in (
            ("fb-up", VALIDATION_SCOPE.actor_id, "up"),
            ("fb-down", "bob", "down"),
        ):
            await _insert_feedback(
                connection,
                VALIDATION_SCOPE,
                feedback_id=feedback_id,
                turn_id="t-in",
                conversation_id="irrelevant",
                actor_id=actor_id,
                rating=rating,
                comment=None,
                created_at=late,
            )
        # Turn created the day before, activation inside the period: excluded.
        await _insert_used_turn(
            connection,
            VALIDATION_SCOPE,
            "t-before",
            [("s-a", "auto")],
            created_at=_T0 - timedelta(days=1),
            activated_at=_T0,
        )
        await _insert_feedback(
            connection,
            VALIDATION_SCOPE,
            feedback_id="fb-before",
            turn_id="t-before",
            conversation_id="irrelevant",
            actor_id=VALIDATION_SCOPE.actor_id,
            rating="down",
            comment=None,
            created_at=_T0,
        )
        # A comment-only (unrated) feedback counts as neither up nor down.
        await _insert_used_turn(connection, VALIDATION_SCOPE, "t-comment", [("s-b", "auto")])
        await _insert_feedback(
            connection,
            VALIDATION_SCOPE,
            feedback_id="fb-comment",
            turn_id="t-comment",
            conversation_id="irrelevant",
            actor_id=VALIDATION_SCOPE.actor_id,
            rating=None,
            comment="hmm",
            created_at=_T0,
        )

    async def check(sessions, store: MysqlSkillHubStore) -> None:
        assert await store.ratings(_DAY0) == [SkillRatings("s-a", up=1, down=1)]

    _run(owned_project_mysql, seed, check)


def test_last_used_ignores_period(owned_project_mysql: IsolatedMysql) -> None:
    async def seed(connection) -> None:
        for skill_id in ("s-a", "s-b", "s-unused"):
            await _insert_skill(connection, VALIDATION_SCOPE, skill_id)
        await _insert_used_turn(connection, VALIDATION_SCOPE, "t1", [("s-a", "manual")])
        await _insert_used_turn(
            connection,
            VALIDATION_SCOPE,
            "t2",
            [("s-a", "auto")],
            created_at=_T0 + timedelta(days=100, microseconds=5),
        )
        await _insert_used_turn(
            connection,
            VALIDATION_SCOPE,
            "t3",
            [("s-b", "pinned")],
            created_at=_T0 - timedelta(days=50),
        )

    async def check(sessions, store: MysqlSkillHubStore) -> None:
        assert await store.last_used() == {
            "s-a": (_T0 + timedelta(days=100, microseconds=5)).replace(tzinfo=UTC),
            "s-b": (_T0 - timedelta(days=50)).replace(tzinfo=UTC),
        }

    _run(owned_project_mysql, seed, check)


def test_pinned_by_counts_latest_enabled_agent_revisions_only(
    owned_project_mysql: IsolatedMysql,
) -> None:
    async def seed(connection) -> None:
        for skill_id in ("s-a", "s-b", "s-c"):
            await _insert_skill(connection, VALIDATION_SCOPE, skill_id)
        # Latest revision pins s-a and s-b.
        await _insert_agent(
            connection,
            VALIDATION_SCOPE,
            "agent-a",
            [(["s-a"], "enabled"), (["s-a", "s-b"], "enabled")],
        )
        # An older revision pinned s-a and s-c; the latest pins nothing.
        await _insert_agent(
            connection, VALIDATION_SCOPE, "agent-b", [(["s-a", "s-c"], "enabled"), ([], "enabled")]
        )
        # Latest revision disabled.
        await _insert_agent(connection, VALIDATION_SCOPE, "agent-c", [(["s-a"], "disabled")])
        # NULL pinned list reads as empty.
        await _insert_agent(connection, VALIDATION_SCOPE, "agent-d", [(None, "enabled")])
        await _insert_agent(connection, VALIDATION_SCOPE, "agent-e", [(["s-a"], "enabled")])

    async def check(sessions, store: MysqlSkillHubStore) -> None:
        assert await store.pinned_by() == {
            "s-a": ("agent-a", "agent-e"),
            "s-b": ("agent-a",),
        }

    _run(owned_project_mysql, seed, check)


def test_usage_trend_buckets_by_granularity(owned_project_mysql: IsolatedMysql) -> None:
    uses = [
        ("t-sep30", datetime(2026, 9, 30, 23, 59, 59), "manual"),  # before the period
        ("t-mon", datetime(2026, 10, 5, 0, 0, 0), "manual"),  # Monday
        ("t-wed", datetime(2026, 10, 7, 12, 0, 0), "manual"),
        ("t-sun", datetime(2026, 10, 11, 23, 30, 0), "auto"),  # Sunday, same week
        ("t-mon2", datetime(2026, 10, 12, 0, 0, 0), "manual"),  # next Monday
        ("t-nov", datetime(2026, 11, 2, 8, 0, 0), "pinned"),
    ]

    async def seed(connection) -> None:
        for skill_id in ("s-a", "s-other"):
            await _insert_skill(connection, VALIDATION_SCOPE, skill_id)
        for turn_id, at, mode in uses:
            await _insert_used_turn(
                connection, VALIDATION_SCOPE, turn_id, [("s-a", mode)], created_at=at
            )
        await _insert_used_turn(
            connection, VALIDATION_SCOPE, "t-other", [("s-other", "manual")], created_at=_T0
        )

    async def check(sessions, store: MysqlSkillHubStore) -> None:
        period = Period.parse(date(2026, 10, 1), date(2026, 11, 30))
        assert await store.usage_trend("s-a", period, "day") == [
            UsagePoint(date(2026, 10, 5), "manual", 1),
            UsagePoint(date(2026, 10, 7), "manual", 1),
            UsagePoint(date(2026, 10, 11), "auto", 1),
            UsagePoint(date(2026, 10, 12), "manual", 1),
            UsagePoint(date(2026, 11, 2), "pinned", 1),
        ]
        assert await store.usage_trend("s-a", period, "week") == [
            UsagePoint(date(2026, 10, 5), "manual", 2),
            UsagePoint(date(2026, 10, 5), "auto", 1),
            UsagePoint(date(2026, 10, 12), "manual", 1),
            UsagePoint(date(2026, 11, 2), "pinned", 1),
        ]
        assert await store.usage_trend("s-a", period, "month") == [
            UsagePoint(date(2026, 10, 1), "manual", 3),
            UsagePoint(date(2026, 10, 1), "auto", 1),
            UsagePoint(date(2026, 11, 1), "pinned", 1),
        ]

    _run(owned_project_mysql, seed, check)


def test_top_agents_include_tapper_default(owned_project_mysql: IsolatedMysql) -> None:
    async def seed(connection) -> None:
        await _insert_skill(connection, VALIDATION_SCOPE, "s-a")
        await _insert_agent(connection, VALIDATION_SCOPE, "agent-a", [([], "enabled")])
        await _insert_agent(connection, VALIDATION_SCOPE, "agent-b", [([], "enabled")])
        turns = [
            ("a1", "agent-a-r1"),
            ("a2", "agent-a-r1"),
            ("a3", "agent-a-r1"),
            ("b1", "agent-b-r1"),
            ("d1", None),
            ("d2", None),
        ]
        for turn_id, revision_id in turns:
            await _insert_used_turn(
                connection,
                VALIDATION_SCOPE,
                turn_id,
                [("s-a", "pinned" if revision_id else "manual")],
                agent_revision_id=revision_id,
            )

    async def check(sessions, store: MysqlSkillHubStore) -> None:
        assert await store.top_agents("s-a", _DAY0) == [
            RankedKey("agent-a", 3),
            RankedKey(TAPPER_DEFAULT_AGENT_KEY, 2),
            RankedKey("agent-b", 1),
        ]
        assert await store.top_agents("s-a", _DAY0, limit=2) == [
            RankedKey("agent-a", 3),
            RankedKey(TAPPER_DEFAULT_AGENT_KEY, 2),
        ]

    _run(owned_project_mysql, seed, check)


def test_transfer_writes_owner_and_event_atomically(owned_project_mysql: IsolatedMysql) -> None:
    async def seed(connection) -> None:
        for actor_id in ("new-owner", "third-owner"):
            await _insert_actor(connection, actor_id=actor_id)
        await _insert_skill(connection, VALIDATION_SCOPE, "s-a")

    async def owner_and_events(sessions) -> tuple[tuple[str, datetime], list[str]]:
        async with sessions() as session:
            owner = (
                await session.execute(
                    sa.select(skill.c.owner_actor_id, skill.c.owner_assigned_at).where(
                        skill.c.skill_id == "s-a"
                    )
                )
            ).one()
            events = (
                (
                    await session.execute(
                        sa.select(skill_maintenance_event.c.event_id).where(
                            skill_maintenance_event.c.skill_id == "s-a",
                            skill_maintenance_event.c.kind == "owner_transferred",
                        )
                    )
                )
                .scalars()
                .all()
            )
        return (owner.owner_actor_id, owner.owner_assigned_at), list(events)

    async def check(sessions, store: MysqlSkillHubStore) -> None:
        assert await store.actor_exists("new-owner") is True
        assert await store.actor_exists("ghost") is False

        transferred_at = (_T0 + timedelta(hours=3)).replace(tzinfo=UTC)
        event = new_skill_event(VALIDATION_SCOPE, "s-a", "owner_transferred", now=transferred_at)
        record = await store.transfer_owner("s-a", "new-owner", event)
        assert record.owner_actor_id == "new-owner"
        assert record.owner_assigned_at == transferred_at
        assert await owner_and_events(sessions) == (
            ("new-owner", transferred_at.replace(tzinfo=None)),
            [event.event_id],
        )

        # The event insert fails (duplicate `event_id`): the owner update in
        # the same transaction rolls back with it.
        later = transferred_at + timedelta(hours=1)
        duplicate = new_skill_event(VALIDATION_SCOPE, "s-a", "owner_transferred", now=later)
        duplicate = replace(duplicate, event_id=event.event_id)
        with pytest.raises(IntegrityError):
            await store.transfer_owner("s-a", "third-owner", duplicate)
        assert await owner_and_events(sessions) == (
            ("new-owner", transferred_at.replace(tzinfo=None)),
            [event.event_id],
        )
        unchanged = await store.skill("s-a")
        assert unchanged is not None and unchanged.owner_actor_id == "new-owner"

    _run(owned_project_mysql, seed, check)


def test_queries_are_project_scoped(owned_project_mysql: IsolatedMysql) -> None:
    async def seed(connection) -> None:
        await connection.execute(
            sa.insert(project).values(
                project_id=OTHER_SCOPE.project_id, enterprise_id=OTHER_SCOPE.enterprise_id
            )
        )
        for actor_id in ("importer", "editor"):
            await _insert_actor(connection, actor_id=actor_id)
        # Mine: a Skill whose second revision "importer" imported, an
        # authored Skill "editor" revised, and a built-in one.
        await _insert_skill(connection, VALIDATION_SCOPE, "s-mine", category="analysis")
        await _insert_skill_revision(
            connection,
            VALIDATION_SCOPE,
            "s-mine",
            revision_number=2,
            display_name="Mine v2",
            description="second revision",
            source_kind="imported",
            version_label="1.2.0",
            frontmatter={"name": "mine", "compatibility": "Python 3.13"},
            applicable_tasks=["test.design", "knowledge.answer"],
            origin_kind="zip",
            origin_ref="mine.zip",
            origin_imported_at=_T0 + timedelta(days=1),
            status="disabled",
            created_at=_T0 + timedelta(days=1),
            actor_id="importer",
        )
        await _insert_skill(connection, VALIDATION_SCOPE, "s-authored")
        await _insert_skill_revision(
            connection,
            VALIDATION_SCOPE,
            "s-authored",
            revision_number=2,
            source_kind="authored",
            origin_kind="authored",
            actor_id="editor",
        )
        await _insert_skill(connection, VALIDATION_SCOPE, "s-builtin")
        await connection.execute(
            sa.update(skill_revision)
            .where(skill_revision.c.skill_id == "s-builtin")
            .values(source_kind="built_in", origin_kind="built_in")
        )
        await _insert_used_turn(connection, VALIDATION_SCOPE, "t-mine", [("s-mine", "manual")])

        # Other Project: Skill, usage, rating and a pinning Agent.
        await _insert_skill(connection, OTHER_SCOPE, "s-other")
        await _insert_agent(connection, OTHER_SCOPE, "agent-other", [(["s-other"], "enabled")])
        await _insert_used_turn(
            connection,
            OTHER_SCOPE,
            "t-other",
            [("s-other", "pinned")],
            agent_revision_id="agent-other-r1",
        )
        await _insert_feedback(
            connection,
            OTHER_SCOPE,
            feedback_id="fb-other",
            turn_id="t-other",
            conversation_id="c",
            actor_id=OTHER_SCOPE.actor_id,
            rating="up",
            comment=None,
        )

    async def check(sessions, store: MysqlSkillHubStore) -> None:
        records = {record.skill_id: record for record in await store.skills()}
        assert records.keys() == {"s-mine", "s-authored", "s-builtin"}
        assert records["s-mine"] == SkillRecord(
            skill_id="s-mine",
            name="Mine v2",
            description="second revision",
            category="analysis",
            version="1.2.0",
            latest_revision_id="s-mine-r2",
            revision_number=2,
            source_kind="imported",
            origin_kind="zip",
            origin_ref="mine.zip",
            origin_imported_at=(_T0 + timedelta(days=1)).replace(tzinfo=UTC),
            # An imported Skill: the actor of its latest (importing) revision.
            origin_actor_id="importer",
            owner_actor_id=VALIDATION_SCOPE.actor_id,
            owner_assigned_at=_T0.replace(tzinfo=UTC),
            status="disabled",
            applicable_tasks=("knowledge.answer", "test.design"),
            compatibility="Python 3.13",
            updated_at=(_T0 + timedelta(days=1)).replace(tzinfo=UTC),
            built_in=False,
        )
        builtin = records["s-builtin"]
        assert (builtin.built_in, builtin.version, builtin.compatibility) == (True, "r1", None)
        # Authored/built-in Skills: the actor of the first revision.
        assert builtin.origin_actor_id == VALIDATION_SCOPE.actor_id
        assert records["s-authored"].origin_actor_id == VALIDATION_SCOPE.actor_id
        assert await store.skill("s-mine") == records["s-mine"]
        assert await store.skill("s-other") is None

        assert await store.activation_counts(_DAY0) == [ActivationCount("s-mine", "manual", 1)]
        assert await store.distinct_users(_DAY0) == {"s-mine": 1}
        assert await store.ratings(_DAY0) == []
        assert (await store.last_used()).keys() == {"s-mine"}
        assert await store.pinned_by() == {}
        assert await store.usage_trend("s-other", _DAY0, "day") == []
        assert await store.top_agents("s-other", _DAY0) == []
        assert await store.top_callers("s-other", _DAY0) == []

        updated = await store.set_category("s-mine", "writing")
        assert updated == replace(records["s-mine"], category="writing")
        with pytest.raises(LookupError):
            await store.set_category("s-other", "writing")
        event = new_skill_event(VALIDATION_SCOPE, "s-other", "owner_transferred", now=_T0)
        with pytest.raises(LookupError):
            await store.transfer_owner("s-other", VALIDATION_SCOPE.actor_id, event)
        async with sessions() as session:
            other = (
                await session.execute(
                    sa.select(skill.c.category).where(skill.c.skill_id == "s-other")
                )
            ).scalar_one()
            events = await session.scalar(
                sa.select(sa.func.count()).select_from(skill_maintenance_event)
            )
        assert (other, events) == ("other", 0)

    _run(owned_project_mysql, seed, check)
