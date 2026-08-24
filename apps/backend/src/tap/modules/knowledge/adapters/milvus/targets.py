"""Bind one request to one validated physical Milvus collection."""

from __future__ import annotations

import re
from dataclasses import dataclass

from tap.modules.knowledge.adapters.milvus.config import MilvusIndexTarget
from tap.modules.knowledge.adapters.milvus.transport import MilvusReader
from tap.modules.knowledge.ports.errors import SearchUnavailable

_SAFE_COLLECTION_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,254}\Z")


@dataclass(frozen=True, slots=True)
class BoundMilvusTarget:
    configured: MilvusIndexTarget
    physical_collection: str


async def bind_target(
    reader: MilvusReader,
    target: MilvusIndexTarget,
) -> BoundMilvusTarget:
    """Resolve an alias once and validate the immutable target description."""
    physical_collection = await reader.describe_alias(target.alias)
    if (
        not isinstance(physical_collection, str)
        or _SAFE_COLLECTION_NAME.fullmatch(physical_collection) is None
        or not physical_collection.startswith(target.physical_name_prefix)
    ):
        raise SearchUnavailable("Milvus alias resolved outside configured target")

    descriptor = await reader.describe_collection(physical_collection)
    if (
        descriptor.collection_name != physical_collection
        or descriptor.family is not target.family
        or descriptor.schema_version != target.schema_version
        or descriptor.schema_sha256 != target.schema_sha256
        or descriptor.corpus_version != target.corpus_version
        or descriptor.embedding_model_version != target.embedding_model_version
        or descriptor.vector_dimension != target.vector_dimension
        or descriptor.dynamic_fields_enabled is not False
        or descriptor.consistency_level != "Strong"
    ):
        raise SearchUnavailable("Milvus collection does not match configured target")

    return BoundMilvusTarget(
        configured=target,
        physical_collection=physical_collection,
    )
