"""New destructive isolation scenarios reject unowned database receipts before SQL."""

from importlib import import_module

import pytest
from scripts.migration_support import IsolatedMysql


@pytest.mark.parametrize(
    "module_name,scenario_name",
    [
        (
            "test_turn_outbox",
            "test_project_scopes_isolate_turn_idempotency_events_and_outbox_leases",
        ),
        (
            "test_projection_coordinator",
            "test_project_scope_rejects_foreign_physical_alias_before_yield",
        ),
        (
            "test_projection_coordinator",
            "test_projection_fence_rejects_mismatched_document_on_first_insert",
        ),
    ],
)
@pytest.mark.parametrize(
    "url",
    [
        "mysql+asyncmy://unused:unused@127.0.0.1:3306/tap",
        "mysql+pymysql://unused:unused@127.0.0.1:23306/shared",
        "mysql+pymysql://unused:unused@remote.invalid:23306/tap_schema_0123456789ab",
        "mysql+pymysql://unused:unused@127.0.0.1:23306/tap_schema_ffffffffffff",
    ],
)
def test_new_project_scenarios_reject_unowned_databases_before_sql(
    monkeypatch: pytest.MonkeyPatch, module_name: str, scenario_name: str, url: str
) -> None:
    module = import_module(f"apps.backend.tests.integration.{module_name}")
    monkeypatch.setenv("TAP_DATABASE_URL", url)
    monkeypatch.setenv("TAP_ALEMBIC_DATABASE_URL", url)
    monkeypatch.setattr(module, "DATABASE_URL", url)

    def no_engine(*args: object, **kwargs: object) -> None:
        pytest.fail("unowned scenario reached the SQL engine factory")

    monkeypatch.setattr(module, "create_engine_and_session_factory", no_engine)
    receipt = IsolatedMysql(url=url, project="tap-schema-0123456789ab")
    with pytest.raises(ValueError, match="refusing"):
        getattr(module, scenario_name)(receipt)
