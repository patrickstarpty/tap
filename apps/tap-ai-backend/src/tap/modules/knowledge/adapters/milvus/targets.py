"""Bind one request to one validated physical Milvus collection."""

from __future__ import annotations

from dataclasses import dataclass

from tap.modules.knowledge.adapters.milvus.config import (
    MilvusIndexTarget,
    is_owned_physical_collection,
)
from tap.modules.knowledge.adapters.milvus.transport import MilvusReader
from tap.modules.knowledge.ports.errors import SearchUnavailable


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
    if not is_owned_physical_collection(
        target.physical_name_prefix,
        physical_collection,
        exact_generation_names=target.exact_generation_names,
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
