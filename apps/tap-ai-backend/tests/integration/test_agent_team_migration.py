"""`0036_agent_team` migration: `ai_agent` owner backfill, `answer_feedback`
resolution columns, the new `agent_maintenance_event` table, and the
`langfuse_sync_cursor` reset for `trace` (Task 1, 2026-10-23 agent team
view)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import create_engine, inspect, text

from tap.modules.access.adapters.validation import VALIDATION_SCOPE

LEGACY_TIME = datetime(2026, 9, 1, 12, 0, 0)


def test_0036_backfills_agent_owner_and_adds_feedback_resolution_and_event_table(
    owned_project_mysql,
):
    owned_project_mysql.rebuild("0035_skills_agents_authoring")
    sync_engine = create_engine(owned_project_mysql.url)
    scope = {
        "enterprise_id": VALIDATION_SCOPE.enterprise_id,
        "project_id": VALIDATION_SCOPE.project_id,
        "actor_id": VALIDATION_SCOPE.actor_id,
        "identity_mode": VALIDATION_SCOPE.identity_mode.value,
        "identity_origin": VALIDATION_SCOPE.identity_mode.value.upper(),
    }
    try:
        with sync_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ai_agent (agent_id, display_name, created_at, "
                    "enterprise_id, project_id, actor_id, identity_mode, identity_origin) "
                    "VALUES ('legacy-agent', 'Legacy Agent', :created_at, :enterprise_id, "
                    ":project_id, :actor_id, :identity_mode, :identity_origin)"
                ),
                scope | {"created_at": LEGACY_TIME},
            )
            connection.execute(
                text("INSERT INTO langfuse_sync_cursor (name, position) VALUES ('trace', :t)"),
                {"t": LEGACY_TIME},
            )
            connection.execute(
                text("INSERT INTO langfuse_sync_cursor (name, position) VALUES ('feedback', :t)"),
                {"t": LEGACY_TIME},
            )

        owned_project_mysql.upgrade("head")

        inspector = inspect(sync_engine)
        ai_agent_columns = {c["name"]: c for c in inspector.get_columns("ai_agent")}
        assert ai_agent_columns["job_title"]["nullable"] is True
        assert ai_agent_columns["job_title"]["type"].length == 64
        assert ai_agent_columns["owner_actor_id"]["nullable"] is False
        assert ai_agent_columns["owner_assigned_at"]["nullable"] is False

        answer_feedback_columns = {c["name"]: c for c in inspector.get_columns("answer_feedback")}
        assert answer_feedback_columns["resolved_at"]["nullable"] is True
        assert answer_feedback_columns["resolved_by"]["nullable"] is True
        assert answer_feedback_columns["resolution_note"]["nullable"] is True
        assert answer_feedback_columns["resolution_note"]["type"].length == 500

        assert "agent_maintenance_event" in inspector.get_table_names()
        event_indexes = {
            index["name"]: tuple(index["column_names"])
            for index in inspector.get_indexes("agent_maintenance_event")
        }
        assert event_indexes["ix_agent_maintenance_event_project_agent_occurred"] == (
            "project_id",
            "agent_id",
            "occurred_at",
        )
        assert event_indexes["ix_agent_maintenance_event_project_occurred"] == (
            "project_id",
            "occurred_at",
        )

        with sync_engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT owner_actor_id, owner_assigned_at, actor_id, created_at "
                    "FROM ai_agent WHERE agent_id = 'legacy-agent'"
                )
            ).one()
            assert row.owner_actor_id == row.actor_id
            assert row.owner_assigned_at == row.created_at

            cursor_names = {
                r.name for r in connection.execute(text("SELECT name FROM langfuse_sync_cursor"))
            }
            assert cursor_names == {"feedback"}
    finally:
        sync_engine.dispose()


def test_0036_downgrade_removes_added_columns_and_event_table(owned_project_mysql):
    owned_project_mysql.rebuild("0035_skills_agents_authoring")
    sync_engine = create_engine(owned_project_mysql.url)
    try:
        owned_project_mysql.upgrade("head")
        owned_project_mysql.downgrade("0035_skills_agents_authoring")
        inspector = inspect(sync_engine)
        assert "agent_maintenance_event" not in inspector.get_table_names()
        ai_agent_columns = {c["name"] for c in inspector.get_columns("ai_agent")}
        assert "job_title" not in ai_agent_columns
        assert "owner_actor_id" not in ai_agent_columns
        assert "owner_assigned_at" not in ai_agent_columns
        answer_feedback_columns = {c["name"] for c in inspector.get_columns("answer_feedback")}
        assert "resolved_at" not in answer_feedback_columns
        assert "resolved_by" not in answer_feedback_columns
        assert "resolution_note" not in answer_feedback_columns
    finally:
        sync_engine.dispose()


def test_0037_renames_the_agent_fk_to_the_metadata_name_and_back(owned_project_mysql):
    """Not run locally (needs isolated MySQL)."""
    sync_engine = create_engine(owned_project_mysql.url)
    try:
        owned_project_mysql.upgrade("head")

        def fk_names() -> set[str]:
            return {
                fk["name"]
                for fk in inspect(sync_engine).get_foreign_keys("agent_maintenance_event")
            }

        assert "fk_agent_maintenance_event_project_parent_0" in fk_names()
        assert "fk_agent_maintenance_event_agent" not in fk_names()

        owned_project_mysql.downgrade("0036_agent_team")
        assert "fk_agent_maintenance_event_agent" in fk_names()
        assert "fk_agent_maintenance_event_project_parent_0" not in fk_names()
    finally:
        sync_engine.dispose()
