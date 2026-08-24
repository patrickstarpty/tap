from __future__ import annotations

import math

import pytest

from tap.modules.knowledge.adapters.milvus.config import MilvusIndexTarget
from tap.modules.knowledge.adapters.milvus.mapping import map_milvus_hit
from tap.modules.knowledge.adapters.milvus.targets import BoundMilvusTarget
from tap.modules.knowledge.domain.models import (
    ContentRole,
    DocumentAnchor,
    RevisionKind,
    SourceFamily,
)
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


def bound_target() -> BoundMilvusTarget:
    return BoundMilvusTarget(
        configured=doc_target(),
        physical_collection="kb_doc_v1_corpus_fixture_v1",
    )


def valid_doc_row() -> dict[str, object]:
    return {
        "chunk_id": "h_" + "1" * 64,
        "logical_chunk_id": "h_" + "2" * 64,
        "root_id": "h_" + "3" * 64,
        "parent_id": None,
        "title": "Payment policy",
        "content": "Refunds require an approved request.",
        "content_role": "source",
        "index_family": "doc",
        "physical_collection": "kb_doc_v1_corpus_fixture_v1",
        "schema_version": "doc-schema-v1",
        "corpus_version": "corpus-fixture-v1",
        "embedding_model_version": "research-embedding-v1",
        "source_id": "blob:handbook/payment-policy",
        "source_type": "doc",
        "revision_kind": "blob_version",
        "source_revision": "2026-08-24T00:00:00Z",
        "source_content_hash": "sha256:" + "4" * 64,
        "chunk_content_hash": "sha256:" + "5" * 64,
        "anchor_json": '{"type":"document","headingPath":["Payments"],"page":1}',
        "derived_from_chunk_ids": [],
        "score": 0.75,
        "provider_request_id": "milvus-request-v1",
    }


def test_map_milvus_hit_builds_provider_neutral_provenance_from_a_closed_doc_row() -> None:
    hit = map_milvus_hit(valid_doc_row(), bound_target(), local_rank=1)

    assert hit.family is SourceFamily.DOC
    assert hit.chunk_id == "h_" + "1" * 64
    assert hit.logical_chunk_id == "h_" + "2" * 64
    assert hit.root_id == "h_" + "3" * 64
    assert hit.parent_id is None
    assert hit.content_role is ContentRole.SOURCE
    assert hit.source.revision_kind is RevisionKind.BLOB_VERSION
    assert hit.source.anchor == DocumentAnchor(heading_path=("Payments",), page=1)
    assert hit.index_revision.physical_index == bound_target().physical_collection
    assert hit.index_revision.schema_version == "doc-schema-v1"
    assert hit.index_revision.corpus_version == "corpus-fixture-v1"
    assert hit.local_rank == 1


def test_map_milvus_hit_preserves_bounded_multiline_document_content() -> None:
    row = {**valid_doc_row(), "content": "Approval steps:\n1. Review\n2. Approve"}

    hit = map_milvus_hit(row, bound_target(), local_rank=1)

    assert hit.content == "Approval steps:\n1. Review\n2. Approve"


@pytest.mark.parametrize(
    ("field", "forged"),
    (
        ("chunk_id", "not-a-chunk-id"),
        ("logical_chunk_id", "h_" + "A" * 64),
        ("root_id", None),
        ("parent_id", "not-a-chunk-id"),
        ("title", 7),
        ("content", ""),
        ("content_role", "untrusted"),
        ("index_family", "code"),
        ("physical_collection", "kb_doc_v1_forged"),
        ("schema_version", "doc-schema-v2"),
        ("corpus_version", "corpus-forged-v1"),
        ("embedding_model_version", "other-model-v1"),
        ("source_id", ""),
        ("source_type", "code"),
        ("revision_kind", "git_commit"),
        ("source_revision", None),
        ("source_content_hash", "sha256:" + "G" * 64),
        ("chunk_content_hash", "sha256:" + "6" * 63),
        ("anchor_json", '{"type":"code","path":"secrets.txt"}'),
        ("derived_from_chunk_ids", ["not-a-chunk-id"]),
        ("score", math.inf),
        ("provider_request_id", 17),
    ),
)
def test_map_milvus_hit_rejects_each_forged_row_value(field: str, forged: object) -> None:
    row = {**valid_doc_row(), field: forged}

    with pytest.raises(SearchUnavailable):
        map_milvus_hit(row, bound_target(), local_rank=1)


@pytest.mark.parametrize("field", tuple(valid_doc_row()))
def test_map_milvus_hit_rejects_each_missing_row_key(field: str) -> None:
    row = valid_doc_row()
    del row[field]

    with pytest.raises(SearchUnavailable):
        map_milvus_hit(row, bound_target(), local_rank=1)


def test_map_milvus_hit_rejects_extra_row_and_anchor_fields() -> None:
    extra_row = {**valid_doc_row(), "tenant_id": "must-not-be-returned"}
    widened_anchor = {
        **valid_doc_row(),
        "anchor_json": (
            '{"type":"document","headingPath":["Payments"],"page":1,"privateLocator":"secret"}'
        ),
    }

    with pytest.raises(SearchUnavailable):
        map_milvus_hit(extra_row, bound_target(), local_rank=1)
    with pytest.raises(SearchUnavailable):
        map_milvus_hit(widened_anchor, bound_target(), local_rank=1)


@pytest.mark.parametrize("local_rank", (0, -1, True, 1.5))
def test_map_milvus_hit_rejects_non_positive_or_non_integer_local_rank(
    local_rank: object,
) -> None:
    with pytest.raises(SearchUnavailable):
        map_milvus_hit(valid_doc_row(), bound_target(), local_rank=local_rank)  # type: ignore[arg-type]
