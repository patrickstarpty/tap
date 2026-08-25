from __future__ import annotations

import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import SecretStr

from tap.operations.milvus.bootstrap import bootstrap_local_rbac
from tap.operations.milvus.client import (
    MilvusSdk,
    PyMilvusAdmin,
    build_probe_clients,
    build_reader_client,
    connect_local_admin,
)
from tap.operations.milvus.client import (
    local_role_credentials as load_local_role_credentials,
)
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
        self.memberships: dict[str, frozenset[str]] = {}
        self.password_rotations: list[tuple[str, str]] = []

    async def ensure_user(self, username: str, password: SecretStr) -> None:
        self.users[username] = password.get_secret_value()

    async def ensure_role(self, role_name: str) -> None:
        self.roles.add(role_name)

    async def replace_user_roles(
        self,
        username: str,
        role_names: frozenset[str],
    ) -> None:
        self.memberships[username] = role_names

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
        rotated_root_password=SecretStr("tap-local-Root1!"),
        reader_username="tap_reader",
        reader_password=SecretStr("tap-local-Reader1!"),
        writer_username="tap_writer",
        writer_password=SecretStr("tap-local-Writer1!"),
        provisioner_username="tap_provisioner",
        provisioner_password=SecretStr("tap-local-Provisioner1!"),
    )


def _env_example() -> dict[str, str]:
    repository = Path(__file__).resolve().parents[5]
    return {
        key: value
        for line in (repository / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
        for key, separator, value in (line.partition("="),)
        if separator
    }


def _milvus_password_is_compliant(password: str) -> bool:
    character_classes = (
        any(character.islower() for character in password),
        any(character.isupper() for character in password),
        any(character.isdigit() for character in password),
        any(not character.isalnum() for character in password),
    )
    return len(password) >= 8 and sum(character_classes) >= 3


async def test_local_password_defaults_match_consumers_and_pinned_server_policy() -> None:
    environment = _env_example()
    credentials = load_local_role_credentials({})
    configured = {
        "MILVUS_ROOT_PASSWORD": credentials.rotated_root_password,
        "MILVUS_READER_PASSWORD": credentials.reader_password,
        "MILVUS_WRITER_PASSWORD": credentials.writer_password,
        "MILVUS_PROVISIONER_PASSWORD": credentials.provisioner_password,
    }
    for setting_name, secret in configured.items():
        password = secret.get_secret_value()
        if not secrets.compare_digest(password, environment[setting_name]):
            raise AssertionError(f"{setting_name} default is inconsistent")
        if not _milvus_password_is_compliant(password):
            raise AssertionError(f"{setting_name} default violates the pinned Milvus policy")

    connections: list[tuple[str, str]] = []

    def client_factory(**kwargs: object) -> object:
        username = kwargs.get("user")
        password = kwargs.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise AssertionError("Milvus client credentials are malformed")
        connections.append((username, password))
        return object()

    sdk = MilvusSdk(
        client_factory=client_factory,
        create_schema=lambda **kwargs: object(),
        function_factory=lambda **kwargs: object(),
        ann_search_request_factory=lambda **kwargs: object(),
        ranker_factory=object,
        varchar_type=object(),
        sparse_vector_type=object(),
        float_vector_type=object(),
        array_type=object(),
        int64_type=object(),
        bool_type=object(),
        bm25_function_type=object(),
        permission_error=RuntimeError,
    )
    build_probe_clients({}, sdk=sdk)
    build_reader_client({}, sdk=sdk)
    expected_connections = [
        (environment["MILVUS_PROVISIONER_USERNAME"], environment["MILVUS_PROVISIONER_PASSWORD"]),
        (environment["MILVUS_WRITER_USERNAME"], environment["MILVUS_WRITER_PASSWORD"]),
        (environment["MILVUS_READER_USERNAME"], environment["MILVUS_READER_PASSWORD"]),
        (environment["MILVUS_READER_USERNAME"], environment["MILVUS_READER_PASSWORD"]),
    ]
    if connections != expected_connections:
        raise AssertionError("Milvus role client defaults are inconsistent")

    minio_password = environment["MILVUS_MINIO_ROOT_PASSWORD"]
    if len(minio_password) < 8:
        raise AssertionError("MILVUS_MINIO_ROOT_PASSWORD violates the MinIO minimum length")


async def test_bootstrap_replaces_three_non_overlapping_least_privilege_roles() -> None:
    admin = RecordingAdmin()

    await bootstrap_local_rbac(admin, local_role_credentials())

    assert set(admin.users) == {"tap_reader", "tap_writer", "tap_provisioner"}
    assert admin.roles == {"tap_reader", "tap_writer", "tap_provisioner"}
    assert admin.memberships == {
        "tap_reader": frozenset({"tap_reader"}),
        "tap_writer": frozenset({"tap_writer"}),
        "tap_provisioner": frozenset({"tap_provisioner"}),
    }
    assert admin.role_privileges("tap_reader") == set(READER_PRIVILEGES)
    assert admin.role_privileges("tap_writer") == set(WRITER_PRIVILEGES)
    assert admin.role_privileges("tap_provisioner") == set(PROVISIONER_PRIVILEGES)
    assert "Search" not in admin.role_privileges("tap_writer")
    assert "Insert" not in admin.role_privileges("tap_reader")
    assert not (READER_PRIVILEGES & WRITER_PRIVILEGES)
    assert not (WRITER_PRIVILEGES & PROVISIONER_PRIVILEGES)
    assert admin.password_rotations == [("root", "tap-local-Root1!")]


async def test_bootstrap_second_run_converges_to_the_same_final_grants() -> None:
    admin = RecordingAdmin()
    credentials = local_role_credentials()

    await bootstrap_local_rbac(admin, credentials)
    first_grants = dict(admin.grants)
    admin.grants["tap_reader"] = frozenset({MilvusGrant("collection", "*", "Insert")})
    admin.memberships["tap_reader"] = frozenset({"tap_reader", "tap_writer"})
    admin.users["tap_reader"] = "stale-reader-password"

    await bootstrap_local_rbac(admin, credentials)

    assert admin.grants == first_grants
    assert admin.users["tap_reader"] == "tap-local-Reader1!"
    assert admin.memberships["tap_reader"] == frozenset({"tap_reader"})
    assert admin.password_rotations == [
        ("root", "tap-local-Root1!"),
        ("root", "tap-local-Root1!"),
    ]


class AuthenticationServer:
    def __init__(self, password: str, *, fail_rotation: bool = False) -> None:
        self.password = password
        self.fail_rotation = fail_rotation
        self.events: list[tuple[str, str]] = []


class AuthenticationClient:
    def __init__(self, server: AuthenticationServer, password: str) -> None:
        self.server = server
        self.password = password
        self.closed = False

    def list_users(self, **kwargs: object) -> list[str]:
        self.server.events.append(("authenticate", self.password))
        if self.password != self.server.password:
            raise RuntimeError("authentication failed with a secret detail")
        return ["root"]

    def update_password(
        self,
        user_name: str,
        old_password: str,
        new_password: str,
        **kwargs: object,
    ) -> None:
        if self.server.fail_rotation:
            raise RuntimeError("rotation failed with a secret detail")
        if self.password != self.server.password or old_password != self.server.password:
            raise RuntimeError("rotation authentication failed")
        self.server.events.append(("rotate", user_name))
        self.server.password = new_password

    def close(self) -> None:
        self.closed = True


async def test_admin_connection_uses_initial_root_only_with_explicit_opt_in() -> None:
    attempted_passwords: list[str] = []
    server = AuthenticationServer("initial-root-secret")

    def factory(**kwargs: object) -> AuthenticationClient:
        password = kwargs["password"]
        assert isinstance(password, str)
        attempted_passwords.append(password)
        return AuthenticationClient(server, password)

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

    assert attempted_passwords == [
        "tap-local-rotated-root",
        "initial-root-secret",
        "tap-local-rotated-root",
    ]
    assert server.events == [
        ("authenticate", "tap-local-rotated-root"),
        ("authenticate", "initial-root-secret"),
        ("rotate", "root"),
        ("authenticate", "tap-local-rotated-root"),
    ]
    assert admin.authenticated_with_initial_root is False


async def test_admin_connection_refuses_implicit_initial_root_fallback() -> None:
    attempted_passwords: list[str] = []
    server = AuthenticationServer("initial-root-secret")

    def factory(**kwargs: object) -> AuthenticationClient:
        password = kwargs["password"]
        assert isinstance(password, str)
        attempted_passwords.append(password)
        return AuthenticationClient(server, password)

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


async def test_admin_connection_stops_when_initial_root_rotation_fails() -> None:
    attempted_passwords: list[str] = []
    server = AuthenticationServer("initial-root-secret", fail_rotation=True)

    def factory(**kwargs: object) -> AuthenticationClient:
        password = kwargs["password"]
        assert isinstance(password, str)
        attempted_passwords.append(password)
        return AuthenticationClient(server, password)

    with pytest.raises(RuntimeError, match="initial root rotation failed") as captured:
        await connect_local_admin(
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
    assert "initial-root-secret" not in str(captured.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"writer_username": "tap_reader"},
        {"reader_username": "root"},
        {"provisioner_username": "ROOT"},
    ],
)
async def test_role_credentials_reject_duplicate_or_root_identities(
    changes: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="three unique non-root"):
        replace(local_role_credentials(), **changes)


class IdentityClient:
    def __init__(self) -> None:
        self.passwords = {"tap_reader": "stale-reader-password"}
        self.roles = {"tap_reader", "tap_writer", "unexpected_role"}
        self.memberships = {"tap_reader": {"tap_writer", "unexpected_role"}}

    def list_users(self, **kwargs: object) -> list[str]:
        return sorted(self.passwords)

    def describe_user(self, user_name: str, **kwargs: object) -> dict[str, object]:
        return {"user_name": user_name, "roles": sorted(self.memberships.get(user_name, set()))}

    def revoke_role(self, user_name: str, role_name: str, **kwargs: object) -> None:
        self.memberships[user_name].remove(role_name)

    def drop_user(self, user_name: str, **kwargs: object) -> None:
        assert self.memberships.get(user_name, set()) == set()
        self.passwords.pop(user_name)
        self.memberships.pop(user_name, None)

    def create_user(self, user_name: str, password: str, **kwargs: object) -> None:
        self.passwords[user_name] = password
        self.memberships[user_name] = set()

    def list_roles(self, **kwargs: object) -> list[str]:
        return sorted(self.roles)

    def create_role(self, role_name: str, **kwargs: object) -> None:
        self.roles.add(role_name)

    def grant_role(self, user_name: str, role_name: str, **kwargs: object) -> None:
        self.memberships[user_name].add(role_name)


async def test_sdk_admin_recreates_existing_user_and_converges_exact_role_membership() -> None:
    client = IdentityClient()
    admin = PyMilvusAdmin(
        client,  # type: ignore[arg-type]
        SecretStr("tap-local-rotated-root"),
        authenticated_with_initial_root=False,
    )

    await admin.ensure_user("tap_reader", SecretStr("tap-local-Reader1!"))
    await admin.ensure_role("tap_reader")
    await admin.replace_user_roles("tap_reader", frozenset({"tap_reader"}))

    assert client.passwords == {"tap_reader": "tap-local-Reader1!"}
    assert client.memberships == {"tap_reader": {"tap_reader"}}

    await admin.ensure_user("tap_reader", SecretStr("tap-local-Reader1!"))
    await admin.ensure_role("tap_reader")
    await admin.replace_user_roles("tap_reader", frozenset({"tap_reader"}))

    assert client.passwords == {"tap_reader": "tap-local-Reader1!"}
    assert client.memberships == {"tap_reader": {"tap_reader"}}
