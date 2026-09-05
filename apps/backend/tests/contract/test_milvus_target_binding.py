from __future__ import annotations

from dataclasses import replace

import pytest

from tap.modules.knowledge.adapters.milvus.config import MilvusIndexTarget
from tap.modules.knowledge.adapters.milvus.targets import BoundMilvusTarget, bind_target
from tap.modules.knowledge.adapters.milvus.transport import MilvusCollectionDescriptor
from tap.modules.knowledge.domain.models import SourceFamily
from tap.modules.knowledge.ports.errors import SearchUnavailable


def doc_target() -> MilvusIndexTarget:
    return MilvusIndexTarget(
        family=SourceFamily.DOC,
        alias="kb_doc_active",
        physical_name_prefix="kb_doc_v1_",
        schema_version="doc-schema-v1",
        schema_sha256="sha256:" + "c" * 64,
        corpus_version="corpus-fixture-v1",
        embedding_model_version="research-embedding-v1",
        vector_dimension=1536,
    )


def descriptor() -> MilvusCollectionDescriptor:
    return MilvusCollectionDescriptor(
        collection_name="kb_doc_v1_corpus_fixture_v1",
        family=SourceFamily.DOC,
        schema_version="doc-schema-v1",
        schema_sha256="sha256:" + "c" * 64,
        corpus_version="corpus-fixture-v1",
        embedding_model_version="research-embedding-v1",
        vector_dimension=1536,
        dynamic_fields_enabled=False,
        consistency_level="Strong",
    )


class AliasReader:
    def __init__(self, collection_descriptor: MilvusCollectionDescriptor | None = None) -> None:
        self.alias_calls: list[str] = []
        self.collection_calls: list[str] = []
        self.collection_descriptor = collection_descriptor or descriptor()

    async def describe_alias(self, alias: str) -> str:
        self.alias_calls.append(alias)
        return "kb_doc_v1_corpus_fixture_v1"

    async def describe_collection(self, collection_name: str) -> MilvusCollectionDescriptor:
        self.collection_calls.append(collection_name)
        return self.collection_descriptor


@pytest.mark.asyncio
async def test_bind_target_resolves_alias_once_and_validates_the_physical_description() -> None:
    reader = AliasReader()

    bound = await bind_target(reader, doc_target())

    assert bound == BoundMilvusTarget(
        configured=doc_target(),
        physical_collection="kb_doc_v1_corpus_fixture_v1",
    )
    assert reader.alias_calls == ["kb_doc_active"]
    assert reader.collection_calls == ["kb_doc_v1_corpus_fixture_v1"]


@pytest.mark.parametrize(
    ("field", "forged"),
    (
        ("collection_name", "kb_doc_v1_other"),
        ("family", SourceFamily.CODE),
        ("schema_version", "doc-schema-v2"),
        ("schema_sha256", "sha256:" + "d" * 64),
        ("corpus_version", "corpus-other-v1"),
        ("embedding_model_version", "other-embedding-v1"),
        ("vector_dimension", 768),
        ("dynamic_fields_enabled", True),
        ("consistency_level", "Bounded"),
    ),
)
@pytest.mark.asyncio
async def test_bind_target_rejects_each_mismatched_or_widened_descriptor(
    field: str,
    forged: object,
) -> None:
    reader = AliasReader(replace(descriptor(), **{field: forged}))

    with pytest.raises(SearchUnavailable, match="collection does not match configured target"):
        await bind_target(reader, doc_target())


@pytest.mark.asyncio
async def test_bind_target_rejects_a_physical_name_outside_the_configured_prefix() -> None:
    class ForgedAliasReader(AliasReader):
        async def describe_alias(self, alias: str) -> str:
            self.alias_calls.append(alias)
            return "attacker_collection"

    reader = ForgedAliasReader()

    with pytest.raises(SearchUnavailable, match="alias resolved outside configured target"):
        await bind_target(reader, doc_target())

    assert reader.collection_calls == []


@pytest.mark.asyncio
async def test_strict_bind_target_rejects_a_prefix_sharing_unowned_name() -> None:
    base = "kb_doc_v1_tapper_demo"

    class ForgedAliasReader(AliasReader):
        async def describe_alias(self, alias: str) -> str:
            self.alias_calls.append(alias)
            return base + "_unowned"

    reader = ForgedAliasReader()
    target = replace(
        doc_target(),
        physical_name_prefix=base,
        exact_generation_names=True,
    )

    with pytest.raises(SearchUnavailable, match="alias resolved outside configured target"):
        await bind_target(reader, target)

    assert reader.collection_calls == []
