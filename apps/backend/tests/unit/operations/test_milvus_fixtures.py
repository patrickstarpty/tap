from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tap.operations.milvus.fixtures import (
    EXPECTED_QUERY_CASE_IDS,
    EXPECTED_SOURCE_IDS,
    build_collection_schema,
    collection_description,
    content_hash,
    fixture_rows,
    load_doc_fixture,
    load_query_cases,
    manifest_sha256,
    schema_sha256,
    sha256_id,
    validate_collection_descriptor,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "milvus"
DOC_FIXTURE = FIXTURES / "doc-fixture-v1.json"
QUERY_FIXTURE = FIXTURES / "query-cases-v1.json"


def test_loads_the_closed_sanitized_fixture_and_exact_query_cases() -> None:
    manifest = load_doc_fixture(DOC_FIXTURE)
    cases = load_query_cases(QUERY_FIXTURE)

    assert len(manifest.chunks) == 12
    assert {chunk.source_id for chunk in manifest.chunks} == EXPECTED_SOURCE_IDS
    assert len(cases) == 8
    assert {case.case_id for case in cases} == EXPECTED_QUERY_CASE_IDS
    assert {case.tenant_id for case in cases} == {"tenant-a", "tenant-b"}
    assert {case.project_id for case in cases} == {"project-a", "project-b"}
    assert any(not case.expected_source_ids for case in cases)
    assert next(
        case for case in cases if case.case_id == "payment-subtree-card-only"
    ).expected_source_ids == ("blob:fixture/payment/card",)


def test_ids_hashes_and_schema_digest_are_deterministic_and_canonical() -> None:
    manifest = load_doc_fixture(DOC_FIXTURE)

    assert sha256_id("e\u0301\r\nline") == sha256_id("é\nline")
    assert content_hash("e\u0301\r\nline") == content_hash("é\nline")
    assert (
        schema_sha256() == "sha256:998b3ca8933a0ad33e61d2acc6b5aa629b10fa691f42860bbe3fe2074402c71f"
    )
    assert manifest.schema_sha256 == schema_sha256()
    assert manifest_sha256(manifest) == manifest_sha256(load_doc_fixture(DOC_FIXTURE))


@pytest.mark.parametrize(
    ("path", "mutation"),
    [
        (("chunks", 0, "chunkId"), "h_" + "0" * 64),
        (("chunks", 0, "logicalChunkId"), "h_" + "1" * 64),
        (("chunks", 0, "sourceContentHash"), "sha256:" + "0" * 64),
        (("chunks", 0, "chunkContentHash"), "sha256:" + "1" * 64),
        (("schemaSha256",), "sha256:" + "2" * 64),
        (("vectorDimension",), 2),
        (("embeddingModelVersion",), "mixed-model-v2"),
        (("physicalCollection",), "wrong_family_v1"),
    ],
)
def test_doc_loader_rejects_identity_hash_schema_and_target_mismatch(
    tmp_path: Path,
    path: tuple[str | int, ...],
    mutation: object,
) -> None:
    raw = json.loads(DOC_FIXTURE.read_text())
    target = raw
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = mutation
    candidate = tmp_path / "fixture.json"
    candidate.write_text(json.dumps(raw))

    with pytest.raises(ValueError):
        load_doc_fixture(candidate)


def test_doc_loader_rejects_duplicate_or_extra_source_ids(tmp_path: Path) -> None:
    raw = json.loads(DOC_FIXTURE.read_text())
    raw["chunks"][1] = dict(raw["chunks"][0])
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        load_doc_fixture(duplicate)

    raw = json.loads(DOC_FIXTURE.read_text())
    extra = dict(raw["chunks"][0])
    extra["sourceId"] = "blob:fixture/not-allowed"
    raw["chunks"].append(extra)
    widened = tmp_path / "widened.json"
    widened.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        load_doc_fixture(widened)


def test_json_loader_rejects_duplicate_keys_and_widened_records(tmp_path: Path) -> None:
    raw = DOC_FIXTURE.read_text()
    duplicate_key = tmp_path / "duplicate-key.json"
    duplicate_key.write_text(
        raw.replace('"schemaVersion":', '"unexpected":true,"unexpected":false,"schemaVersion":', 1)
    )
    with pytest.raises(ValueError):
        load_doc_fixture(duplicate_key)

    widened = json.loads(raw)
    widened["chunks"][0]["sourceType"] = "doc"
    widened_path = tmp_path / "widened.json"
    widened_path.write_text(json.dumps(widened))
    with pytest.raises(ValueError):
        load_doc_fixture(widened_path)


def test_schema_is_closed_strong_and_contains_exact_analyzer_function_and_indexes() -> None:
    manifest = load_doc_fixture(DOC_FIXTURE)
    schema = build_collection_schema(manifest)

    assert schema["auto_id"] is False
    assert schema["enable_dynamic_field"] is False
    assert schema["enable_namespace"] is False
    assert schema["consistency_level"] == "Strong"
    assert schema["description"] == collection_description(manifest)
    fields = {field["name"]: field for field in schema["fields"]}
    assert set(fields) == {
        "chunk_id",
        "logical_chunk_id",
        "root_id",
        "parent_id",
        "title",
        "content",
        "content_role",
        "tenant_id",
        "project_id",
        "allowed_group_ids",
        "classification_rank",
        "environment",
        "deleted",
        "index_family",
        "physical_collection",
        "corpus_version",
        "schema_version",
        "embedding_model_version",
        "source_id",
        "source_type",
        "revision_kind",
        "source_revision",
        "source_content_hash",
        "chunk_content_hash",
        "anchor_json",
        "derived_from_chunk_ids",
        "bm25_sparse",
        "dense_vector",
    }
    assert fields["content"]["params"]["analyzer_params"]["tokenizer"]["identifier"] == "whatlang"
    assert schema["functions"] == (
        {
            "name": "content_bm25_v1",
            "function_type": "BM25",
            "input_field_names": ("content",),
            "output_field_names": ("bm25_sparse",),
        },
    )
    assert schema["indexes"]["bm25_sparse"]["params"]["inverted_index_algo"] == "DAAT_MAXSCORE"


def test_rows_inject_exact_manifest_family_target_and_provenance() -> None:
    manifest = load_doc_fixture(DOC_FIXTURE)
    vectors = {chunk.chunk_id: (0.0,) * manifest.vector_dimension for chunk in manifest.chunks}
    rows = fixture_rows(manifest, vectors)

    assert len(rows) == 12
    assert {row["index_family"] for row in rows} == {"doc"}
    assert {row["source_type"] for row in rows} == {"doc"}
    assert {row["revision_kind"] for row in rows} == {"blob_version"}
    assert {row["physical_collection"] for row in rows} == {manifest.physical_collection}
    assert {row["embedding_model_version"] for row in rows} == {manifest.embedding_model_version}
    assert all(row["derived_from_chunk_ids"] == [] for row in rows)


def test_rows_reject_extra_vectors_mixed_dimensions_and_non_finite_values() -> None:
    manifest = load_doc_fixture(DOC_FIXTURE)
    vectors = {chunk.chunk_id: (0.0,) * manifest.vector_dimension for chunk in manifest.chunks}
    with pytest.raises(ValueError):
        fixture_rows(manifest, {**vectors, "h_" + "f" * 64: (0.0,) * 1536})
    with pytest.raises(ValueError):
        fixture_rows(manifest, {**vectors, manifest.chunks[0].chunk_id: (0.0,) * 2})
    with pytest.raises(ValueError):
        fixture_rows(manifest, {**vectors, manifest.chunks[0].chunk_id: (float("nan"),) * 1536})
    with pytest.raises(ValueError):
        fixture_rows(manifest, {**vectors, manifest.chunks[0].chunk_id: (0,) * 1536})


def test_description_and_descriptor_reconciliation_are_closed() -> None:
    manifest = load_doc_fixture(DOC_FIXTURE)
    descriptor = {
        "collection_name": manifest.physical_collection,
        "family": "doc",
        "schema_version": manifest.schema_version,
        "schema_sha256": manifest.schema_sha256,
        "corpus_version": manifest.corpus_version,
        "embedding_model_version": manifest.embedding_model_version,
        "vector_dimension": manifest.vector_dimension,
        "dynamic_fields_enabled": False,
        "consistency_level": "Strong",
    }
    validate_collection_descriptor(manifest, descriptor)

    for change in (
        {"schema_sha256": "sha256:" + "a" * 64},
        {"dynamic_fields_enabled": True},
        {"family": "code"},
        {"index_family": "doc"},
    ):
        with pytest.raises(ValueError):
            validate_collection_descriptor(manifest, {**descriptor, **change})

    metadata = json.loads(collection_description(manifest).split(":", 1)[1])
    metadata["extra"] = True
    with pytest.raises(ValueError):
        validate_collection_descriptor(
            manifest,
            {**descriptor, "description": "tap-collection-metadata-v1:" + json.dumps(metadata)},
        )


def test_manifest_rejects_runtime_model_or_physical_mutation() -> None:
    manifest = load_doc_fixture(DOC_FIXTURE)
    with pytest.raises(ValueError):
        build_collection_schema(replace(manifest, vector_dimension=2))
    with pytest.raises(ValueError):
        build_collection_schema(replace(manifest, physical_collection="kb_code_v1_fixture"))


def test_fixture_digest_rejects_rehashed_content_or_acl_substitution(tmp_path: Path) -> None:
    raw = json.loads(DOC_FIXTURE.read_text())
    chunk = raw["chunks"][0]
    chunk["content"] = "重新计算散列后仍不属于固定脱敏语料。"
    chunk["sourceContentHash"] = content_hash(chunk["content"])
    chunk["chunkContentHash"] = content_hash(chunk["content"])
    chunk["chunkId"] = sha256_id(
        f"{chunk['sourceId']}\0{chunk['sourceRevision']}\0{chunk['content']}"
    )
    chunk["rootId"] = chunk["chunkId"]
    chunk["allowedGroupIds"] = ["group-support"]
    candidate = tmp_path / "rehashed.json"
    candidate.write_text(json.dumps(raw, ensure_ascii=False))

    with pytest.raises(ValueError, match="trusted corpus"):
        load_doc_fixture(candidate)


def test_query_digest_rejects_changed_policy_expectations(tmp_path: Path) -> None:
    raw = json.loads(QUERY_FIXTURE.read_text())
    raw["cases"][0]["expectedSourceIds"] = ["blob:fixture/public/support"]
    raw["cases"][0]["groupIds"] = ["group-support"]
    candidate = tmp_path / "changed-queries.json"
    candidate.write_text(json.dumps(raw))

    with pytest.raises(ValueError, match="trusted policy cases"):
        load_query_cases(candidate)
