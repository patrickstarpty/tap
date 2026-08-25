"""Thin, deadline-bounded PyMilvus clients for operational identities."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from pydantic import SecretStr

from tap.modules.knowledge.adapters.milvus.transport import (
    MilvusCollectionDescriptor,
    MilvusHybridRequest,
    MilvusQueryRequest,
)
from tap.modules.knowledge.domain.models import SourceFamily
from tap.operations.milvus.contracts import (
    PROVISIONER_PRIVILEGES,
    READER_PRIVILEGES,
    WRITER_PRIVILEGES,
    MilvusAdmin,
    MilvusGrant,
    MilvusProbeClients,
    MilvusRoleCredentials,
)

_TIMEOUT_SECONDS = 10.0
_PERMISSION_DENIED_COMPATIBLE_CODE = 3


@dataclass(frozen=True, slots=True)
class MilvusSdk:
    client_factory: Callable[..., object]
    create_schema: Callable[..., object]
    function_factory: Callable[..., object]
    ann_search_request_factory: Callable[..., object]
    ranker_factory: Callable[[], object]
    varchar_type: object
    sparse_vector_type: object
    float_vector_type: object
    array_type: object
    int64_type: object
    bool_type: object
    bm25_function_type: object
    permission_error: type[BaseException]


class _SyncClient(Protocol):
    def list_users(self, **kwargs: object) -> object: ...

    def create_user(self, user_name: str, password: str, **kwargs: object) -> object: ...

    def drop_user(self, user_name: str, **kwargs: object) -> object: ...

    def describe_user(self, user_name: str, **kwargs: object) -> object: ...

    def create_role(self, role_name: str, **kwargs: object) -> object: ...

    def list_roles(self, **kwargs: object) -> object: ...

    def grant_role(self, user_name: str, role_name: str, **kwargs: object) -> object: ...

    def revoke_role(self, user_name: str, role_name: str, **kwargs: object) -> object: ...

    def describe_role(self, role_name: str, **kwargs: object) -> object: ...

    def grant_privilege_v2(
        self,
        role_name: str,
        privilege: str,
        collection_name: str,
        **kwargs: object,
    ) -> object: ...

    def revoke_privilege_v2(
        self,
        role_name: str,
        privilege: str,
        collection_name: str,
        **kwargs: object,
    ) -> object: ...

    def update_password(
        self,
        user_name: str,
        old_password: str,
        new_password: str,
        **kwargs: object,
    ) -> object: ...

    def create_collection(self, collection_name: str, **kwargs: object) -> object: ...

    def prepare_index_params(self, **kwargs: object) -> object: ...

    def create_index(
        self,
        collection_name: str,
        index_params: object,
        **kwargs: object,
    ) -> object: ...

    def describe_index(
        self,
        collection_name: str,
        index_name: str,
        **kwargs: object,
    ) -> object: ...

    def load_collection(self, collection_name: str, **kwargs: object) -> object: ...

    def release_collection(self, collection_name: str, **kwargs: object) -> object: ...

    def get_load_state(self, collection_name: str, **kwargs: object) -> object: ...

    def create_alias(self, collection_name: str, alias: str, **kwargs: object) -> object: ...

    def alter_alias(self, collection_name: str, alias: str, **kwargs: object) -> object: ...

    def describe_alias(self, alias: str, **kwargs: object) -> object: ...

    def describe_collection(self, collection_name: str, **kwargs: object) -> object: ...

    def drop_alias(self, alias: str, **kwargs: object) -> object: ...

    def drop_collection(self, collection_name: str, **kwargs: object) -> object: ...

    def insert(self, collection_name: str, data: object, **kwargs: object) -> object: ...

    def upsert(self, collection_name: str, data: object, **kwargs: object) -> object: ...

    def delete(self, collection_name: str, **kwargs: object) -> object: ...

    def flush(self, collection_name: str, **kwargs: object) -> object: ...

    def hybrid_search(self, **kwargs: object) -> object: ...

    def query(self, collection_name: str, **kwargs: object) -> object: ...

    def close(self) -> None: ...


class PyMilvusAdmin:
    def __init__(
        self,
        client: _SyncClient,
        current_root_password: SecretStr,
        *,
        authenticated_with_initial_root: bool,
    ) -> None:
        self._client = client
        self._current_root_password = current_root_password
        self.authenticated_with_initial_root = authenticated_with_initial_root

    async def ensure_user(self, username: str, password: SecretStr) -> None:
        users = await _call(lambda: self._client.list_users(timeout=_TIMEOUT_SECONDS))
        if username in _string_items(users):
            raw = await _call(
                lambda: self._client.describe_user(username, timeout=_TIMEOUT_SECONDS)
            )
            for role_name in sorted(_user_roles(raw)):

                def revoke_existing_role(role_name: str = role_name) -> object:
                    return self._client.revoke_role(
                        username,
                        role_name,
                        timeout=_TIMEOUT_SECONDS,
                    )

                await _call(revoke_existing_role)
            await _call(lambda: self._client.drop_user(username, timeout=_TIMEOUT_SECONDS))
        await _call(
            lambda: self._client.create_user(
                username,
                password.get_secret_value(),
                timeout=_TIMEOUT_SECONDS,
            )
        )

    async def ensure_role(self, role_name: str) -> None:
        roles = await _call(lambda: self._client.list_roles(timeout=_TIMEOUT_SECONDS))
        if role_name not in _role_names(roles):
            await _call(lambda: self._client.create_role(role_name, timeout=_TIMEOUT_SECONDS))

    async def replace_user_roles(
        self,
        username: str,
        role_names: frozenset[str],
    ) -> None:
        user = await _call(lambda: self._client.describe_user(username, timeout=_TIMEOUT_SECONDS))
        current = _user_roles(user)
        for role_name in sorted(current - role_names):

            def revoke_role(role_name: str = role_name) -> object:
                return self._client.revoke_role(
                    username,
                    role_name,
                    timeout=_TIMEOUT_SECONDS,
                )

            await _call(revoke_role)
        for role_name in sorted(role_names - current):

            def grant_role(role_name: str = role_name) -> object:
                return self._client.grant_role(
                    username,
                    role_name,
                    timeout=_TIMEOUT_SECONDS,
                )

            await _call(grant_role)

    async def replace_role_grants(
        self,
        role_name: str,
        grants: frozenset[MilvusGrant],
    ) -> None:
        raw = await _call(lambda: self._client.describe_role(role_name, timeout=_TIMEOUT_SECONDS))
        current = _role_grants(raw)
        for grant in sorted(current - grants, key=_grant_sort_key):

            def revoke(grant: MilvusGrant = grant) -> object:
                return self._client.revoke_privilege_v2(
                    role_name,
                    grant.privilege,
                    grant.resource_name,
                    timeout=_TIMEOUT_SECONDS,
                )

            await _call(revoke)
        for grant in sorted(grants - current, key=_grant_sort_key):

            def grant_privilege(grant: MilvusGrant = grant) -> object:
                return self._client.grant_privilege_v2(
                    role_name,
                    grant.privilege,
                    grant.resource_name,
                    timeout=_TIMEOUT_SECONDS,
                )

            await _call(grant_privilege)

    async def rotate_root_password(self, password: SecretStr) -> None:
        old_password = self._current_root_password.get_secret_value()
        new_password = password.get_secret_value()
        if old_password == new_password:
            return
        await _call(
            lambda: self._client.update_password(
                "root",
                old_password,
                new_password,
                reset_connection=True,
                timeout=_TIMEOUT_SECONDS,
            )
        )
        self._current_root_password = password
        self.authenticated_with_initial_root = False

    async def close(self) -> None:
        await _call(self._client.close)


class PyMilvusProvisioner:
    def __init__(self, client: _SyncClient, sdk: MilvusSdk) -> None:
        self._client = client
        self._sdk = sdk

    async def create_collection(self, name: str, schema: Mapping[str, object]) -> None:
        if schema.get("vector_dimension") != 2:
            raise ValueError("health schema vector dimension is invalid")
        await _call(lambda: self._create_collection(name))

    def _create_collection(self, name: str) -> object:
        sdk_schema = self._sdk.create_schema(
            auto_id=False,
            enable_dynamic_field=False,
            description="TAP isolated behavioral health probe",
        )
        add_field = cast(Callable[..., object], getattr(sdk_schema, "add_field"))
        add_function = cast(Callable[..., object], getattr(sdk_schema, "add_function"))
        add_field(
            field_name="chunk_id",
            datatype=self._sdk.varchar_type,
            is_primary=True,
            max_length=66,
        )
        add_field(
            field_name="content",
            datatype=self._sdk.varchar_type,
            max_length=256,
            enable_analyzer=True,
        )
        add_field(field_name="bm25_sparse", datatype=self._sdk.sparse_vector_type)
        add_field(field_name="dense_vector", datatype=self._sdk.float_vector_type, dim=2)
        add_field(field_name="tenant_id", datatype=self._sdk.varchar_type, max_length=64)
        add_field(field_name="project_id", datatype=self._sdk.varchar_type, max_length=64)
        add_field(
            field_name="allowed_group_ids",
            datatype=self._sdk.array_type,
            element_type=self._sdk.varchar_type,
            max_capacity=4,
            max_length=64,
        )
        add_field(field_name="classification_rank", datatype=self._sdk.int64_type)
        add_field(field_name="environment", datatype=self._sdk.varchar_type, max_length=32)
        add_field(field_name="corpus_version", datatype=self._sdk.varchar_type, max_length=64)
        add_field(field_name="deleted", datatype=self._sdk.bool_type)
        add_function(
            self._sdk.function_factory(
                name="content_bm25_v1",
                function_type=self._sdk.bm25_function_type,
                input_field_names=["content"],
                output_field_names=["bm25_sparse"],
            )
        )
        return self._client.create_collection(
            name,
            schema=sdk_schema,
            consistency_level="Strong",
            timeout=_TIMEOUT_SECONDS,
        )

    async def create_indexes(self, name: str) -> None:
        await _call(lambda: self._create_indexes(name))

    def _create_indexes(self, name: str) -> None:
        params = self._client.prepare_index_params()
        add_index = cast(Callable[..., object], getattr(params, "add_index"))
        add_index(
            field_name="dense_vector",
            index_name="dense_vector",
            index_type="FLAT",
            metric_type="COSINE",
        )
        add_index(
            field_name="bm25_sparse",
            index_name="bm25_sparse",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
            params={"bm25_k1": 1.2, "bm25_b": 0.75},
        )
        for field_name in (
            "tenant_id",
            "project_id",
            "allowed_group_ids",
            "classification_rank",
            "environment",
            "corpus_version",
            "deleted",
        ):
            add_index(
                field_name=field_name,
                index_name=field_name,
                index_type="INVERTED",
            )
        self._client.create_index(name, params, timeout=_TIMEOUT_SECONDS)
        for index_name in (
            "dense_vector",
            "bm25_sparse",
            "tenant_id",
            "project_id",
            "allowed_group_ids",
            "classification_rank",
            "environment",
            "corpus_version",
            "deleted",
        ):
            self._client.describe_index(name, index_name, timeout=_TIMEOUT_SECONDS)
        self._client.load_collection(name, timeout=_TIMEOUT_SECONDS)
        self._client.get_load_state(name, timeout=_TIMEOUT_SECONDS)
        connection = cast(object, getattr(self._client, "_get_connection")())
        get_loading_progress = cast(
            Callable[..., object],
            getattr(connection, "get_loading_progress"),
        )
        get_loading_progress(name, timeout=_TIMEOUT_SECONDS)
        self._client.release_collection(name, timeout=_TIMEOUT_SECONDS)
        self._client.load_collection(name, timeout=_TIMEOUT_SECONDS)

    async def grant_collection(self, name: str, role_name: str) -> None:
        privileges = _privileges_for_role(role_name)
        for privilege in sorted(privileges):

            def grant_privilege(privilege: str = privilege) -> object:
                return self._client.grant_privilege_v2(
                    role_name,
                    privilege,
                    name,
                    timeout=_TIMEOUT_SECONDS,
                )

            await _call(grant_privilege)

    async def revoke_collection(self, name: str, role_name: str) -> None:
        privileges = _privileges_for_role(role_name)
        for privilege in sorted(privileges):

            def revoke_privilege(privilege: str = privilege) -> object:
                return self._client.revoke_privilege_v2(
                    role_name,
                    privilege,
                    name,
                    timeout=_TIMEOUT_SECONDS,
                )

            await _call(revoke_privilege)

    async def create_alias(self, alias: str, collection_name: str) -> None:
        await _call(
            lambda: self._client.create_alias(
                collection_name,
                alias,
                timeout=_TIMEOUT_SECONDS,
            )
        )

    async def alter_alias(self, alias: str, collection_name: str) -> None:
        await _call(
            lambda: self._client.alter_alias(
                collection_name,
                alias,
                timeout=_TIMEOUT_SECONDS,
            )
        )

    async def describe_alias(self, alias: str) -> str:
        raw = await _call(lambda: self._client.describe_alias(alias, timeout=_TIMEOUT_SECONDS))
        return _alias_collection(raw)

    async def drop_alias(self, alias: str) -> None:
        await _call(lambda: self._client.drop_alias(alias, timeout=_TIMEOUT_SECONDS))

    async def drop_collection(self, name: str) -> None:
        await _call(lambda: self._client.drop_collection(name, timeout=_TIMEOUT_SECONDS))

    async def close(self) -> None:
        await _call(self._client.close)


class PyMilvusWriter:
    def __init__(self, client: _SyncClient) -> None:
        self._client = client

    async def insert(self, name: str, rows: tuple[Mapping[str, object], ...]) -> None:
        await _call(
            lambda: self._client.insert(name, [dict(row) for row in rows], timeout=_TIMEOUT_SECONDS)
        )

    async def upsert(self, name: str, rows: tuple[Mapping[str, object], ...]) -> None:
        await _call(
            lambda: self._client.upsert(name, [dict(row) for row in rows], timeout=_TIMEOUT_SECONDS)
        )

    async def delete(self, name: str, chunk_ids: tuple[str, ...]) -> None:
        await _call(
            lambda: self._client.delete(
                name,
                ids=list(chunk_ids),
                timeout=_TIMEOUT_SECONDS,
            )
        )

    async def flush(self, name: str) -> None:
        await _call(lambda: self._client.flush(name, timeout=_TIMEOUT_SECONDS))

    async def close(self) -> None:
        await _call(self._client.close)


class PyMilvusProbeReader:
    def __init__(self, client: _SyncClient, sdk: MilvusSdk) -> None:
        self._client = client
        self._sdk = sdk

    async def describe_alias(self, alias: str) -> str:
        raw = await _call(lambda: self._client.describe_alias(alias, timeout=_TIMEOUT_SECONDS))
        return _alias_collection(raw)

    async def describe_collection(self, collection_name: str) -> MilvusCollectionDescriptor:
        raw = await _call(
            lambda: self._client.describe_collection(
                collection_name,
                timeout=_TIMEOUT_SECONDS,
            )
        )
        if not isinstance(raw, Mapping) or raw.get("collection_name") != collection_name:
            raise RuntimeError("Milvus returned malformed collection metadata")
        return MilvusCollectionDescriptor(
            collection_name=collection_name,
            family=SourceFamily.DOC,
            schema_version="health-v1",
            schema_sha256="sha256:" + "0" * 64,
            corpus_version="health-v1",
            embedding_model_version="health-v1",
            vector_dimension=2,
            dynamic_fields_enabled=False,
            consistency_level="Strong",
        )

    async def hybrid_search(
        self,
        request: MilvusHybridRequest,
    ) -> tuple[Mapping[str, object], ...]:
        raw = await _call(lambda: self._hybrid_search(request))
        return _hybrid_rows(raw)

    def _hybrid_search(self, request: MilvusHybridRequest) -> object:
        sdk_requests = [
            self._sdk.ann_search_request_factory(
                data=[request.channels[0].query],
                anns_field="bm25_sparse",
                param={"metric_type": "BM25", "params": {}},
                limit=request.channels[0].limit,
                expr=request.channels[0].filter_expression,
            ),
            self._sdk.ann_search_request_factory(
                data=[list(cast(tuple[float, ...], request.channels[1].query))],
                anns_field="dense_vector",
                param={"metric_type": "COSINE", "params": {}},
                limit=request.channels[1].limit,
                expr=request.channels[1].filter_expression,
            ),
        ]
        return self._client.hybrid_search(
            collection_name=request.collection_name,
            reqs=sdk_requests,
            ranker=self._sdk.ranker_factory(),
            limit=request.limit,
            output_fields=list(request.output_fields),
            consistency_level="Strong",
            timeout=_TIMEOUT_SECONDS,
        )

    async def query(
        self,
        request: MilvusQueryRequest,
    ) -> tuple[Mapping[str, object], ...]:
        raw = await _call(
            lambda: self._client.query(
                request.collection_name,
                filter=request.filter_expression,
                output_fields=list(request.output_fields),
                limit=request.limit,
                consistency_level="Strong",
                timeout=_TIMEOUT_SECONDS,
            )
        )
        return _mapping_rows(raw)

    async def close(self) -> None:
        await _call(self._client.close)


class PyMilvusDeniedProbe:
    def __init__(
        self,
        reader: _SyncClient,
        writer: _SyncClient,
        provisioner: _SyncClient,
        sdk: MilvusSdk,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._provisioner = provisioner
        self._sdk = sdk

    async def verify(self, collection_name: str) -> None:
        row = {
            "chunk_id": "h_" + "9" * 64,
            "content": "denied probe",
            "dense_vector": [0.0, 1.0],
            "tenant_id": "tap-health",
            "project_id": "tap-health",
            "allowed_group_ids": ["tap-health-denied"],
            "classification_rank": 0,
            "environment": "local",
            "corpus_version": "health-v1",
            "deleted": False,
        }
        await _assert_denied(
            lambda: self._reader.insert(collection_name, [row], timeout=_TIMEOUT_SECONDS),
            self._sdk.permission_error,
        )
        await _assert_denied(
            lambda: self._reader.delete(
                collection_name,
                ids=[row["chunk_id"]],
                timeout=_TIMEOUT_SECONDS,
            ),
            self._sdk.permission_error,
        )
        await _assert_denied(
            lambda: _search_one(self._writer, collection_name, self._sdk),
            self._sdk.permission_error,
        )
        await _assert_denied(
            lambda: self._writer.query(
                collection_name,
                filter="deleted == false",
                output_fields=["chunk_id"],
                limit=1,
                timeout=_TIMEOUT_SECONDS,
            ),
            self._sdk.permission_error,
        )
        for operation in (
            lambda: self._provisioner.insert(
                collection_name,
                [row],
                timeout=_TIMEOUT_SECONDS,
            ),
            lambda: self._provisioner.upsert(
                collection_name,
                [row],
                timeout=_TIMEOUT_SECONDS,
            ),
            lambda: self._provisioner.delete(
                collection_name,
                ids=[row["chunk_id"]],
                timeout=_TIMEOUT_SECONDS,
            ),
            lambda: _search_one(self._provisioner, collection_name, self._sdk),
            lambda: self._provisioner.query(
                collection_name,
                filter="deleted == false",
                output_fields=["chunk_id"],
                limit=1,
                timeout=_TIMEOUT_SECONDS,
            ),
        ):
            await _assert_denied(operation, self._sdk.permission_error)


class _HealthAdminUnavailable:
    async def ensure_user(self, username: str, password: SecretStr) -> None:
        raise RuntimeError("Milvus health does not have admin authority")

    async def ensure_role(self, role_name: str) -> None:
        raise RuntimeError("Milvus health does not have admin authority")

    async def replace_user_roles(
        self,
        username: str,
        role_names: frozenset[str],
    ) -> None:
        raise RuntimeError("Milvus health does not have admin authority")

    async def replace_role_grants(
        self,
        role_name: str,
        grants: frozenset[MilvusGrant],
    ) -> None:
        raise RuntimeError("Milvus health does not have admin authority")

    async def rotate_root_password(self, password: SecretStr) -> None:
        raise RuntimeError("Milvus health does not have admin authority")


async def connect_local_admin(
    settings: Mapping[str, str],
    *,
    client_factory: Callable[..., object],
) -> PyMilvusAdmin:
    uri = _setting(settings, "MILVUS_URI", "http://127.0.0.1:19530")
    database = _setting(settings, "MILVUS_DATABASE", "default")
    rotated = _setting(settings, "MILVUS_ROOT_PASSWORD", "tap-local-rotated-root")
    initial = _setting(settings, "MILVUS_INITIAL_ROOT_PASSWORD", "Milvus")
    allow_initial = _setting(settings, "TAP_ALLOW_INITIAL_MILVUS_ROOT", "0")
    if allow_initial not in {"0", "1"}:
        raise ValueError("TAP_ALLOW_INITIAL_MILVUS_ROOT must be exactly 0 or 1")
    rotated_client = cast(
        _SyncClient,
        client_factory(
            uri=uri,
            user="root",
            password=rotated,
            db_name=database,
            timeout=_TIMEOUT_SECONDS,
        ),
    )
    try:
        await _call(lambda: rotated_client.list_users(timeout=_TIMEOUT_SECONDS))
    except Exception:
        await _close_quietly(rotated_client)
        if allow_initial != "1":
            raise RuntimeError("Milvus rotated root authentication failed") from None
    else:
        return PyMilvusAdmin(
            rotated_client,
            SecretStr(rotated),
            authenticated_with_initial_root=False,
        )

    initial_client = cast(
        _SyncClient,
        client_factory(
            uri=uri,
            user="root",
            password=initial,
            db_name=database,
            timeout=_TIMEOUT_SECONDS,
        ),
    )
    try:
        await _call(lambda: initial_client.list_users(timeout=_TIMEOUT_SECONDS))
    except Exception:
        await _close_quietly(initial_client)
        raise RuntimeError("Milvus initial root authentication failed") from None
    try:
        await _call(
            lambda: initial_client.update_password(
                "root",
                initial,
                rotated,
                reset_connection=True,
                timeout=_TIMEOUT_SECONDS,
            )
        )
    except Exception:
        await _close_quietly(initial_client)
        raise RuntimeError("Milvus initial root rotation failed") from None
    await _close_quietly(initial_client)
    reconnected_client = cast(
        _SyncClient,
        client_factory(
            uri=uri,
            user="root",
            password=rotated,
            db_name=database,
            timeout=_TIMEOUT_SECONDS,
        ),
    )
    try:
        await _call(lambda: reconnected_client.list_users(timeout=_TIMEOUT_SECONDS))
    except Exception:
        await _close_quietly(reconnected_client)
        raise RuntimeError("Milvus rotated root reconnect failed") from None
    return PyMilvusAdmin(
        reconnected_client,
        SecretStr(rotated),
        authenticated_with_initial_root=False,
    )


def local_role_credentials(settings: Mapping[str, str]) -> MilvusRoleCredentials:
    return MilvusRoleCredentials(
        rotated_root_password=SecretStr(
            _setting(settings, "MILVUS_ROOT_PASSWORD", "tap-local-rotated-root")
        ),
        reader_username=_setting(settings, "MILVUS_READER_USERNAME", "tap_reader"),
        reader_password=SecretStr(
            _setting(settings, "MILVUS_READER_PASSWORD", "tap-local-reader-password")
        ),
        writer_username=_setting(settings, "MILVUS_WRITER_USERNAME", "tap_writer"),
        writer_password=SecretStr(
            _setting(settings, "MILVUS_WRITER_PASSWORD", "tap-local-writer-password")
        ),
        provisioner_username=_setting(
            settings,
            "MILVUS_PROVISIONER_USERNAME",
            "tap_provisioner",
        ),
        provisioner_password=SecretStr(
            _setting(
                settings,
                "MILVUS_PROVISIONER_PASSWORD",
                "tap-local-provisioner-password",
            )
        ),
    )


def build_probe_clients(
    settings: Mapping[str, str],
    *,
    sdk: MilvusSdk,
) -> MilvusProbeClients:
    uri = _setting(settings, "MILVUS_URI", "http://127.0.0.1:19530")
    database = _setting(settings, "MILVUS_DATABASE", "default")

    def connect(username: str, password: SecretStr) -> _SyncClient:
        return cast(
            _SyncClient,
            sdk.client_factory(
                uri=uri,
                user=username,
                password=password.get_secret_value(),
                db_name=database,
                timeout=_TIMEOUT_SECONDS,
            ),
        )

    provisioner_client = connect(
        _setting(settings, "MILVUS_PROVISIONER_USERNAME", "tap_provisioner"),
        SecretStr(
            _setting(
                settings,
                "MILVUS_PROVISIONER_PASSWORD",
                "tap-local-provisioner-password",
            )
        ),
    )
    writer_client = connect(
        _setting(settings, "MILVUS_WRITER_USERNAME", "tap_writer"),
        SecretStr(_setting(settings, "MILVUS_WRITER_PASSWORD", "tap-local-writer-password")),
    )
    reader_client = connect(
        _setting(settings, "MILVUS_READER_USERNAME", "tap_reader"),
        SecretStr(_setting(settings, "MILVUS_READER_PASSWORD", "tap-local-reader-password")),
    )
    denied_probe = PyMilvusDeniedProbe(
        reader=reader_client,
        writer=writer_client,
        provisioner=provisioner_client,
        sdk=sdk,
    )
    return MilvusProbeClients(
        admin=cast(MilvusAdmin, _HealthAdminUnavailable()),
        provisioner=PyMilvusProvisioner(provisioner_client, sdk),
        writer=PyMilvusWriter(writer_client),
        reader=PyMilvusProbeReader(reader_client, sdk),
        denied_probe=denied_probe,
    )


def build_reader_client(
    settings: Mapping[str, str],
    *,
    sdk: MilvusSdk,
) -> PyMilvusProbeReader:
    client = cast(
        _SyncClient,
        sdk.client_factory(
            uri=_setting(settings, "MILVUS_URI", "http://127.0.0.1:19530"),
            user=_setting(settings, "MILVUS_READER_USERNAME", "tap_reader"),
            password=_setting(
                settings,
                "MILVUS_READER_PASSWORD",
                "tap-local-reader-password",
            ),
            db_name=_setting(settings, "MILVUS_DATABASE", "default"),
            timeout=_TIMEOUT_SECONDS,
        ),
    )
    return PyMilvusProbeReader(client, sdk)


async def close_probe_clients(clients: MilvusProbeClients) -> None:
    for client in (clients.reader, clients.writer, clients.provisioner):
        close = getattr(client, "close", None)
        if callable(close):
            await cast(Callable[[], Awaitable[None]], close)()


async def _call[T](operation: Callable[[], T]) -> T:
    try:
        async with asyncio.timeout(_TIMEOUT_SECONDS):
            return await asyncio.to_thread(operation)
    except TimeoutError:
        raise RuntimeError("Milvus operation timed out") from None


async def _close_quietly(client: _SyncClient) -> None:
    try:
        await _call(client.close)
    except Exception:
        return


async def _assert_denied(
    operation: Callable[[], object],
    permission_error: type[BaseException],
) -> None:
    try:
        await _call(operation)
    except permission_error as error:
        if getattr(error, "compatible_code", None) == _PERMISSION_DENIED_COMPATIBLE_CODE:
            return
        raise RuntimeError("Milvus denial probe returned an unexpected provider error") from None
    raise RuntimeError("Milvus RBAC denial probe unexpectedly succeeded")


def _search_one(client: _SyncClient, collection_name: str, sdk: MilvusSdk) -> object:
    request = sdk.ann_search_request_factory(
        data=[[1.0, 0.0]],
        anns_field="dense_vector",
        param={"metric_type": "COSINE", "params": {}},
        limit=1,
        expr="deleted == false",
    )
    return client.hybrid_search(
        collection_name=collection_name,
        reqs=[request],
        ranker=sdk.ranker_factory(),
        limit=1,
        output_fields=["chunk_id"],
        timeout=_TIMEOUT_SECONDS,
    )


def _string_items(raw: object) -> set[str]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise RuntimeError("Milvus returned malformed identity metadata")
    return {item for item in raw if isinstance(item, str)}


def _role_names(raw: object) -> set[str]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise RuntimeError("Milvus returned malformed role metadata")
    names = set()
    for item in raw:
        if isinstance(item, str):
            names.add(item)
        elif isinstance(item, Mapping) and isinstance(item.get("role_name"), str):
            names.add(cast(str, item["role_name"]))
    return names


def _user_roles(raw: object) -> set[str]:
    if not isinstance(raw, Mapping):
        raise RuntimeError("Milvus returned malformed user metadata")
    roles = raw.get("roles", ())
    return _role_names(roles)


def _role_grants(raw: object) -> frozenset[MilvusGrant]:
    if not isinstance(raw, Mapping):
        raise RuntimeError("Milvus returned malformed grant metadata")
    privileges = raw.get("privileges", raw.get("grants", ()))
    if not isinstance(privileges, Sequence) or isinstance(privileges, (str, bytes)):
        raise RuntimeError("Milvus returned malformed grant metadata")
    grants = set()
    for item in privileges:
        if not isinstance(item, Mapping):
            raise RuntimeError("Milvus returned malformed grant metadata")
        privilege = item.get("privilege") or item.get("privilege_name")
        resource_name = item.get("object_name") or item.get("collection_name")
        if not isinstance(privilege, str) or not isinstance(resource_name, str):
            raise RuntimeError("Milvus returned malformed grant metadata")
        grants.add(MilvusGrant("collection", resource_name, privilege))
    return frozenset(grants)


def _grant_sort_key(grant: MilvusGrant) -> tuple[str, str, str]:
    return grant.resource_level, grant.resource_name, grant.privilege


def _alias_collection(raw: object) -> str:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("collection_name"), str):
        raise RuntimeError("Milvus returned malformed alias metadata")
    return cast(str, raw["collection_name"])


def _hybrid_rows(raw: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], list):
        raise RuntimeError("Milvus returned malformed health search results")
    rows = []
    for item in raw[0]:
        if not isinstance(item, Mapping) or not isinstance(item.get("entity"), Mapping):
            raise RuntimeError("Milvus returned malformed health search results")
        rows.append(dict(cast(Mapping[str, object], item["entity"])))
    return tuple(rows)


def _mapping_rows(raw: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
        raise RuntimeError("Milvus returned malformed health query results")
    return tuple(dict(cast(Mapping[str, object], item)) for item in raw)


def _privileges_for_role(role_name: str) -> frozenset[str]:
    privileges = {
        "tap_reader": READER_PRIVILEGES,
        "tap_writer": WRITER_PRIVILEGES,
        "tap_provisioner": PROVISIONER_PRIVILEGES,
    }.get(role_name)
    if privileges is None:
        raise ValueError("Milvus role name is outside the local closed set")
    return privileges


def _setting(settings: Mapping[str, str], name: str, default: str) -> str:
    value = settings.get(name, default)
    if not isinstance(value, str) or not value or len(value) > 1_024:
        raise ValueError(f"{name} must be a bounded non-empty setting")
    return value
