"""Idempotent three-role local Milvus bootstrap orchestration."""

from __future__ import annotations

from tap.operations.milvus.contracts import (
    PROVISIONER_PRIVILEGES,
    READER_PRIVILEGES,
    WRITER_PRIVILEGES,
    MilvusAdmin,
    MilvusGrant,
    MilvusRoleCredentials,
)

_ROLE_PRIVILEGES = (
    ("tap_reader", READER_PRIVILEGES),
    ("tap_writer", WRITER_PRIVILEGES),
    ("tap_provisioner", PROVISIONER_PRIVILEGES),
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

    for role_name, privileges in _ROLE_PRIVILEGES:
        await admin.replace_role_grants(
            role_name,
            frozenset(
                MilvusGrant(
                    resource_level="collection",
                    resource_name="*",
                    privilege=privilege,
                )
                for privilege in privileges
            ),
        )

    await admin.rotate_root_password(credentials.rotated_root_password)
