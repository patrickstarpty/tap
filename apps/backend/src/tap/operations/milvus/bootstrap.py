"""Idempotent three-role local Milvus bootstrap orchestration."""

from __future__ import annotations

from tap.operations.milvus.contracts import (
    PROVISIONER_BASE_GRANTS,
    READER_BASE_GRANTS,
    WRITER_BASE_GRANTS,
    MilvusAdmin,
    MilvusRoleCredentials,
)

_ROLE_GRANTS = (
    ("tap_reader", READER_BASE_GRANTS),
    ("tap_writer", WRITER_BASE_GRANTS),
    ("tap_provisioner", PROVISIONER_BASE_GRANTS),
)


async def bootstrap_local_rbac(
    admin: MilvusAdmin,
    credentials: MilvusRoleCredentials,
) -> None:
    """Create or rotate the three local identities and replace their grants."""
    users = (
        ("tap_reader", credentials.reader_username, credentials.reader_password),
        ("tap_writer", credentials.writer_username, credentials.writer_password),
        (
            "tap_provisioner",
            credentials.provisioner_username,
            credentials.provisioner_password,
        ),
    )
    for role_name, username, password in users:
        await admin.ensure_user(username, password)
        await admin.ensure_role(role_name)
        await admin.replace_user_roles(username, frozenset({role_name}))

    for role_name, grants in _ROLE_GRANTS:
        await admin.replace_role_grants(role_name, grants)

    await admin.rotate_root_password(credentials.rotated_root_password)
