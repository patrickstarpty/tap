"""MysqlModelCallRecorder integration tests: content join, nulls, failure isolation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import sqlalchemy as sa
from scripts.migration_support import IsolatedMysql

from tap.modules.access.adapters import mysql as _access_mysql  # noqa: F401  (registers FK targets)
from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.mysql_model_calls import MysqlModelCallRecorder
from tap.modules.ai.domain.model_calls import ModelCallRecord
from tap.modules.ai.domain.models import ModelOperation
from tap.platform.db.session import create_engine_and_session_factory
from tap.platform.telemetry.schema import model_call, model_call_content
from tests.owned_mysql import owned_project_database_url


def _record(**overrides: object) -> ModelCallRecord:
    fields: dict[str, object] = {
        "call_id": uuid4().hex,
        "scope": VALIDATION_SCOPE,
        "trace_id": None,
        "span_id": None,
        "turn_id": "t1",
        "job_id": None,
        "operation": ModelOperation.CHAT,
        "model_name": "gpt-test",
        "upstream_model": "gpt-test-upstream",
        "provider": "openai",
        "input_tokens": 10,
        "output_tokens": 20,
        "cost_usd": Decimal("0.00012345"),
        "latency_ms": 250,
        "attempts": 1,
        "status": "ok",
        "error_code": None,
        "gateway_call_id": "gw-1",
        "provider_request_id": "req-1",
        "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
        "request_json": '{"messages": []}',
        "response_text": "hello",
        "reasoning_text": None,
    }
    fields.update(overrides)
    return ModelCallRecord(**fields)


def test_record_writes_metadata_and_content(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            recorder = MysqlModelCallRecorder(sessions)
            record = _record()
            await recorder.record(record)
            assert recorder.degraded() is False

            async with engine.connect() as connection:
                call_rows = (
                    (
                        await connection.execute(
                            sa.select(model_call).where(model_call.c.call_id == record.call_id)
                        )
                    )
                    .mappings()
                    .all()
                )
                content_rows = (
                    (
                        await connection.execute(
                            sa.select(model_call_content).where(
                                model_call_content.c.call_id == record.call_id
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
            assert len(call_rows) == 1
            assert len(content_rows) == 1
            assert call_rows[0]["cost_usd"] == Decimal("0.00012345")
            assert content_rows[0]["request_json"] == '{"messages": []}'
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_error_record_allows_null_usage_and_model(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            recorder = MysqlModelCallRecorder(sessions)
            record = _record(
                status="error",
                upstream_model=None,
                provider=None,
                input_tokens=None,
                output_tokens=None,
                cost_usd=None,
                error_code="upstream_timeout",
                response_text=None,
            )
            await recorder.record(record)
            assert recorder.degraded() is False

            async with engine.connect() as connection:
                call_rows = (
                    (
                        await connection.execute(
                            sa.select(model_call).where(model_call.c.call_id == record.call_id)
                        )
                    )
                    .mappings()
                    .all()
                )
            assert len(call_rows) == 1
            assert call_rows[0]["status"] == "error"
            assert call_rows[0]["upstream_model"] is None
            assert call_rows[0]["cost_usd"] is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_failure_is_swallowed_and_marks_degraded(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await connection.execute(sa.text("DROP TABLE model_call_content"))

            recorder = MysqlModelCallRecorder(sessions)
            record = _record()
            await recorder.record(record)
            assert recorder.degraded() is True

            async with engine.connect() as connection:
                call_rows = (
                    (
                        await connection.execute(
                            sa.select(model_call).where(model_call.c.call_id == record.call_id)
                        )
                    )
                    .mappings()
                    .all()
                )
            assert call_rows == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_success_after_failure_clears_degraded(owned_project_mysql: IsolatedMysql) -> None:
    async def scenario() -> None:
        engine, sessions = create_engine_and_session_factory(
            owned_project_database_url(owned_project_mysql)
        )
        try:
            async with engine.begin() as connection:
                await connection.execute(sa.text("DROP TABLE model_call_content"))

            recorder = MysqlModelCallRecorder(sessions)
            failing_record = _record()
            await recorder.record(failing_record)
            assert recorder.degraded() is True

            async with engine.begin() as connection:
                await connection.run_sync(
                    lambda sync_connection: model_call_content.create(sync_connection)
                )

            succeeding_record = _record()
            await recorder.record(succeeding_record)
            assert recorder.degraded() is False
        finally:
            await engine.dispose()

    asyncio.run(scenario())
