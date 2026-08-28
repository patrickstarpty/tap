from __future__ import annotations

import asyncio
import io
import logging
import secrets
import subprocess
import sys
import time
import traceback
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from typing import ContextManager

import grpc
import pytest
from pydantic import SecretStr
from pymilvus.decorators import _log_rpc_error
from pymilvus.exceptions import MilvusException

from tap.operations.milvus import client as milvus_client
from tap.operations.milvus.bootstrap import bootstrap_local_rbac
from tap.operations.milvus.client import (
    MilvusSdk,
    PyMilvusAdmin,
    PyMilvusProvisioner,
    _assert_denied,
    build_probe_clients,
    build_reader_client,
    connect_local_admin,
)
from tap.operations.milvus.client import (
    local_role_credentials as load_local_role_credentials,
)
from tap.operations.milvus.contracts import (
    PROVISIONER_BASE_GRANTS,
    PROVISIONER_PRIVILEGES,
    READER_BASE_PRIVILEGES,
    READER_TARGET_PRIVILEGES,
    WRITER_PRIVILEGES,
    MilvusGrant,
    MilvusRoleCredentials,
)

pytestmark = pytest.mark.asyncio

_SYNTHETIC_PROVIDER_MARKERS = (
    "SYNTHETIC_CREDENTIAL_MARKER",
    "SYNTHETIC_FILTER_MARKER",
    "SYNTHETIC_GROUP_MARKER",
    "SYNTHETIC_VECTOR_MARKER",
)
_PROVISIONER_GLOBAL_PRIVILEGES = frozenset(
    {
        "CreateAlias",
        "CreateCollection",
        "DescribeAlias",
        "DropAlias",
        "DropCollection",
        "ManageOwnership",
        "SelectOwnership",
    }
)
_PROVISIONER_COLLECTION_PRIVILEGES = frozenset(
    {
        "CreateIndex",
        "GetLoadState",
        "GetLoadingProgress",
        "IndexDetail",
        "Load",
        "Release",
    }
)


def _provider_log_scope() -> ContextManager[None]:
    factory = getattr(milvus_client, "suppress_pymilvus_rpc_logging", None)
    if factory is None:
        return nullcontext()
    return factory()


def _emit_provider_rpc_error(details: str) -> None:
    try:
        raise RuntimeError(details)
    except RuntimeError:
        _log_rpc_error("synthetic_call", "RPC error", details, time.monotonic())


async def test_pinned_rpc_error_records_have_the_filter_call_site_metadata() -> None:
    provider_logger = logging.getLogger("pymilvus.decorators")
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Capture()
    original_propagate = provider_logger.propagate
    provider_logger.addHandler(handler)
    provider_logger.propagate = False
    try:
        _emit_provider_rpc_error("SAFE_METADATA_MARKER")
    finally:
        provider_logger.removeHandler(handler)
        provider_logger.propagate = original_propagate

    assert len(records) == 1
    record = records[0]
    assert record.name == "pymilvus.decorators"
    assert record.module == "decorators"
    assert record.funcName == "_log_rpc_error"
    assert record.pathname.replace("\\", "/").endswith("/pymilvus/decorators.py")


async def test_provider_log_scope_preserves_same_logger_non_rpc_records() -> None:
    provider_logger = logging.getLogger("pymilvus.decorators")
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    original_level = provider_logger.level
    original_propagate = provider_logger.propagate
    provider_logger.addHandler(handler)
    provider_logger.setLevel(logging.DEBUG)
    provider_logger.propagate = False
    try:
        with _provider_log_scope():
            provider_logger.debug("SAME_LOGGER_RETRY_DEBUG_VISIBLE")
            provider_logger.info("SAME_LOGGER_RETRY_INFO_VISIBLE")
            provider_logger.warning("SAME_LOGGER_STATUS_WARNING_VISIBLE")
            provider_logger.error("SAME_LOGGER_STATUS_ERROR_VISIBLE")
    finally:
        provider_logger.removeHandler(handler)
        provider_logger.setLevel(original_level)
        provider_logger.propagate = original_propagate

    assert output.getvalue().splitlines() == [
        "SAME_LOGGER_RETRY_DEBUG_VISIBLE",
        "SAME_LOGGER_RETRY_INFO_VISIBLE",
        "SAME_LOGGER_STATUS_WARNING_VISIBLE",
        "SAME_LOGGER_STATUS_ERROR_VISIBLE",
    ]


async def test_provider_log_scope_suppresses_worker_thread_details_and_preserves_tap_logs() -> None:
    provider_logger = logging.getLogger("pymilvus.decorators")
    tap_logger = logging.getLogger("tap.operations.milvus.test")
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    provider_level = provider_logger.level
    tap_level = tap_logger.level
    tap_propagate = tap_logger.propagate
    provider_logger.addHandler(handler)
    tap_logger.addHandler(handler)
    provider_logger.setLevel(logging.ERROR)
    tap_logger.setLevel(logging.ERROR)
    tap_logger.propagate = False
    try:
        with _provider_log_scope():
            await asyncio.to_thread(
                _emit_provider_rpc_error,
                " ".join(_SYNTHETIC_PROVIDER_MARKERS),
            )
            tap_logger.error("TAP_LOG_REMAINS_VISIBLE")
    finally:
        provider_logger.removeHandler(handler)
        tap_logger.removeHandler(handler)
        provider_logger.setLevel(provider_level)
        tap_logger.setLevel(tap_level)
        tap_logger.propagate = tap_propagate

    rendered = output.getvalue()
    assert "TAP_LOG_REMAINS_VISIBLE" in rendered
    for marker in _SYNTHETIC_PROVIDER_MARKERS:
        assert marker not in rendered


async def test_overlapping_provider_log_scopes_do_not_suppress_unrelated_contexts() -> None:
    provider_logger = logging.getLogger("pymilvus.decorators")
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    original_level = provider_logger.level
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    release_first = asyncio.Event()
    release_second = asyncio.Event()

    async def first_scope() -> None:
        with _provider_log_scope():
            first_entered.set()
            await release_first.wait()

    async def second_scope() -> None:
        with _provider_log_scope():
            second_entered.set()
            await release_second.wait()
            _emit_provider_rpc_error("SECOND_SCOPE_PRIVATE_MARKER")

    provider_logger.addHandler(handler)
    provider_logger.setLevel(logging.ERROR)
    first = asyncio.create_task(first_scope())
    second = asyncio.create_task(second_scope())
    try:
        await first_entered.wait()
        await second_entered.wait()
        provider_logger.error("OUTSIDE_CONTEXT_VISIBLE")
        release_first.set()
        await first
        release_second.set()
        await second
        provider_logger.error("AFTER_CONTEXT_VISIBLE")
    finally:
        for task in (first, second):
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        provider_logger.removeHandler(handler)
        provider_logger.setLevel(original_level)

    rendered = output.getvalue()
    assert "OUTSIDE_CONTEXT_VISIBLE" in rendered
    assert "AFTER_CONTEXT_VISIBLE" in rendered
    assert "SECOND_SCOPE_PRIVATE_MARKER" not in rendered


async def test_provider_log_scope_restores_filters_after_success_and_failure() -> None:
    provider_logger = logging.getLogger("pymilvus.decorators")
    original_filters = tuple(provider_logger.filters)

    with _provider_log_scope():
        pass
    assert tuple(provider_logger.filters) == original_filters

    failure = RuntimeError("synthetic operation failure")
    with pytest.raises(RuntimeError) as captured:
        with _provider_log_scope():
            raise failure

    assert captured.value is failure
    assert tuple(provider_logger.filters) == original_filters


async def test_bootstrap_cli_suppresses_provider_rpc_details_before_generic_failure() -> None:
    repository = Path(__file__).resolve().parents[5]
    program = """
import time
import scripts.milvus_bootstrap as cli
from pymilvus.decorators import _log_rpc_error

async def fail():
    details = (
        "SYNTHETIC_CREDENTIAL_MARKER SYNTHETIC_FILTER_MARKER "
        "SYNTHETIC_GROUP_MARKER SYNTHETIC_VECTOR_MARKER"
    )
    try:
        raise RuntimeError(details)
    except RuntimeError:
        _log_rpc_error("synthetic_call", "RPC error", details, time.monotonic())
    raise RuntimeError("synthetic provider failure")

cli._run = fail
raise SystemExit(cli.main())
"""

    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "Milvus RBAC bootstrap failed.\n"
    for marker in _SYNTHETIC_PROVIDER_MARKERS:
        assert marker not in completed.stderr


class StructuredRpcError(grpc.RpcError):
    def __init__(self, status: grpc.StatusCode) -> None:
        self._status = status

    def code(self) -> grpc.StatusCode:
        return self._status

    def details(self) -> str:
        return "provider detail must remain sanitized"


async def test_denial_classifier_accepts_pinned_milvus_permission_code() -> None:
    def deny() -> None:
        raise MilvusException(compatible_code=3)

    await _assert_denied(deny, MilvusException)


async def test_denial_classifier_accepts_grpc_permission_denied_status() -> None:
    def deny() -> None:
        raise StructuredRpcError(grpc.StatusCode.PERMISSION_DENIED)

    await _assert_denied(deny, MilvusException)


async def test_denial_classifier_rejects_and_sanitizes_other_grpc_statuses() -> None:
    def fail() -> None:
        raise StructuredRpcError(grpc.StatusCode.UNAVAILABLE)

    with pytest.raises(
        RuntimeError,
        match="Milvus denial probe returned an unexpected provider error",
    ) as captured:
        await _assert_denied(fail, MilvusException)

    assert "provider detail" not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__suppress_context__ is True
    assert "provider detail" not in "".join(traceback.format_exception(captured.value))


async def test_denial_classifier_rejects_and_sanitizes_other_provider_errors() -> None:
    def fail() -> None:
        raise MilvusException(compatible_code=1, message="provider detail must remain sanitized")

    with pytest.raises(
        RuntimeError,
        match="Milvus denial probe returned an unexpected provider error",
    ) as captured:
        await _assert_denied(fail, MilvusException)

    assert "provider detail" not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__suppress_context__ is True
    assert "provider detail" not in "".join(traceback.format_exception(captured.value))


async def test_denial_classifier_preserves_cancellation() -> None:
    def cancel() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _assert_denied(cancel, MilvusException)


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
    assert admin.role_privileges("tap_reader") == set(READER_BASE_PRIVILEGES)
    assert admin.role_privileges("tap_writer") == set(WRITER_PRIVILEGES)
    assert admin.role_privileges("tap_provisioner") == (
        _PROVISIONER_GLOBAL_PRIVILEGES | _PROVISIONER_COLLECTION_PRIVILEGES
    )
    assert "Search" not in admin.role_privileges("tap_writer")
    assert "Insert" not in admin.role_privileges("tap_reader")
    assert "SelectOwnership" in admin.role_privileges("tap_provisioner")
    assert "SelectOwnership" not in admin.role_privileges("tap_reader")
    assert "SelectOwnership" not in admin.role_privileges("tap_writer")
    assert not ((READER_BASE_PRIVILEGES | READER_TARGET_PRIVILEGES) & WRITER_PRIVILEGES)
    assert not (WRITER_PRIVILEGES & PROVISIONER_PRIVILEGES)
    assert admin.password_rotations == [("root", "tap-local-Root1!")]


async def test_bootstrap_reader_base_is_global_describe_only() -> None:
    admin = RecordingAdmin()

    await bootstrap_local_rbac(admin, local_role_credentials())

    assert admin.grants["tap_reader"] == frozenset(
        {
            MilvusGrant("instance", "*", "DescribeAlias"),
            MilvusGrant("instance", "*", "DescribeCollection"),
        }
    )
    assert admin.grants["tap_writer"] == frozenset(
        MilvusGrant("collection", "*", privilege) for privilege in WRITER_PRIVILEGES
    )
    assert admin.grants["tap_provisioner"] == frozenset(
        {
            *(
                MilvusGrant("instance", "*", privilege)
                for privilege in _PROVISIONER_GLOBAL_PRIVILEGES
            ),
            *(
                MilvusGrant("collection", "*", privilege)
                for privilege in _PROVISIONER_COLLECTION_PRIVILEGES
            ),
        }
    )


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


async def test_sdk_provisioner_reader_target_mutations_exclude_base_describe_privileges() -> None:
    class GrantClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str, str]] = []

        def grant_privilege_v2(
            self,
            role_name: str,
            privilege: str,
            object_name: str,
            **kwargs: object,
        ) -> None:
            self.calls.append(("grant", role_name, privilege, object_name))

        def revoke_privilege_v2(
            self,
            role_name: str,
            privilege: str,
            object_name: str,
            **kwargs: object,
        ) -> None:
            self.calls.append(("revoke", role_name, privilege, object_name))

    client = GrantClient()
    provisioner = PyMilvusProvisioner(client, object())  # type: ignore[arg-type]

    await provisioner.grant_collection("kb_doc_v1_corpus_fixture_v1", "tap_reader")
    await provisioner.revoke_collection("kb_doc_v1_corpus_fixture_v1", "tap_reader")

    assert client.calls == [
        ("grant", "tap_reader", "Query", "kb_doc_v1_corpus_fixture_v1"),
        ("grant", "tap_reader", "Search", "kb_doc_v1_corpus_fixture_v1"),
        ("revoke", "tap_reader", "Query", "kb_doc_v1_corpus_fixture_v1"),
        ("revoke", "tap_reader", "Search", "kb_doc_v1_corpus_fixture_v1"),
    ]


async def test_sdk_admin_preserves_global_describe_and_removes_wildcard_reader_search() -> None:
    physical = "kb_doc_v1_corpus_fixture_v1"

    class GrantInventoryClient:
        def __init__(self) -> None:
            self.privileges = [
                {
                    "object_type": object_type,
                    "object_name": "*",
                    "db_name": "default",
                    "role_name": "tap_reader",
                    "privilege": privilege,
                }
                for object_type, privilege in (
                    ("Global", "DescribeAlias"),
                    ("Global", "DescribeCollection"),
                    ("Collection", "Query"),
                    ("Collection", "Search"),
                )
            ]
            self.privileges.extend(
                {
                    "object_type": "Collection",
                    "object_name": physical,
                    "db_name": "default",
                    "role_name": "tap_reader",
                    "privilege": privilege,
                }
                for privilege in ("Query", "Search")
            )
            self.calls: list[tuple[str, str]] = []

        def describe_role(self, role_name: str, **kwargs: object) -> dict[str, object]:
            return {"role": role_name, "privileges": list(self.privileges)}

        def revoke_privilege_v2(
            self,
            role_name: str,
            privilege: str,
            object_name: str,
            **kwargs: object,
        ) -> None:
            self.calls.append(("revoke", privilege))
            self.privileges = [
                item
                for item in self.privileges
                if not (
                    item["role_name"] == role_name
                    and item["privilege"] == privilege
                    and item["object_name"] == object_name
                )
            ]

        def grant_privilege_v2(
            self,
            role_name: str,
            privilege: str,
            object_name: str,
            **kwargs: object,
        ) -> None:
            self.calls.append(("grant", privilege))
            self.privileges.append(
                {
                    "object_type": "Global",
                    "object_name": object_name,
                    "db_name": "default",
                    "role_name": role_name,
                    "privilege": privilege,
                }
            )

    client = GrantInventoryClient()
    admin = PyMilvusAdmin(
        client,  # type: ignore[arg-type]
        SecretStr("tap-local-Root1!"),
        authenticated_with_initial_root=False,
        database_name="default",
    )
    expected = frozenset(
        {
            MilvusGrant("instance", "*", "DescribeAlias"),
            MilvusGrant("instance", "*", "DescribeCollection"),
        }
    )

    await admin.replace_role_grants("tap_reader", expected)
    await admin.replace_role_grants("tap_reader", expected)

    assert client.calls == [("revoke", "Query"), ("revoke", "Search")]
    assert {(item["object_type"], item["privilege"]) for item in client.privileges} == {
        ("Global", "DescribeAlias"),
        ("Global", "DescribeCollection"),
        ("Collection", "Query"),
        ("Collection", "Search"),
    }
    assert {
        (item["object_name"], item["privilege"])
        for item in client.privileges
        if item["object_name"] != "*"
    } == {(physical, "Query"), (physical, "Search")}


async def test_sdk_admin_rejects_duplicate_base_grant_inventory() -> None:
    duplicate = {
        "object_type": "Global",
        "object_name": "*",
        "db_name": "default",
        "role_name": "tap_reader",
        "privilege": "DescribeAlias",
    }

    class DuplicateGrantClient:
        def describe_role(self, role_name: str, **kwargs: object) -> dict[str, object]:
            return {
                "role": role_name,
                "privileges": [
                    duplicate,
                    dict(duplicate),
                    {**duplicate, "privilege": "DescribeCollection"},
                ],
            }

    admin = PyMilvusAdmin(
        DuplicateGrantClient(),  # type: ignore[arg-type]
        SecretStr("tap-local-Root1!"),
        authenticated_with_initial_root=False,
        database_name="default",
    )
    expected = frozenset(
        {
            MilvusGrant("instance", "*", "DescribeAlias"),
            MilvusGrant("instance", "*", "DescribeCollection"),
        }
    )

    with pytest.raises(RuntimeError, match="grant metadata"):
        await admin.replace_role_grants("tap_reader", expected)


async def test_sdk_admin_rejects_conflicting_base_dimensions_before_mutation() -> None:
    client = RoleGrantInventoryClient(
        "tap_reader",
        [
            role_grant("tap_reader", "Global", "*", "DescribeAlias"),
            role_grant("tap_reader", "Collection", "*", "DescribeAlias"),
        ],
    )
    admin = sdk_admin(client)
    expected = frozenset(
        {
            MilvusGrant("instance", "*", "DescribeAlias"),
            MilvusGrant("instance", "*", "DescribeCollection"),
        }
    )

    with pytest.raises(RuntimeError, match="grant metadata"):
        await admin.replace_role_grants("tap_reader", expected)

    assert client.calls == []


class RoleGrantInventoryClient:
    def __init__(self, role_name: str, privileges: list[dict[str, str]]) -> None:
        self.role_name = role_name
        self.privileges = privileges
        self.calls: list[tuple[str, str, str]] = []

    def describe_role(self, role_name: str, **kwargs: object) -> dict[str, object]:
        assert role_name == self.role_name
        return {"role": role_name, "privileges": [dict(item) for item in self.privileges]}

    def revoke_privilege_v2(
        self,
        role_name: str,
        privilege: str,
        object_name: str,
        **kwargs: object,
    ) -> None:
        self.calls.append(("revoke", privilege, object_name))
        self.privileges = [
            item
            for item in self.privileges
            if not (
                item["role_name"] == role_name
                and item["privilege"] == privilege
                and item["object_name"] == object_name
            )
        ]

    def grant_privilege_v2(
        self,
        role_name: str,
        privilege: str,
        object_name: str,
        **kwargs: object,
    ) -> None:
        self.calls.append(("grant", privilege, object_name))
        global_privilege = (
            role_name == "tap_reader" and privilege in {"DescribeAlias", "DescribeCollection"}
        ) or (role_name == "tap_provisioner" and privilege in _PROVISIONER_GLOBAL_PRIVILEGES)
        self.privileges.append(
            {
                "object_type": "Global" if global_privilege else "Collection",
                "object_name": object_name,
                "db_name": "default",
                "role_name": role_name,
                "privilege": privilege,
            }
        )


def role_grant(
    role_name: str,
    object_type: str,
    object_name: str,
    privilege: str,
    *,
    database_name: str = "default",
) -> dict[str, str]:
    return {
        "object_type": object_type,
        "object_name": object_name,
        "db_name": database_name,
        "role_name": role_name,
        "privilege": privilege,
    }


def sdk_admin(client: RoleGrantInventoryClient) -> PyMilvusAdmin:
    return PyMilvusAdmin(
        client,  # type: ignore[arg-type]
        SecretStr("tap-local-Root1!"),
        authenticated_with_initial_root=False,
        database_name="default",
    )


async def test_sdk_admin_reconciles_persisted_grants_in_the_configured_database() -> None:
    physical = "kb_doc_v1_corpus_fixture_v1"

    class DatabaseScopedClient(RoleGrantInventoryClient):
        def __init__(self) -> None:
            super().__init__(
                "tap_reader",
                [
                    role_grant("tap_reader", "Global", "*", "DescribeAlias"),
                    role_grant("tap_reader", "Global", "*", "UnknownPrivilege"),
                    role_grant("tap_reader", "Collection", physical, "Query"),
                    role_grant("tap_reader", "Collection", physical, "Search"),
                ],
            )
            self.database_calls: list[tuple[str, object]] = []

        def describe_role(self, role_name: str, **kwargs: object) -> dict[str, object]:
            database_name = kwargs.get("db_name")
            self.database_calls.append(("describe", database_name))
            raw = super().describe_role(role_name, **kwargs)
            if database_name != "default":
                raw["privileges"] = [
                    {key: value for key, value in item.items() if key != "db_name"}
                    for item in self.privileges
                ]
            return raw

        def revoke_privilege_v2(
            self,
            role_name: str,
            privilege: str,
            object_name: str,
            **kwargs: object,
        ) -> None:
            self.database_calls.append(("revoke", kwargs.get("db_name")))
            super().revoke_privilege_v2(
                role_name,
                privilege,
                object_name,
                **kwargs,
            )

        def grant_privilege_v2(
            self,
            role_name: str,
            privilege: str,
            object_name: str,
            **kwargs: object,
        ) -> None:
            self.database_calls.append(("grant", kwargs.get("db_name")))
            super().grant_privilege_v2(
                role_name,
                privilege,
                object_name,
                **kwargs,
            )

    client = DatabaseScopedClient()
    admin = sdk_admin(client)
    expected = frozenset(
        {
            MilvusGrant("instance", "*", "DescribeAlias"),
            MilvusGrant("instance", "*", "DescribeCollection"),
        }
    )

    await admin.replace_role_grants("tap_reader", expected)
    await admin.replace_role_grants("tap_reader", expected)

    assert client.database_calls == [
        ("describe", "default"),
        ("revoke", "default"),
        ("grant", "default"),
        ("describe", "default"),
    ]
    assert {
        (item["object_name"], item["privilege"])
        for item in client.privileges
        if item["object_name"] != "*"
    } == {(physical, "Query"), (physical, "Search")}


async def test_sdk_admin_preserves_active_reader_target_across_repeated_bootstrap() -> None:
    physical = "kb_doc_v1_corpus_fixture_v1"
    client = RoleGrantInventoryClient(
        "tap_reader",
        [
            role_grant("tap_reader", "Global", "*", "DescribeAlias"),
            role_grant("tap_reader", "Global", "*", "DescribeCollection"),
            role_grant("tap_reader", "Collection", physical, "Query"),
            role_grant("tap_reader", "Collection", physical, "Search"),
        ],
    )
    admin = sdk_admin(client)
    expected = frozenset(
        {
            MilvusGrant("instance", "*", "DescribeAlias"),
            MilvusGrant("instance", "*", "DescribeCollection"),
        }
    )

    await admin.replace_role_grants("tap_reader", expected)
    await admin.replace_role_grants("tap_reader", expected)

    assert client.calls == []
    assert {
        (item["object_name"], item["privilege"])
        for item in client.privileges
        if item["object_name"] != "*"
    } == {(physical, "Query"), (physical, "Search")}


async def test_sdk_admin_preserves_temporary_writer_target_across_repeated_bootstrap() -> None:
    physical = "kb_doc_v1_corpus_fixture_v1"
    client = RoleGrantInventoryClient(
        "tap_writer",
        [
            *[
                role_grant("tap_writer", "Collection", "*", privilege)
                for privilege in WRITER_PRIVILEGES
            ],
            role_grant("tap_writer", "Collection", physical, "Flush"),
            role_grant("tap_writer", "Collection", physical, "Insert"),
        ],
    )
    admin = sdk_admin(client)
    expected = frozenset(
        MilvusGrant("collection", "*", privilege) for privilege in WRITER_PRIVILEGES
    )

    await admin.replace_role_grants("tap_writer", expected)
    await admin.replace_role_grants("tap_writer", expected)

    assert client.calls == []
    assert {
        (item["object_name"], item["privilege"])
        for item in client.privileges
        if item["object_name"] != "*"
    } == {(physical, "Flush"), (physical, "Insert")}


async def test_sdk_admin_provisioner_base_is_exact_and_repeated_reconciliation_is_idle() -> None:
    client = RoleGrantInventoryClient(
        "tap_provisioner",
        [
            role_grant("tap_provisioner", scope, "*", privilege)
            for scope, privileges in (
                ("Global", _PROVISIONER_GLOBAL_PRIVILEGES),
                ("Collection", _PROVISIONER_COLLECTION_PRIVILEGES),
            )
            for privilege in sorted(privileges)
        ],
    )
    admin = sdk_admin(client)
    await admin.replace_role_grants("tap_provisioner", PROVISIONER_BASE_GRANTS)
    await admin.replace_role_grants("tap_provisioner", PROVISIONER_BASE_GRANTS)

    assert client.calls == []


async def test_sdk_admin_adds_missing_select_ownership_then_is_idempotent() -> None:
    client = RoleGrantInventoryClient(
        "tap_provisioner",
        [
            role_grant("tap_provisioner", scope, "*", privilege)
            for scope, privileges in (
                (
                    "Global",
                    _PROVISIONER_GLOBAL_PRIVILEGES - {"SelectOwnership"},
                ),
                ("Collection", _PROVISIONER_COLLECTION_PRIVILEGES),
            )
            for privilege in sorted(privileges)
        ],
    )
    expected = frozenset(
        {
            *(
                MilvusGrant("instance", "*", privilege)
                for privilege in _PROVISIONER_GLOBAL_PRIVILEGES
            ),
            *(
                MilvusGrant("collection", "*", privilege)
                for privilege in _PROVISIONER_COLLECTION_PRIVILEGES
            ),
        }
    )
    admin = sdk_admin(client)

    await admin.replace_role_grants("tap_provisioner", expected)
    await admin.replace_role_grants("tap_provisioner", expected)

    assert client.calls == [("grant", "SelectOwnership", "*")]
    added = next(item for item in client.privileges if item["privilege"] == "SelectOwnership")
    assert added["object_type"] == "Global"


@pytest.mark.parametrize(
    "invalid",
    (
        role_grant(
            "tap_reader",
            "Collection",
            "kb_doc_v1_corpus_fixture_v1",
            "Insert",
        ),
        role_grant(
            "tap_reader",
            "Global",
            "kb_doc_v1_corpus_fixture_v1",
            "Search",
        ),
        role_grant(
            "tap_reader",
            "Database",
            "kb_doc_v1_corpus_fixture_v1",
            "Search",
        ),
        role_grant(
            "tap_reader",
            "Collection",
            "kb_doc_v1_corpus_fixture_v1",
            "Search",
            database_name="other",
        ),
        role_grant("tap_reader", "Collection", "unowned_collection", "Search"),
    ),
    ids=("privilege", "global", "database", "other-database", "name"),
)
async def test_sdk_admin_invalid_reader_concrete_fails_before_any_base_mutation(
    invalid: dict[str, str],
) -> None:
    client = RoleGrantInventoryClient(
        "tap_reader",
        [
            role_grant("tap_reader", "Global", "*", "DescribeAlias"),
            role_grant("tap_reader", "Collection", "*", "Search"),
            invalid,
        ],
    )
    admin = sdk_admin(client)
    expected = frozenset(
        {
            MilvusGrant("instance", "*", "DescribeAlias"),
            MilvusGrant("instance", "*", "DescribeCollection"),
        }
    )

    with pytest.raises(RuntimeError, match="concrete grant metadata"):
        await admin.replace_role_grants("tap_reader", expected)

    assert client.calls == []


async def test_sdk_admin_provisioner_concrete_fails_before_any_base_mutation() -> None:
    client = RoleGrantInventoryClient(
        "tap_provisioner",
        [
            role_grant("tap_provisioner", "Collection", "*", "CreateAlias"),
            role_grant(
                "tap_provisioner",
                "Collection",
                "kb_doc_v1_corpus_fixture_v1",
                "CreateCollection",
            ),
        ],
    )
    admin = sdk_admin(client)
    with pytest.raises(RuntimeError, match="concrete grant metadata"):
        await admin.replace_role_grants("tap_provisioner", PROVISIONER_BASE_GRANTS)

    assert client.calls == []


@pytest.mark.parametrize(
    ("privilege", "expected_scope", "wrong_scope"),
    (
        *(
            (privilege, "Global", "Collection")
            for privilege in sorted(_PROVISIONER_GLOBAL_PRIVILEGES)
        ),
        *(
            (privilege, "Collection", "Global")
            for privilege in sorted(_PROVISIONER_COLLECTION_PRIVILEGES)
        ),
    ),
)
async def test_sdk_admin_corrects_each_wrong_provisioner_base_scope_then_idempotent(
    privilege: str,
    expected_scope: str,
    wrong_scope: str,
) -> None:
    client = RoleGrantInventoryClient(
        "tap_provisioner",
        [
            role_grant(
                "tap_provisioner",
                wrong_scope if item == privilege else scope,
                "*",
                item,
            )
            for scope, items in (
                ("Global", _PROVISIONER_GLOBAL_PRIVILEGES),
                ("Collection", _PROVISIONER_COLLECTION_PRIVILEGES),
            )
            for item in sorted(items)
        ],
    )
    admin = sdk_admin(client)

    await admin.replace_role_grants("tap_provisioner", PROVISIONER_BASE_GRANTS)
    await admin.replace_role_grants("tap_provisioner", PROVISIONER_BASE_GRANTS)

    assert client.calls == [
        ("revoke", privilege, "*"),
        ("grant", privilege, "*"),
    ]
    corrected = next(item for item in client.privileges if item["privilege"] == privilege)
    assert corrected["object_type"] == expected_scope


async def test_sdk_admin_removes_unknown_global_provisioner_base_then_idempotent() -> None:
    client = RoleGrantInventoryClient(
        "tap_provisioner",
        [
            *[
                role_grant("tap_provisioner", scope, "*", privilege)
                for scope, privileges in (
                    ("Global", _PROVISIONER_GLOBAL_PRIVILEGES),
                    ("Collection", _PROVISIONER_COLLECTION_PRIVILEGES),
                )
                for privilege in sorted(privileges)
            ],
            role_grant("tap_provisioner", "Global", "*", "UnknownPrivilege"),
        ],
    )
    admin = sdk_admin(client)

    await admin.replace_role_grants("tap_provisioner", PROVISIONER_BASE_GRANTS)
    await admin.replace_role_grants("tap_provisioner", PROVISIONER_BASE_GRANTS)

    assert client.calls == [("revoke", "UnknownPrivilege", "*")]


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


async def test_admin_connection_prefers_persisted_rotated_root_even_with_initial_opt_in() -> None:
    attempted_passwords: list[str] = []
    server = AuthenticationServer("tap-local-rotated-root")

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

    assert attempted_passwords == ["tap-local-rotated-root"]
    assert server.events == [("authenticate", "tap-local-rotated-root")]
    assert admin.authenticated_with_initial_root is False


async def test_admin_connection_falls_back_when_client_constructor_rejects_rotated_root() -> None:
    class RedactedCredential(str):
        def __repr__(self) -> str:
            return "<redacted>"

    rotated = RedactedCredential(secrets.token_urlsafe(18))
    initial = RedactedCredential(secrets.token_urlsafe(18))
    server = AuthenticationServer(initial)
    attempts: list[str] = []

    def factory(**kwargs: object) -> AuthenticationClient:
        password = kwargs["password"]
        assert isinstance(password, str)
        if secrets.compare_digest(password, rotated):
            attempts.append("rotated")
            if not secrets.compare_digest(server.password, rotated):
                raise RuntimeError("constructor authentication failed")
        elif secrets.compare_digest(password, initial):
            attempts.append("initial")
        else:
            raise AssertionError("unexpected credential")
        return AuthenticationClient(server, password)

    admin = await connect_local_admin(
        {
            "MILVUS_URI": "http://127.0.0.1:19530",
            "MILVUS_DATABASE": "default",
            "MILVUS_ROOT_PASSWORD": rotated,
            "MILVUS_INITIAL_ROOT_PASSWORD": initial,
            "TAP_ALLOW_INITIAL_MILVUS_ROOT": "1",
        },
        client_factory=factory,
    )

    assert attempts == ["rotated", "initial", "rotated"]
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
        database_name="default",
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
