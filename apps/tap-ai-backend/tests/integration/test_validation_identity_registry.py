"""Validation identity is read from the migrated registry, never client assertions."""

import os
from collections.abc import Iterator
from contextlib import ExitStack

import pytest


def test_identity_metadata_has_registry_relations() -> None:
    from sqlalchemy import UniqueConstraint

    from tap.platform.db.registry import load_authoritative_metadata

    tables = load_authoritative_metadata().tables
    assert {"enterprise", "project", "actor_principal"} <= set(tables)
    for name, columns in [
        ("project", ("enterprise_id", "project_id")),
        ("actor_principal", ("enterprise_id", "actor_id")),
    ]:
        assert any(
            isinstance(constraint, UniqueConstraint) and tuple(constraint.columns.keys()) == columns
            for constraint in tables[name].constraints
        )
    assert {fk.target_fullname for fk in tables["project"].foreign_keys} == {
        "enterprise.enterprise_id"
    }
    assert {fk.target_fullname for fk in tables["actor_principal"].foreign_keys} == {
        "enterprise.enterprise_id"
    }


@pytest.fixture
def identity_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Own fresh identity data; never mutate a caller's registry or reset its state."""
    if os.getenv("TAP_RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires owned isolated MySQL")
    from scripts.migration_support import isolated_mysql

    with ExitStack() as resources:
        with monkeypatch.context() as environment:
            environment.delenv("TAP_DATABASE_URL", raising=False)
            environment.delenv("TAP_ALEMBIC_DATABASE_URL", raising=False)
            database = resources.enter_context(isolated_mysql())
            database.upgrade("0006_validation_identity")
        yield database.url.replace("mysql+pymysql:", "mysql+asyncmy:")


@pytest.mark.parametrize("disabled_table", ["enterprise", "project", "actor_principal"])
@pytest.mark.asyncio
async def test_identity_mysql_registry_observes_disabled_and_unknown_principals(
    disabled_table: str,
    identity_database: str,
) -> None:
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tap.modules.access.adapters.mysql import (
        MysqlIdentityRegistry,
        actor_principal,
        enterprise,
        project,
    )
    from tap.modules.access.adapters.validation import (
        ValidationAuthorizationPolicy,
        ValidationScopeProvider,
    )
    from tap.modules.access.application.scope import RequestFacts
    from tap.modules.access.domain.authorization import ResourceRef

    engine = create_async_engine(identity_database)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    registry = MysqlIdentityRegistry(sessions)
    table, identity_column, identity = {
        "enterprise": (enterprise, enterprise.c.enterprise_id, "local"),
        "project": (project, project.c.project_id, "tapper-demo"),
        "actor_principal": (actor_principal, actor_principal.c.actor_id, "tapper-local-user"),
    }[disabled_table]
    try:
        principal = await registry.get_principal("local", "tapper-demo", "tapper-local-user")
        assert (
            principal is not None and principal.enabled and principal.principal_type == "VALIDATION"
        )
        for enterprise_id, project_id, actor_id in [
            ("other", "tapper-demo", "tapper-local-user"),
            ("local", "other", "tapper-local-user"),
            ("local", "tapper-demo", "other"),
        ]:
            assert await registry.get_principal(enterprise_id, project_id, actor_id) is None
        scope = await ValidationScopeProvider().current(RequestFacts())
        resource = ResourceRef(enterprise_id="local", project_id="tapper-demo", kind="knowledge")
        policy = ValidationAuthorizationPolicy(registry)
        assert (await policy.authorize(scope, "knowledge.read", resource)).allowed
        async with sessions.begin() as session:
            await session.execute(
                update(table).where(identity_column == identity).values(enabled=False)
            )
        assert not (await policy.authorize(scope, "knowledge.read", resource)).allowed
    finally:
        await engine.dispose()
