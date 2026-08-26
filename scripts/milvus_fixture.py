"""Publish or explicitly finalize the sanitized local Milvus fixture."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

from pymilvus import DataType, Function, FunctionType, MilvusClient  # type: ignore[import-untyped]

from tap.modules.knowledge.adapters.milvus.transport import (
    MilvusCollectionDescriptor,
    MilvusQueryRequest,
    _collection_descriptor,
)
from tap.operations.milvus.activation import LocalCorpusActivator
from tap.operations.milvus.async_call import deadline_then_settle_blocking_call
from tap.operations.milvus.client import (
    PyMilvusWriter,
    local_role_credentials,
    suppress_pymilvus_rpc_logging,
)
from tap.operations.milvus.contracts import (
    READER_BASE_PRIVILEGES,
    READER_TARGET_PRIVILEGES,
    WRITER_PRIVILEGES,
    MilvusProvisioner,
    MilvusPublishClients,
    MilvusScopedGrant,
)
from tap.operations.milvus.fixtures import (
    BM25_FUNCTION,
    CONTENT_ANALYZER,
    INDEXES,
    DocFixtureManifest,
    load_doc_fixture,
)
from tap.operations.milvus.publish import finalize_old_physical, publish_fixture

_TIMEOUT_SECONDS = 10.0
_FIXTURE = Path("apps/backend/tests/fixtures/milvus/doc-fixture-v1.json")
_ACTIVE_MARKER = Path(".local/milvus-active-corpus.json")
_ANALYZER_TEXTS = (
    "退款申请须由付款组审批。",
    "大额付款需要双人复核。",
    "Rollback requires recorded approval.",
    "Canary rollout checks health.",
)
_ANALYZER_TOKENS = (
    ("退款", "申请", "须", "由", "付款", "组", "审批"),
    ("大额", "付款", "需要", "双人", "复核"),
    ("rollback", "requir", "record", "approv"),
    ("Canary", "rollout", "checks", "health"),
)


class _FixtureProvisioner:
    def __init__(self, client: MilvusClient, database_name: str) -> None:
        if not database_name:
            raise ValueError("Milvus database name is required")
        self._client = client
        self._database_name = database_name

    async def create_collection(self, name: str, schema: Mapping[str, object]) -> None:
        await _call(lambda: self._create_collection(name, schema))

    async def collection_exists(self, name: str) -> bool:
        raw = await _call(
            lambda: self._client.list_collections(timeout=_TIMEOUT_SECONDS)
        )
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            raise RuntimeError("Milvus returned malformed collection inventory")
        if any(not isinstance(item, str) for item in raw):
            raise RuntimeError("Milvus returned malformed collection inventory")
        return name in raw

    async def has_collection_grant(self, name: str, role_name: str) -> bool:
        return bool(await self.collection_grants(name, role_name))

    async def collection_grants(
        self,
        name: str,
        role_name: str,
    ) -> frozenset[MilvusScopedGrant]:
        raw = await _call(
            lambda: self._client.describe_role(
                role_name,
                db_name=self._database_name,
                timeout=_TIMEOUT_SECONDS,
            )
        )
        if not isinstance(raw, Mapping) or raw.get("role") != role_name:
            raise RuntimeError("Milvus returned malformed grant metadata")
        privileges = raw.get("privileges", raw.get("grants", ()))
        if isinstance(privileges, (str, bytes)) or not isinstance(privileges, Sequence):
            raise RuntimeError("Milvus returned malformed grant metadata")
        scoped: set[MilvusScopedGrant] = set()
        reader_base: set[str] = set()
        seen: set[tuple[str, str, str, str, str]] = set()
        for item in privileges:
            if not isinstance(item, Mapping):
                raise RuntimeError("Milvus returned malformed grant metadata")
            object_type = item.get("object_type")
            resource_name = item.get("object_name")
            database_name = item.get("db_name")
            grant_role = item.get("role_name")
            privilege = item.get("privilege")
            dimensions = (
                object_type,
                resource_name,
                database_name,
                grant_role,
                privilege,
            )
            if any(type(value) is not str or not value for value in dimensions):
                raise RuntimeError("Milvus returned malformed grant metadata")
            exact_dimensions = cast(tuple[str, str, str, str, str], dimensions)
            if exact_dimensions in seen:
                raise RuntimeError("Milvus returned duplicate grant metadata")
            seen.add(exact_dimensions)
            if grant_role != role_name or database_name != self._database_name:
                raise RuntimeError("Milvus returned ambiguous scoped grant metadata")
            if role_name == "tap_reader" and resource_name == "*":
                if object_type != "Global":
                    raise RuntimeError(
                        "Milvus returned ambiguous reader base grant metadata"
                    )
                reader_base.add(cast(str, privilege))
                continue
            if resource_name == name:
                if object_type != "Collection":
                    raise RuntimeError(
                        "Milvus returned ambiguous scoped grant metadata"
                    )
                scoped.add(
                    MilvusScopedGrant(
                        role_name=grant_role,
                        object_type="Collection",
                        db_name=database_name,
                        object_name=resource_name,
                        privilege=cast(str, privilege),
                    )
                )
        if role_name == "tap_reader" and reader_base != READER_BASE_PRIVILEGES:
            raise RuntimeError("Milvus returned incomplete reader base grant metadata")
        return frozenset(scoped)

    def _create_collection(self, name: str, schema: Mapping[str, object]) -> object:
        sdk_schema = MilvusClient.create_schema(
            auto_id=False,
            enable_dynamic_field=False,
            description=cast(str, schema["description"]),
        )
        fields = cast(Sequence[Mapping[str, object]], schema["fields"])
        for field in fields:
            params = dict(cast(Mapping[str, object], field["params"]))
            kwargs: dict[str, object] = {
                "field_name": field["name"],
                "datatype": DataType(cast(int, field["type"])),
                "is_primary": field["is_primary"],
                "nullable": field["nullable"],
                **params,
            }
            element_type = field["element_type"]
            if element_type is not None:
                kwargs["element_type"] = DataType(cast(int, element_type))
            cast(Callable[..., object], sdk_schema.add_field)(**kwargs)
        cast(Callable[..., object], sdk_schema.add_function)(
            Function(
                name=cast(str, BM25_FUNCTION["name"]),
                function_type=FunctionType.BM25,
                input_field_names=list(
                    cast(tuple[str, ...], BM25_FUNCTION["input_field_names"])
                ),
                output_field_names=list(
                    cast(tuple[str, ...], BM25_FUNCTION["output_field_names"])
                ),
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

    def _create_indexes(self, name: str) -> object:
        params = self._client.prepare_index_params()
        add_index = cast(Callable[..., object], params.add_index)
        for field_name, definition in INDEXES.items():
            kwargs = {
                "field_name": field_name,
                "index_name": field_name,
                "index_type": definition["index_type"],
            }
            if "metric_type" in definition:
                kwargs["metric_type"] = definition["metric_type"]
            if "params" in definition:
                kwargs["params"] = definition["params"]
            add_index(**kwargs)
        result = self._client.create_index(name, params, timeout=_TIMEOUT_SECONDS)
        self._client.load_collection(name, timeout=_TIMEOUT_SECONDS)
        self._client.get_load_state(name, timeout=_TIMEOUT_SECONDS)
        return result

    async def grant_collection(self, name: str, role_name: str) -> None:
        for privilege in sorted(_role_privileges(role_name)):

            def grant(privilege: str = privilege) -> object:
                return self._client.grant_privilege_v2(
                    role_name,
                    privilege,
                    name,
                    db_name=self._database_name,
                    timeout=_TIMEOUT_SECONDS,
                )

            await _call(grant)

    async def revoke_collection(self, name: str, role_name: str) -> None:
        for privilege in sorted(_role_privileges(role_name)):

            def revoke(privilege: str = privilege) -> object:
                return self._client.revoke_privilege_v2(
                    role_name,
                    privilege,
                    name,
                    db_name=self._database_name,
                    timeout=_TIMEOUT_SECONDS,
                )

            await _call(revoke)

    async def create_alias(self, alias: str, collection_name: str) -> None:
        await _call(
            lambda: self._client.create_alias(
                collection_name,
                alias,
                timeout=_TIMEOUT_SECONDS,
            )
        )

    async def alter_alias(self, alias: str, collection_name: str) -> None:
        if await self.describe_alias(alias) is None:
            await self.create_alias(alias, collection_name)
            return
        await _call(
            lambda: self._client.alter_alias(
                collection_name,
                alias,
                timeout=_TIMEOUT_SECONDS,
            )
        )

    async def describe_alias(self, alias: str) -> str | None:
        raw_aliases = await _call(
            lambda: self._client.list_aliases(timeout=_TIMEOUT_SECONDS)
        )
        aliases = _alias_names(raw_aliases)
        if alias not in aliases:
            return None
        raw = await _call(
            lambda: self._client.describe_alias(alias, timeout=_TIMEOUT_SECONDS)
        )
        if not isinstance(raw, Mapping) or not isinstance(
            raw.get("collection_name"), str
        ):
            raise RuntimeError("Milvus returned malformed alias metadata")
        return cast(str, raw["collection_name"])

    async def drop_alias(self, alias: str) -> None:
        await _call(lambda: self._client.drop_alias(alias, timeout=_TIMEOUT_SECONDS))

    async def drop_collection(self, name: str) -> None:
        await _call(
            lambda: self._client.drop_collection(name, timeout=_TIMEOUT_SECONDS)
        )


class _FixtureReader:
    def __init__(self, client: MilvusClient) -> None:
        self._client = client

    async def describe_alias(self, alias: str) -> str:
        raw = await _call(
            lambda: self._client.describe_alias(alias, timeout=_TIMEOUT_SECONDS)
        )
        if not isinstance(raw, Mapping) or not isinstance(
            raw.get("collection_name"), str
        ):
            raise RuntimeError("Milvus returned malformed alias metadata")
        return cast(str, raw["collection_name"])

    async def describe_collection(
        self, collection_name: str
    ) -> MilvusCollectionDescriptor:
        raw_collection = await _call(
            lambda: self._client.describe_collection(
                collection_name,
                timeout=_TIMEOUT_SECONDS,
            )
        )
        raw_indexes = []
        for index_name in INDEXES:

            def describe_index(index_name: str = index_name) -> object:
                return self._client.describe_index(
                    collection_name,
                    index_name,
                    timeout=_TIMEOUT_SECONDS,
                )

            raw_indexes.append(await _call(describe_index))
        return _collection_descriptor(
            raw_collection,
            tuple(raw_indexes),
            expected_collection=collection_name,
        )

    async def hybrid_search(self, request: object) -> tuple[Mapping[str, object], ...]:
        raise RuntimeError("fixture publisher does not perform hybrid search")

    async def query(
        self, request: MilvusQueryRequest
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
        if not isinstance(raw, list) or any(
            not isinstance(item, Mapping) for item in raw
        ):
            raise RuntimeError("Milvus returned malformed fixture query results")
        return tuple(dict(cast(Mapping[str, object], item)) for item in raw)

    async def query_persisted_rows(
        self,
        collection_name: str,
        filter_expression: str,
        output_fields: tuple[str, ...],
        limit: int,
    ) -> tuple[Mapping[str, object], ...]:
        raw = await _call(
            lambda: self._client.query(
                collection_name,
                filter=filter_expression,
                output_fields=list(output_fields),
                limit=limit,
                consistency_level="Strong",
                timeout=_TIMEOUT_SECONDS,
            )
        )
        if not isinstance(raw, list) or any(
            not isinstance(item, Mapping) for item in raw
        ):
            raise RuntimeError("Milvus returned malformed fixture query results")
        return tuple(dict(cast(Mapping[str, object], item)) for item in raw)

    async def close(self) -> None:
        await _call(self._client.close)


async def _run(args: argparse.Namespace) -> None:
    settings = dict(os.environ)
    credentials = local_role_credentials(settings)
    provisioner_client = _connect(
        settings,
        credentials.provisioner_username,
        credentials.provisioner_password.get_secret_value(),
    )
    writer_client = _connect(
        settings,
        credentials.writer_username,
        credentials.writer_password.get_secret_value(),
    )
    reader_client = _connect(
        settings,
        credentials.reader_username,
        credentials.reader_password.get_secret_value(),
    )
    provisioner = _FixtureProvisioner(
        provisioner_client,
        settings.get("MILVUS_DATABASE", "default"),
    )
    writer = PyMilvusWriter(writer_client)
    reader = _FixtureReader(reader_client)
    clients = MilvusPublishClients(
        provisioner=cast(MilvusProvisioner, provisioner),
        writer=writer,
        reader=reader,
    )
    try:
        if args.command == "finalize":
            await finalize_old_physical(clients, args.old_physical)
            return
        manifest = load_doc_fixture(args.fixture)
        await _verify_analyzer(provisioner_client)
        await publish_fixture(
            clients,
            manifest,
            _deterministic_vectors(manifest),
            LocalCorpusActivator(args.active_marker),
        )
    finally:
        for client in (reader_client, writer_client, provisioner_client):
            try:
                await _call(client.close)
            except Exception:
                pass


async def _verify_analyzer(client: MilvusClient) -> None:
    raw = await _call(
        lambda: client.run_analyzer(
            list(_ANALYZER_TEXTS),
            analyzer_params=CONTENT_ANALYZER,
            with_detail=False,
            timeout=_TIMEOUT_SECONDS,
        )
    )
    if not isinstance(raw, list) or len(raw) != len(_ANALYZER_TOKENS):
        raise RuntimeError(
            "Milvus analyzer behavior is incompatible with doc-schema-v1"
        )
    tokens = tuple(tuple(getattr(item, "tokens", ())) for item in raw)
    if tokens != _ANALYZER_TOKENS:
        raise RuntimeError(
            "Milvus analyzer behavior is incompatible with doc-schema-v1"
        )


def _deterministic_vectors(
    manifest: DocFixtureManifest,
) -> dict[str, tuple[float, ...]]:
    vectors = {}
    for index, chunk in enumerate(manifest.chunks):
        vector = [0.0] * manifest.vector_dimension
        vector[index] = 1.0
        vectors[chunk.chunk_id] = tuple(vector)
    return vectors


def _connect(
    settings: Mapping[str, str],
    username: str,
    password: str,
) -> MilvusClient:
    return MilvusClient(
        uri=settings.get("MILVUS_URI", "http://127.0.0.1:19530"),
        user=username,
        password=password,
        db_name=settings.get("MILVUS_DATABASE", "default"),
        timeout=_TIMEOUT_SECONDS,
    )


def _role_privileges(role_name: str) -> frozenset[str]:
    privileges = {
        "tap_writer": WRITER_PRIVILEGES,
        "tap_reader": READER_TARGET_PRIVILEGES,
    }.get(role_name)
    if privileges is None:
        raise ValueError("fixture role is outside the closed set")
    return privileges


def _alias_names(raw: object) -> frozenset[str]:
    if isinstance(raw, Mapping):
        raw = raw.get("aliases")
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise RuntimeError("Milvus returned malformed alias inventory")
    if any(not isinstance(item, str) for item in raw):
        raise RuntimeError("Milvus returned malformed alias inventory")
    return frozenset(cast(Sequence[str], raw))


async def _call[T](operation: Callable[[], T]) -> T:
    return await deadline_then_settle_blocking_call(
        operation,
        timeout_seconds=_TIMEOUT_SECONDS,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage the sanitized local Milvus fixture"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    publish = subparsers.add_parser("publish")
    publish.add_argument("--fixture", type=Path, default=_FIXTURE)
    publish.add_argument("--active-marker", type=Path, default=_ACTIVE_MARKER)
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--old-physical", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        with suppress_pymilvus_rpc_logging():
            asyncio.run(_run(args))
    except Exception:
        print("Milvus fixture operation failed.", file=sys.stderr)
        return 1
    print("Milvus fixture operation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
