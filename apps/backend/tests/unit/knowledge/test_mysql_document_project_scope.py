from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.access.domain.context import IdentityMode, ProjectScopeContext
from tap.modules.knowledge.adapters.mysql_documents import (
    MysqlDocumentRepository,
    _decode_cursor,
    _encode_cursor,
)
from tap.modules.knowledge.ports.documents import InvalidDocumentCursor

OTHER_SCOPE = ProjectScopeContext(
    enterprise_id="local",
    project_id="other-project",
    actor_id="tapper-local-user",
    identity_mode=IdentityMode.VALIDATION,
)


def test_document_project_scope_is_required_before_connection() -> None:
    with pytest.raises(TypeError):
        MysqlDocumentRepository(async_sessionmaker())  # type: ignore[call-arg]


def test_document_project_scope_binds_repository_and_retention_lock() -> None:
    first = MysqlDocumentRepository(async_sessionmaker(), scope=VALIDATION_SCOPE)
    second = MysqlDocumentRepository(async_sessionmaker(), scope=OTHER_SCOPE)
    assert first.scope is VALIDATION_SCOPE
    assert first._answer_snapshot_lock_name != second._answer_snapshot_lock_name
    assert len(first._answer_snapshot_lock_name) <= 64


def test_document_project_cursor_cannot_replay_in_another_project() -> None:
    created_at = datetime(2026, 9, 5)
    document_id = "doc_" + "a" * 32
    cursor = _encode_cursor(created_at, document_id, VALIDATION_SCOPE)
    assert _decode_cursor(cursor, VALIDATION_SCOPE) == (created_at, document_id)
    with pytest.raises(InvalidDocumentCursor):
        _decode_cursor(cursor, OTHER_SCOPE)
