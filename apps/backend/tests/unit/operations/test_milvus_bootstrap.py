from __future__ import annotations

import pytest
from pydantic import SecretStr

from tap.operations.milvus.bootstrap import bootstrap_local_rbac
from tap.operations.milvus.client import connect_local_admin
from tap.operations.milvus.contracts import (
    PROVISIONER_PRIVILEGES,
    READER_PRIVILEGES,
    WRITER_PRIVILEGES,
    MilvusGrant,
    MilvusRoleCredentials,
)

pytestmark = pytest.mark.asyncio


class RecordingAdmin:
    def __init__(self) -> None:
        self.users: dict[str, str] = {}
        self.roles: set[str] = set()
        self.grants: dict[str, frozenset[MilvusGrant]] = {}
        self.password_rotations: list[tuple[str, str]] = []

    async def ensure_user(self, username: str, password: SecretStr) -> None:
        self.users[username] = password.get_secret_value()

    async def ensure_role(self, role_name: str) -> None:
        self.roles.add(role_name)

    async def replace_role_grants(
        self,
        role_name: str,
        grants: frozenset[MilvusGrant],
    ) -> None:
        self.grants[role_name] = grants

    async def rotate_root_password(self, password: SecretStr) -> None:
        self.password_rotations.append(("root", password.get_secret_value()))

    def role_privileges(self, role_name: str) -> set[str]:
        return {grant.privilege for grant in self.grants[role_name]}


def local_role_credentials() -> MilvusRoleCredentials:
    return MilvusRoleCredentials(
        rotated_root_password=SecretStr("tap-local-rotated-root"),
        reader_username="tap_reader",
        reader_password=SecretStr("tap-local-reader-password"),
        writer_username="tap_writer",
        writer_password=SecretStr("tap-local-writer-password"),
        provisioner_username="tap_provisioner",
        provisioner_password=SecretStr("tap-local-provisioner-password"),
    )


async def test_bootstrap_replaces_three_non_overlapping_least_privilege_roles() -> None:
    admin = RecordingAdmin()

    await bootstrap_local_rbac(admin, local_role_credentials())

    assert set(admin.users) == {"tap_reader", "tap_writer", "tap_provisioner"}
    assert admin.roles == {"tap_reader", "tap_writer", "tap_provisioner"}
    assert admin.role_privileges("tap_reader") == set(READER_PRIVILEGES)
    assert admin.role_privileges("tap_writer") == set(WRITER_PRIVILEGES)
    assert admin.role_privileges("tap_provisioner") == set(PROVISIONER_PRIVILEGES)
    assert "Search" not in admin.role_privileges("tap_writer")
    assert "Insert" not in admin.role_privileges("tap_reader")
    assert not (READER_PRIVILEGES & WRITER_PRIVILEGES)
    assert not (WRITER_PRIVILEGES & PROVISIONER_PRIVILEGES)
    assert admin.password_rotations == [("root", "tap-local-rotated-root")]


async def test_bootstrap_second_run_converges_to_the_same_final_grants() -> None:
    admin = RecordingAdmin()
    credentials = local_role_credentials()

    await bootstrap_local_rbac(admin, credentials)
    first_grants = dict(admin.grants)
    admin.grants["tap_reader"] = frozenset({MilvusGrant("collection", "*", "Insert")})

    await bootstrap_local_rbac(admin, credentials)

    assert admin.grants == first_grants
    assert admin.password_rotations == [
        ("root", "tap-local-rotated-root"),
        ("root", "tap-local-rotated-root"),
    ]


class AuthenticationClient:
    def __init__(self, accepted_password: str, password: str) -> None:
        self.accepted_password = accepted_password
        self.password = password
        self.closed = False

    def list_users(self, **kwargs: object) -> list[str]:
        if self.password != self.accepted_password:
            raise RuntimeError("authentication failed with a secret detail")
        return ["root"]

    def close(self) -> None:
        self.closed = True


async def test_admin_connection_uses_initial_root_only_with_explicit_opt_in() -> None:
    attempted_passwords: list[str] = []

    def factory(**kwargs: object) -> AuthenticationClient:
        password = kwargs["password"]
        assert isinstance(password, str)
        attempted_passwords.append(password)
        return AuthenticationClient("initial-root-secret", password)

    admin = await connect_local_admin(
        {
            "MILVUS_URI": "http://127.0.0.1:19530",
            "MILVUS_DATABASE": "default",
            "MILVUS_ROOT_PASSWORD": "tap-local-rotated-root",
            "MILVUS_INITIAL_ROOT_PASSWORD": "initial-root-secret",
            "TAP_ALLOW_INITIAL_MILVUS_ROOT": "1",
        },
        client_factory=factory,
    )

    assert attempted_passwords == ["tap-local-rotated-root", "initial-root-secret"]
    assert admin.authenticated_with_initial_root is True


async def test_admin_connection_refuses_implicit_initial_root_fallback() -> None:
    attempted_passwords: list[str] = []

    def factory(**kwargs: object) -> AuthenticationClient:
        password = kwargs["password"]
        assert isinstance(password, str)
        attempted_passwords.append(password)
        return AuthenticationClient("initial-root-secret", password)

    with pytest.raises(RuntimeError, match="rotated root authentication failed") as captured:
        await connect_local_admin(
            {
                "MILVUS_URI": "http://127.0.0.1:19530",
                "MILVUS_DATABASE": "default",
                "MILVUS_ROOT_PASSWORD": "tap-local-rotated-root",
                "MILVUS_INITIAL_ROOT_PASSWORD": "initial-root-secret",
                "TAP_ALLOW_INITIAL_MILVUS_ROOT": "0",
            },
            client_factory=factory,
        )

    assert attempted_passwords == ["tap-local-rotated-root"]
    assert "initial-root-secret" not in str(captured.value)
