"""Workflow migration keeps old graph data and protects new business records."""

from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect, text

from tap.modules.access.adapters.validation import VALIDATION_SCOPE


def test_empty_downgrade_preserves_graph_and_populated_downgrade_refuses(
    owned_project_mysql,
) -> None:
    engine = create_engine(owned_project_mysql.url)
    scope = {
        "enterprise_id": VALIDATION_SCOPE.enterprise_id,
        "project_id": VALIDATION_SCOPE.project_id,
        "actor_id": VALIDATION_SCOPE.actor_id,
        "identity_mode": VALIDATION_SCOPE.identity_mode.value,
        "identity_origin": VALIDATION_SCOPE.identity_mode.value.upper(),
    }
    now = datetime(2026, 11, 6)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ai_graph_run (run_id, graph_version, state_schema_version, "
                    "execution_mode, reasoning_mode, status, budget, created_at, updated_at, "
                    "enterprise_id, project_id, actor_id, identity_mode, identity_origin) "
                    "VALUES ('old-graph', 'tapper-chat-v2', 1, 'chat', 'direct', 'queued', "
                    "'{}', :now, :now, :enterprise_id, :project_id, :actor_id, "
                    ":identity_mode, :identity_origin)"
                ),
                {**scope, "now": now},
            )
        owned_project_mysql.downgrade("0038_skill_hub")
        assert "workflow_task" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM ai_graph_run WHERE run_id='old-graph'")
                )
                == 1
            )
        owned_project_mysql.upgrade("head")
        assert "workflow_artifact" in inspect(engine).get_table_names()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO workflow_task (task_id, idempotency_key, request_digest, "
                    "request_json, goal, status, plan_revision, next_sequence, budget_limit, "
                    "budget_used, created_at, updated_at, enterprise_id, project_id, actor_id, "
                    "identity_mode, identity_origin) VALUES ('new-task', 'new-task', 'sha256:0', "
                    "'{}', 'new work', 'queued', 1, 1, '{}', '{}', :now, :now, "
                    ":enterprise_id, :project_id, :actor_id, :identity_mode, :identity_origin)"
                ),
                {**scope, "now": now},
            )
        # The fixture sanitizes Alembic stderr; verify the refusal by the
        # unchanged live schema and records below.
        with pytest.raises(RuntimeError, match="local gate command failed"):
            owned_project_mysql.downgrade("0038_skill_hub")
        assert "workflow_task" in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM ai_graph_run WHERE run_id='old-graph'")
                )
                == 1
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM workflow_task WHERE task_id='new-task'")
                )
                == 1
            )
    finally:
        engine.dispose()


def test_bridge_downgrade_refuses_workflow_owner_or_task_records(
    owned_project_mysql,
) -> None:
    engine = create_engine(owned_project_mysql.url)
    scope = {
        "enterprise_id": VALIDATION_SCOPE.enterprise_id,
        "project_id": VALIDATION_SCOPE.project_id,
        "actor_id": VALIDATION_SCOPE.actor_id,
        "identity_mode": VALIDATION_SCOPE.identity_mode.value,
        "identity_origin": VALIDATION_SCOPE.identity_mode.value.upper(),
    }
    now = datetime(2026, 11, 6)
    try:
        owned_project_mysql.downgrade("0039_agent_workflows")
        assert "execution_owner" not in {
            column["name"] for column in inspect(engine).get_columns("chat_turn")
        }
        owned_project_mysql.upgrade("head")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO conversation (conversation_id, title, created_at, updated_at, "
                    "enterprise_id, project_id, actor_id, identity_mode, identity_origin) "
                    "VALUES ('conversation-1', 'Review', :now, :now, :enterprise_id, "
                    ":project_id, :actor_id, :identity_mode, :identity_origin)"
                ),
                {**scope, "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO chat_turn (turn_id, chat_id, client_request_id, message, state, "
                    "execution_owner, created_at, enterprise_id, project_id, actor_id, "
                    "identity_mode, identity_origin) VALUES "
                    "('pending-owner', 'conversation-1', 'request-1', 'Review', 'queued', "
                    "'workflow', :now, :enterprise_id, :project_id, :actor_id, "
                    ":identity_mode, :identity_origin)"
                ),
                {**scope, "now": now},
            )
        with pytest.raises(RuntimeError, match="local gate command failed"):
            owned_project_mysql.downgrade("0039_agent_workflows")
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM chat_turn WHERE turn_id='pending-owner'"))
            connection.execute(
                text(
                    "INSERT INTO workflow_task (task_id, idempotency_key, request_digest, "
                    "request_json, goal, status, plan_revision, next_sequence, budget_limit, "
                    "budget_used, created_at, updated_at, enterprise_id, project_id, actor_id, "
                    "identity_mode, identity_origin) VALUES "
                    "('pending-admission', 'pending-admission', 'sha256:0', '{}', "
                    "'Review', 'queued', 1, 1, '{}', '{}', :now, :now, "
                    ":enterprise_id, :project_id, :actor_id, :identity_mode, :identity_origin)"
                ),
                {**scope, "now": now},
            )
        with pytest.raises(RuntimeError, match="local gate command failed"):
            owned_project_mysql.downgrade("0039_agent_workflows")
        assert "admission_ready" in {
            column["name"] for column in inspect(engine).get_columns("workflow_task")
        }
    finally:
        engine.dispose()
