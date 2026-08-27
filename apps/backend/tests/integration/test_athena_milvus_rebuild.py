"""Opt-in real Milvus Athena full-rebuild parity and rollback gates."""

from __future__ import annotations

import os

import pytest

if os.getenv("TAP_RUN_MILVUS_INTEGRATION") != "1":
    pytest.skip("real Milvus suite requires TAP_RUN_MILVUS_INTEGRATION=1", allow_module_level=True)

from test_athena_milvus_projection import (  # noqa: E402, F401
    _real_index,
    chunk,
    vector,
    work,
)

from tap.modules.knowledge.adapters.milvus_documents import (  # noqa: E402
    ATHENA_ALIAS,
    ATHENA_PHYSICAL_COLLECTION,
    ReadyRevisionArtifacts,
)
from tap.modules.knowledge.ports.documents import EmbeddingArtifact  # noqa: E402


@pytest.mark.asyncio
async def test_real_milvus_rebuild_has_exact_parity_and_atomic_alias_switch(
    real_index,
) -> None:  # type: ignore[no-untyped-def]
    index, provisioner = real_index
    await index.ensure_target()
    record = ReadyRevisionArtifacts(
        work=work(),
        chunks=(chunk(), chunk(2)),
        embeddings=EmbeddingArtifact(
            "athena-embedding",
            1536,
            (vector(0.1), vector(0.3)),
            (str(chunk().chunk_id), str(chunk(2).chunk_id)),
        ),
        index_version="athena-v1",
    )

    receipt = await index.rebuild((record,))

    assert receipt.row_count == 2
    assert receipt.physical_collection.startswith(ATHENA_PHYSICAL_COLLECTION + "_")
    assert await provisioner.describe_alias(ATHENA_ALIAS) == receipt.physical_collection
    assert await provisioner.collection_exists(ATHENA_PHYSICAL_COLLECTION)
    assert await provisioner.collection_aliases(ATHENA_PHYSICAL_COLLECTION) == ()
    reader_grants = await provisioner.collection_grants(ATHENA_PHYSICAL_COLLECTION, "tap_reader")
    writer_grants = await provisioner.collection_grants(ATHENA_PHYSICAL_COLLECTION, "tap_writer")
    assert reader_grants == frozenset()
    assert writer_grants == frozenset()


@pytest.mark.asyncio
async def test_real_milvus_failed_rebuild_retains_old_alias_and_cleanup_facts(
    real_index, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    index, provisioner = real_index
    await index.ensure_target()

    async def fail_verification(*args: object) -> None:
        raise RuntimeError("injected reconciliation failure")

    monkeypatch.setattr(index, "_require_revision_parity", fail_verification)
    record = ReadyRevisionArtifacts(
        work=work(),
        chunks=(chunk(),),
        embeddings=EmbeddingArtifact(
            "athena-embedding", 1536, (vector(0.1),), (str(chunk().chunk_id),)
        ),
        index_version="athena-v1",
    )

    with pytest.raises(Exception) as caught:
        await index.rebuild((record,))

    assert await provisioner.describe_alias(ATHENA_ALIAS) == ATHENA_PHYSICAL_COLLECTION
    assert getattr(caught.value, "cleanup_facts", ())
