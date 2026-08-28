"""Provider-light contracts for local Milvus operations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import SecretStr

from tap.modules.knowledge.adapters.milvus.transport import (
    MilvusCollectionDescriptor,
    MilvusReader,
)


def validate_milvus_role_usernames(
    *,
    reader_username: str,
    writer_username: str,
    provisioner_username: str,
) -> None:
    """Require three distinct non-root identities before opening any client."""

    usernames = (reader_username, writer_username, provisioner_username)
    if any(not isinstance(username, str) for username in usernames):
        raise TypeError("Milvus RBAC identities must be strings")
    normalized = tuple(username.casefold() for username in usernames)
    if (
        any(not username or username == "root" for username in normalized)
        or len(set(normalized)) != 3
    ):
        raise ValueError("Milvus RBAC requires three unique non-root identities")


@dataclass(frozen=True, slots=True)
class MilvusRoleCredentials:
    rotated_root_password: SecretStr = field(repr=False)
    reader_username: str
    reader_password: SecretStr = field(repr=False)
    writer_username: str
    writer_password: SecretStr = field(repr=False)
    provisioner_username: str
    provisioner_password: SecretStr = field(repr=False)

    def __post_init__(self) -> None:
        validate_milvus_role_usernames(
            reader_username=self.reader_username,
            writer_username=self.writer_username,
            provisioner_username=self.provisioner_username,
        )


@dataclass(frozen=True, slots=True)
class MilvusGrant:
    resource_level: Literal["instance", "database", "collection"]
    resource_name: str
    privilege: str


@dataclass(frozen=True, slots=True)
class MilvusScopedGrant:
    """One fully qualified privilege returned by scoped grant inventory."""

    role_name: str
    object_type: Literal["Collection"]
    db_name: str
    object_name: str
    privilege: str

    @property
    def resource_level(self) -> Literal["collection"]:
        return "collection"

    @property
    def database_name(self) -> str:
        return self.db_name

    @property
    def resource_name(self) -> str:
        return self.object_name


READER_BASE_PRIVILEGES = frozenset({"DescribeAlias", "DescribeCollection"})
READER_TARGET_PRIVILEGES = frozenset({"Search", "Query"})
WRITER_PRIVILEGES = frozenset({"Insert", "Upsert", "Delete", "Flush", "GetFlushState"})
PROVISIONER_PRIVILEGES = frozenset(
    {
        "CreateCollection",
        "DropCollection",
        "CreateIndex",
        "IndexDetail",
        "Load",
        "Release",
        "GetLoadState",
        "GetLoadingProgress",
        "CreateAlias",
        "DropAlias",
        "DescribeAlias",
        "ManageOwnership",
        "SelectOwnership",
    }
)
_PROVISIONER_INSTANCE_PRIVILEGES = frozenset(
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
READER_BASE_GRANTS = frozenset(
    MilvusGrant("instance", "*", privilege) for privilege in READER_BASE_PRIVILEGES
)
WRITER_BASE_GRANTS = frozenset(
    MilvusGrant("collection", "*", privilege) for privilege in WRITER_PRIVILEGES
)
PROVISIONER_BASE_GRANTS = frozenset(
    MilvusGrant(
        "instance" if privilege in _PROVISIONER_INSTANCE_PRIVILEGES else "collection",
        "*",
        privilege,
    )
    for privilege in PROVISIONER_PRIVILEGES
)


class MilvusAdmin(Protocol):
    async def ensure_user(self, username: str, password: SecretStr) -> None: ...

    async def ensure_role(self, role_name: str) -> None: ...

    async def replace_user_roles(
        self,
        username: str,
        role_names: frozenset[str],
    ) -> None: ...

    async def replace_role_grants(
        self,
        role_name: str,
        grants: frozenset[MilvusGrant],
    ) -> None: ...

    async def rotate_root_password(self, password: SecretStr) -> None: ...


class MilvusProvisioner(Protocol):
    async def create_collection(self, name: str, schema: Mapping[str, object]) -> None: ...

    async def create_indexes(self, name: str) -> None: ...

    async def grant_collection(self, name: str, role_name: str) -> None: ...

    async def revoke_collection(self, name: str, role_name: str) -> None: ...

    async def create_alias(self, alias: str, collection_name: str) -> None: ...

    async def alter_alias(self, alias: str, collection_name: str) -> None: ...

    async def describe_alias(self, alias: str) -> str | None: ...

    async def drop_alias(self, alias: str) -> None: ...

    async def drop_collection(self, name: str) -> None: ...


class MilvusDocProvisioner(Protocol):
    async def create_collection(self, name: str, schema: Mapping[str, object]) -> None: ...

    async def create_indexes(
        self,
        name: str,
        schema: Mapping[str, object] | None = None,
    ) -> None: ...

    async def validate_collection_indexes(
        self,
        name: str,
        schema: Mapping[str, object],
    ) -> None: ...

    async def ensure_loaded(self, name: str) -> None: ...

    async def is_loaded(self, name: str) -> bool: ...

    async def grant_collection(self, name: str, role_name: str) -> None: ...

    async def revoke_collection(self, name: str, role_name: str) -> None: ...

    async def collection_grants(
        self,
        name: str,
        role_name: str,
    ) -> frozenset[MilvusScopedGrant]: ...

    async def create_alias(self, alias: str, collection_name: str) -> None: ...

    async def alter_alias(self, alias: str, collection_name: str) -> None: ...

    async def drop_alias(self, alias: str) -> None: ...

    async def drop_collection(self, name: str) -> None: ...

    async def close(self) -> None: ...


class MilvusWriter(Protocol):
    async def insert(self, name: str, rows: tuple[Mapping[str, object], ...]) -> None: ...

    async def upsert(self, name: str, rows: tuple[Mapping[str, object], ...]) -> None: ...

    async def delete(self, name: str, chunk_ids: tuple[str, ...]) -> None: ...

    async def flush(self, name: str) -> None: ...

    async def close(self) -> None: ...


class MilvusDocReader(Protocol):
    async def collection_exists(self, name: str) -> bool: ...

    async def describe_alias(self, alias: str) -> str | None: ...

    async def describe_collection_schema(
        self,
        name: str,
        schema: Mapping[str, object],
    ) -> MilvusCollectionDescriptor: ...

    async def query_persisted_rows(
        self,
        collection_name: str,
        filter_expression: str,
        output_fields: tuple[str, ...],
        limit: int,
    ) -> tuple[Mapping[str, object], ...]: ...

    async def close(self) -> None: ...


class MilvusDeniedProbe(Protocol):
    async def verify(self, collection_name: str) -> None: ...


@dataclass(frozen=True, slots=True)
class MilvusProbeClients:
    admin: MilvusAdmin
    provisioner: MilvusProvisioner
    writer: MilvusWriter
    reader: MilvusReader
    denied_probe: MilvusDeniedProbe


@dataclass(frozen=True, slots=True)
class MilvusPublishClients:
    provisioner: MilvusProvisioner
    writer: MilvusWriter
    reader: MilvusReader


@dataclass(frozen=True, slots=True)
class MilvusHealthReport:
    probe_id: str
    allowed_hits: int
    denied_hits: int
    cleanup_complete: bool
