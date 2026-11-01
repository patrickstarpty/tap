"""`0038_skill_hub` migration: Skill category/owner backfill, revision version
and origin, Agent bindings mapped to `pinned_skill_ids`, `turn_skill_activation`
backfilled from frozen Turn inputs, the `trace` sync cursor reset, and the
downgrade (Task 1, 2026-11-01 Skill Hub phase 1)."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import create_engine, inspect, text

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.adapters.mysql_skill_activations import activation_id_for

PREVIOUS = "0037_agent_team_fk_name"
DIGEST = "sha256:" + "0" * 64
AUTHOR = "skill-author"
SKILL_CREATED = datetime(2026, 9, 1, 9, 0, 0)
SCOPE = {
    "enterprise_id": VALIDATION_SCOPE.enterprise_id,
    "project_id": VALIDATION_SCOPE.project_id,
    "actor_id": VALIDATION_SCOPE.actor_id,
    "identity_mode": VALIDATION_SCOPE.identity_mode.value,
    "identity_origin": VALIDATION_SCOPE.identity_mode.value.upper(),
}
_SCOPE_COLUMNS = "enterprise_id, project_id, actor_id, identity_mode, identity_origin"
_SCOPE_VALUES = ":enterprise_id, :project_id, :actor_id, :identity_mode, :identity_origin"

# (revision_id, skill_id, revision_number, source_kind, actor_id, created_at, frontmatter)
REVISIONS = (
    (
        "r1",
        "s-cat",
        1,
        "imported",
        AUTHOR,
        datetime(2026, 9, 1, 9, 0, 0),
        {"name": "s-cat", "metadata": {"category": "writing", "version": "1.0.0"}},
    ),
    (
        "r2",
        "s-cat",
        2,
        "imported",
        VALIDATION_SCOPE.actor_id,
        datetime(2026, 9, 2, 9, 0, 0),
        {"name": "s-cat", "metadata": {"category": "automation", "version": "v2"}},
    ),
    (
        "r3",
        "s-other",
        1,
        "authored",
        VALIDATION_SCOPE.actor_id,
        datetime(2026, 9, 3, 9, 0, 0),
        {"name": "s-other", "metadata": {"category": "ops"}},
    ),
    (
        "r4",
        "s-built",
        1,
        "built_in",
        VALIDATION_SCOPE.actor_id,
        datetime(2026, 9, 4, 9, 0, 0),
        None,
    ),
)
# (agent revision_id, revision_number, skill_revision_ids)
AGENT_REVISIONS = (
    ("ar1", 1, ["r3", "r1", "r2", "r-missing"]),
    ("ar2", 2, None),
)
# (turn_id, chat_turn.created_at, snapshot skill facts)
TURNS = (
    (
        "t1",
        datetime(2026, 9, 10, 8, 0, 0),
        {
            "agent_revision_id": "ar1",
            "skill_revision_ids": ["r3", "r1"],
            "skill_instruction_templates": ["ab", "数据"],
            "requested_skill_revision_ids": ["r1"],
        },
    ),
    (
        # Frozen before `requested_skill_revision_ids` existed: the Agent
        # revision's binding decides which Skills were pinned.
        "t2",
        datetime(2026, 9, 11, 8, 0, 0),
        {
            "agent_revision_id": "ar1",
            "skill_revision_ids": ["r1", "r4"],
            "skill_instruction_templates": ["abc", "d"],
        },
    ),
    (
        # Two revisions of one Skill: one row, the manual one wins.
        "t3",
        datetime(2026, 9, 12, 8, 0, 0),
        {
            "agent_revision_id": "ar1",
            "skill_revision_ids": ["r1", "r2"],
            "skill_instruction_templates": ["a", "bbbb"],
            "requested_skill_revision_ids": ["r2"],
        },
    ),
    (
        "t4",
        datetime(2026, 9, 13, 8, 0, 0),
        {"agent_revision_id": None, "skill_revision_ids": [], "requested_skill_revision_ids": []},
    ),
)


def _seed(connection) -> None:
    connection.execute(
        text(
            "INSERT INTO actor_principal (enterprise_id, actor_id, principal_type) "
            "VALUES (:enterprise_id, :actor_id, 'VALIDATION')"
        ),
        {"enterprise_id": VALIDATION_SCOPE.enterprise_id, "actor_id": AUTHOR},
    )
    for skill_id in ("s-cat", "s-other", "s-built"):
        connection.execute(
            text(
                f"INSERT INTO skill (skill_id, display_name, created_at, {_SCOPE_COLUMNS}) "
                f"VALUES (:skill_id, :skill_id, :created_at, {_SCOPE_VALUES})"
            ),
            SCOPE | {"skill_id": skill_id, "created_at": SKILL_CREATED},
        )
    for revision_id, skill_id, number, source_kind, actor_id, created_at, frontmatter in REVISIONS:
        connection.execute(
            text(
                "INSERT INTO skill_revision (revision_id, skill_id, revision_number, "
                "display_name, source_kind, content_digest, instruction_template_digest, "
                "frontmatter_json, applicable_tasks, status, created_at, "
                f"{_SCOPE_COLUMNS}) VALUES (:revision_id, :skill_id, :number, :skill_id, "
                ":source_kind, :digest, :digest, :frontmatter, :tasks, 'enabled', :created_at, "
                f"{_SCOPE_VALUES})"
            ),
            SCOPE
            | {
                "revision_id": revision_id,
                "skill_id": skill_id,
                "number": number,
                "source_kind": source_kind,
                "digest": DIGEST,
                "frontmatter": None if frontmatter is None else json.dumps(frontmatter),
                "tasks": json.dumps(["knowledge.answer"]),
                "created_at": created_at,
                "actor_id": actor_id,
            },
        )
    connection.execute(
        text(
            "INSERT INTO ai_agent (agent_id, display_name, created_at, owner_actor_id, "
            f"owner_assigned_at, {_SCOPE_COLUMNS}) VALUES ('agent-1', 'Agent', :created_at, "
            f":actor_id, :created_at, {_SCOPE_VALUES})"
        ),
        SCOPE | {"created_at": SKILL_CREATED},
    )
    for revision_id, number, bound in AGENT_REVISIONS:
        connection.execute(
            text(
                "INSERT INTO ai_agent_revision (revision_id, agent_id, revision_number, "
                "display_name, source_kind, content_digest, system_instruction_digest, "
                "tool_allowlist, output_schema_digest, skill_revision_ids, status, created_at, "
                f"{_SCOPE_COLUMNS}) VALUES (:revision_id, 'agent-1', :number, 'Agent', "
                "'authored', :digest, :digest, :tools, :digest, :bound, 'enabled', :created_at, "
                f"{_SCOPE_VALUES})"
            ),
            SCOPE
            | {
                "revision_id": revision_id,
                "number": number,
                "digest": DIGEST,
                "tools": json.dumps([]),
                "bound": None if bound is None else json.dumps(bound),
                "created_at": SKILL_CREATED,
            },
        )
    connection.execute(
        text(
            "INSERT INTO conversation (conversation_id, title, created_at, updated_at, "
            f"{_SCOPE_COLUMNS}) VALUES ('chat-1', 'Chat', :created_at, :created_at, "
            f"{_SCOPE_VALUES})"
        ),
        SCOPE | {"created_at": SKILL_CREATED},
    )
    for turn_id, created_at, facts in TURNS:
        connection.execute(
            text(
                "INSERT INTO chat_turn (turn_id, chat_id, client_request_id, message, state, "
                f"created_at, {_SCOPE_COLUMNS}) VALUES (:turn_id, 'chat-1', :turn_id, 'hi', "
                f"'completed', :created_at, {_SCOPE_VALUES})"
            ),
            SCOPE | {"turn_id": turn_id, "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO turn_input_snapshot (snapshot_id, turn_id, snapshot_digest, "
                f"snapshot, created_at, {_SCOPE_COLUMNS}) VALUES (:snapshot_id, :turn_id, "
                f":digest, :snapshot, :created_at, {_SCOPE_VALUES})"
            ),
            SCOPE
            | {
                "snapshot_id": f"snap-{turn_id}",
                "turn_id": turn_id,
                "digest": DIGEST,
                "snapshot": json.dumps({"message": "hi", **facts}),
                # Deliberately later than `chat_turn.created_at`: the backfill
                # must take the Turn's time, not the snapshot's.
                "created_at": datetime(2026, 10, 1, 0, 0, 0),
            },
        )
    for name in ("trace", "feedback"):
        connection.execute(
            text("INSERT INTO langfuse_sync_cursor (name, position) VALUES (:n, :t)"),
            {"n": name, "t": SKILL_CREATED},
        )


def _upgrade_seeded(owned_project_mysql):
    owned_project_mysql.rebuild(PREVIOUS)
    engine = create_engine(owned_project_mysql.url)
    with engine.begin() as connection:
        _seed(connection)
    owned_project_mysql.upgrade("head")
    return engine


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def test_skill_category_backfilled_from_latest_frontmatter_or_other(owned_project_mysql):
    engine = _upgrade_seeded(owned_project_mysql)
    try:
        columns = {c["name"]: c for c in inspect(engine).get_columns("skill")}
        assert columns["category"]["nullable"] is False
        assert columns["category"]["type"].length == 16
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT skill_id, category FROM skill")).all()
        categories = dict(rows)
        # s-cat's latest revision says automation (its first said writing);
        # `ops` is outside the closed set and a missing frontmatter has none.
        assert categories == {"s-cat": "automation", "s-other": "other", "s-built": "other"}
    finally:
        engine.dispose()


def test_skill_owner_defaults_to_creator(owned_project_mysql):
    engine = _upgrade_seeded(owned_project_mysql)
    try:
        inspector = inspect(engine)
        columns = {c["name"]: c for c in inspector.get_columns("skill")}
        assert columns["owner_actor_id"]["nullable"] is False
        assert columns["owner_assigned_at"]["nullable"] is False
        assert "fk_skill_owner_actor" in {fk["name"] for fk in inspector.get_foreign_keys("skill")}
        with engine.connect() as connection:
            rows = {
                row.skill_id: row
                for row in connection.execute(
                    text("SELECT skill_id, owner_actor_id, owner_assigned_at FROM skill")
                )
            }
        # The first revision's author owns the Skill, not the latest reviser.
        assert rows["s-cat"].owner_actor_id == AUTHOR
        assert rows["s-other"].owner_actor_id == VALIDATION_SCOPE.actor_id
        assert {row.owner_assigned_at for row in rows.values()} == {SKILL_CREATED}
    finally:
        engine.dispose()


def test_imported_revision_origin_is_unknown_and_authored_keeps_kind(owned_project_mysql):
    engine = _upgrade_seeded(owned_project_mysql)
    try:
        columns = {c["name"]: c for c in inspect(engine).get_columns("skill_revision")}
        assert columns["origin_kind"]["nullable"] is False
        assert columns["version_label"]["nullable"] is True
        assert columns["version_label"]["type"].length == 32
        assert columns["origin_ref"]["type"].length == 512
        with engine.connect() as connection:
            rows = {
                row.revision_id: row
                for row in connection.execute(
                    text(
                        "SELECT revision_id, version_label, origin_kind, origin_ref, "
                        "origin_imported_at, created_at FROM skill_revision"
                    )
                )
            }
        assert rows["r1"].origin_kind == "unknown"
        assert rows["r1"].origin_imported_at == rows["r1"].created_at
        assert rows["r2"].origin_kind == "unknown"
        assert rows["r3"].origin_kind == "authored"
        assert rows["r3"].origin_imported_at is None
        assert rows["r4"].origin_kind == "built_in"
        assert {row.origin_ref for row in rows.values()} == {None}
        # Only a SemVer `metadata.version` is kept.
        assert rows["r1"].version_label == "1.0.0"
        assert rows["r2"].version_label is None
        assert rows["r4"].version_label is None
    finally:
        engine.dispose()


def test_bound_revisions_become_pinned_skill_ids(owned_project_mysql):
    engine = _upgrade_seeded(owned_project_mysql)
    try:
        with engine.connect() as connection:
            rows = {
                row.revision_id: row
                for row in connection.execute(
                    text(
                        "SELECT revision_id, skill_revision_ids, pinned_skill_ids, "
                        "excluded_skill_ids FROM ai_agent_revision"
                    )
                )
            }
        assert _json(rows["ar1"].pinned_skill_ids) == ["s-other", "s-cat"]
        assert _json(rows["ar1"].excluded_skill_ids) == []
        assert _json(rows["ar1"].skill_revision_ids) == ["r3", "r1", "r2", "r-missing"]
        assert _json(rows["ar2"].pinned_skill_ids) == []
        assert _json(rows["ar2"].excluded_skill_ids) == []
        assert rows["ar2"].skill_revision_ids is None
    finally:
        engine.dispose()


def test_turn_skill_activation_backfilled_from_snapshots(owned_project_mysql):
    engine = _upgrade_seeded(owned_project_mysql)
    try:
        inspector = inspect(engine)
        uniques = {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_unique_constraints("turn_skill_activation")
        }
        assert uniques["uq_turn_skill_activation_turn_skill"] == (
            "project_id",
            "turn_id",
            "skill_id",
        )
        indexes = {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_indexes("turn_skill_activation")
        }
        assert indexes["ix_turn_skill_activation_project_skill_activated"] == (
            "project_id",
            "skill_id",
            "activated_at",
        )
        assert "fk_turn_skill_activation_project_parent_0" in {
            fk["name"] for fk in inspector.get_foreign_keys("turn_skill_activation")
        }
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT activation_id, turn_id, skill_id, skill_revision_id, mode, "
                    "activated_at, bytes, project_id, actor_id FROM turn_skill_activation"
                )
            ).all()
        turn_times = {turn_id: created_at for turn_id, created_at, _ in TURNS}
        assert {
            (row.turn_id, row.skill_id, row.skill_revision_id, row.mode, row.bytes) for row in rows
        } == {
            ("t1", "s-other", "r3", "pinned", 2),
            ("t1", "s-cat", "r1", "manual", 6),
            ("t2", "s-cat", "r1", "pinned", 3),
            ("t2", "s-built", "r4", "manual", 1),
            ("t3", "s-cat", "r2", "manual", 4),
        }
        for row in rows:
            assert row.activated_at == turn_times[row.turn_id]
            assert row.activation_id == activation_id_for(row.turn_id, row.skill_id)
            assert row.project_id == VALIDATION_SCOPE.project_id
            assert row.actor_id == VALIDATION_SCOPE.actor_id
    finally:
        engine.dispose()


def test_trace_cursor_deleted_feedback_cursor_kept(owned_project_mysql):
    engine = _upgrade_seeded(owned_project_mysql)
    try:
        assert "skill_maintenance_event" in inspect(engine).get_table_names()
        indexes = {
            item["name"]: tuple(item["column_names"])
            for item in inspect(engine).get_indexes("skill_maintenance_event")
        }
        assert indexes["ix_skill_maintenance_event_project_skill_occurred"] == (
            "project_id",
            "skill_id",
            "occurred_at",
        )
        assert "fk_skill_maintenance_event_project_parent_0" in {
            fk["name"] for fk in inspect(engine).get_foreign_keys("skill_maintenance_event")
        }
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT name FROM langfuse_sync_cursor")).all()
        names = {row.name for row in rows}
        assert names == {"feedback"}
    finally:
        engine.dispose()


def test_downgrade_drops_new_schema(owned_project_mysql):
    engine = _upgrade_seeded(owned_project_mysql)
    try:
        owned_project_mysql.downgrade(PREVIOUS)
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert "turn_skill_activation" not in tables
        assert "skill_maintenance_event" not in tables
        skill_columns = {c["name"] for c in inspector.get_columns("skill")}
        assert not skill_columns & {"category", "owner_actor_id", "owner_assigned_at"}
        # MySQL keeps the FK's backing index unless downgrade drops it too.
        assert "fk_skill_owner_actor" not in {i["name"] for i in inspector.get_indexes("skill")}
        revision_columns = {c["name"] for c in inspector.get_columns("skill_revision")}
        assert not revision_columns & {
            "version_label",
            "origin_kind",
            "origin_ref",
            "origin_imported_at",
        }
        agent_columns = {c["name"] for c in inspector.get_columns("ai_agent_revision")}
        assert not agent_columns & {"pinned_skill_ids", "excluded_skill_ids"}
        assert "skill_revision_ids" in agent_columns

        # Round trip: re-upgrading the (still populated) schema succeeds.
        owned_project_mysql.upgrade("head")
        assert "turn_skill_activation" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
