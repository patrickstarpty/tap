"""Source identity and authority cannot be silently reassigned."""

import pytest


def test_source_legacy_mapping_is_scoped_and_reproducible():
    from tap.modules.knowledge.domain.sources import legacy_source_id

    assert legacy_source_id("project-a", "document-a") == legacy_source_id(
        "project-a", "document-a"
    )
    assert legacy_source_id("project-a", "document-a") != legacy_source_id(
        "project-b", "document-a"
    )
    assert len(legacy_source_id("project-a", "document-a")) == 36


def test_source_tables_keep_multi_selection_ownership():
    from tap.platform.db.registry import load_authoritative_metadata

    tables = load_authoritative_metadata().tables
    assert "knowledge_source" in tables
    assert "source_id" not in tables["knowledge_answer_snapshot"].c
    assert not tables["knowledge_document"].c.source_id.nullable
    assert not tables["knowledge_document_revision"].c.source_id.nullable
    assert not tables["knowledge_citation_snapshot"].c.source_id.nullable
    association = tables["knowledge_answer_source"]
    assert {"trace_id", "source_id", "document_id", "revision_id", "ordinal"} <= set(
        association.c.keys()
    )
    assert any(
        tuple(c.name for c in fk.columns) == ("project_id", "document_id", "source_id")
        for fk in tables["knowledge_document_revision"].foreign_key_constraints
    )


def test_source_unknown_or_empty_legacy_identity_rejected():
    from tap.modules.knowledge.domain.sources import legacy_source_id

    with pytest.raises(ValueError):
        legacy_source_id("project-a", "")


def test_source_projection_receipt_digest_ignores_physical_generation():
    from tap.modules.knowledge.domain.sources import projection_digest
    from tap.modules.knowledge.ports.documents import ManifestChunk

    chunk = ManifestChunk(
        "chunk",
        "logical",
        0,
        "doc",
        None,
        '{"page":1}',
        "sha256:" + "a" * 64,
        "model-v1",
        "index-v1",
    )
    value = projection_digest("revision", "schema-v1", "index-v1", (chunk,))
    from dataclasses import replace

    assert value.startswith("sha256:") and len(value) == 71
    assert value != projection_digest(
        "revision",
        "schema-v1",
        "index-v1",
        (replace(chunk, chunk_content_hash="sha256:" + "b" * 64),),
    )
