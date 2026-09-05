"""Audit accepts only immutable, bounded project facts before any SQL."""

import asyncio
from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.context import AnonymousContext, IdentityMode


def audit_types():
    from tap.modules.governance.domain.audit import (
        AuditAction,
        AuditOutcome,
        AuditResource,
        SafeAuditMetadata,
    )

    return AuditAction, AuditResource, AuditOutcome, SafeAuditMetadata


def test_audit_metadata_copies_and_freezes_valid_values() -> None:
    _, _, _, metadata_type = audit_types()
    values = {"processed_count": 2, "mode": "apply", "content_digest": "a" * 64}
    metadata = metadata_type(values)
    values["processed_count"] = 999
    assert dict(metadata.values) == {
        "processed_count": 2,
        "mode": "apply",
        "content_digest": "a" * 64,
    }
    with pytest.raises(TypeError):
        metadata.values["processed_count"] = 3
    with pytest.raises(FrozenInstanceError):
        metadata.values = {}


@pytest.mark.parametrize(
    "values",
    [
        {"query": "private text"},
        {"prompt": "private prompt"},
        {"body": "document"},
        {"secret": "credential"},
        {"object_key": "blob://private"},
        {"provider_payload": {"message": "private"}},
        {"processed_count": "private"},
        {"processed_count": True},
        {"processed_count": -1},
        {"processed_count": 1_000_001},
        {"mode": "secret-value"},
        {"content_digest": "blob://secret"},
        {"content_digest": "A" * 64},
        {"unknown_count": 1},
        {"mode": "apply" * 1000},
        {1: 1},
        [],
    ],
)
def test_audit_metadata_rejects_free_text_unknown_keys_and_unbounded_values(values) -> None:
    _, _, _, metadata_type = audit_types()
    with pytest.raises((ValueError, TypeError)):
        metadata_type(values)


def test_audit_metadata_rejects_oversized_mapping_before_copy() -> None:
    from collections.abc import Mapping

    _, _, _, metadata_type = audit_types()

    class Oversized(Mapping):
        def __len__(self):
            return 10000

        def __iter__(self):
            raise AssertionError("must reject size before copying input")

        def __getitem__(self, key):
            raise AssertionError("must reject size before reading values")

    with pytest.raises(ValueError, match="bounded"):
        metadata_type(Oversized())


def test_audit_scope_binding_and_validation_precede_sql() -> None:
    from tap.modules.governance.adapters.mysql_audit import MysqlProjectAudit

    action, resource, outcome, metadata = audit_types()

    class NoSql:
        def in_transaction(self):
            return True

        async def execute(self, *args, **kwargs):
            raise AssertionError("invalid facts must never reach SQL")

    connection = cast(AsyncConnection, NoSql())
    with pytest.raises(TypeError, match="ProjectScopeContext"):
        MysqlProjectAudit(connection, scope=AnonymousContext(enterprise_id="local"))
    adapter = MysqlProjectAudit(connection, scope=VALIDATION_SCOPE)

    async def run():
        for scope in (
            AnonymousContext(enterprise_id="local"),
            replace(VALIDATION_SCOPE, project_id="another"),
            replace(VALIDATION_SCOPE, enterprise_id="another"),
            replace(VALIDATION_SCOPE, actor_id="another"),
            replace(VALIDATION_SCOPE, identity_mode=IdentityMode.PRODUCT),
        ):
            with pytest.raises((TypeError, ValueError), match="scope|ProjectScopeContext"):
                await adapter.append(
                    scope,
                    action.RECOVER_UPLOADS,
                    resource.PROJECT_MAINTENANCE,
                    outcome.COMPLETED,
                    metadata({}),
                    correlation_id="request-1",
                    idempotency_key="command-1",
                )
        for overrides in (
            {"action": "secret"},
            {"resource": "blob://secret"},
            {"outcome": "raw-error"},
            {"safe_metadata": {"query": "secret"}},
            {"correlation_id": ""},
            {"idempotency_key": ""},
            {"correlation_id": "x" * 129},
            {"idempotency_key": "x" * 129},
        ):
            kwargs = dict(
                scope=VALIDATION_SCOPE,
                action=action.RECOVER_UPLOADS,
                resource=resource.PROJECT_MAINTENANCE,
                outcome=outcome.COMPLETED,
                safe_metadata=metadata({}),
                correlation_id="request-1",
                idempotency_key="command-1",
            )
            with pytest.raises((ValueError, TypeError)):
                await adapter.append(**{**kwargs, **overrides})

    asyncio.run(run())


def test_audit_domain_requires_actual_project_and_nonempty_actor() -> None:
    from tap.modules.governance.domain.audit import require_audit_scope

    for field in ("enterprise_id", "project_id", "actor_id"):
        scope = replace(VALIDATION_SCOPE)
        object.__setattr__(scope, field, "")
        with pytest.raises(ValueError):
            require_audit_scope(scope)
    with pytest.raises(TypeError):
        require_audit_scope(AnonymousContext(enterprise_id="local"))
