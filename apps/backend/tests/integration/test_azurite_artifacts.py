"""Opt-in real Azurite artifact round-trip and integrity gates."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from pydantic import SecretStr

if os.getenv("TAP_RUN_AZURITE_INTEGRATION") != "1":
    pytest.skip(
        "real Azurite suite requires TAP_RUN_AZURITE_INTEGRATION=1", allow_module_level=True
    )

from azure.core.exceptions import ResourceNotFoundError  # noqa: E402
from azure.storage.blob.aio import BlobServiceClient  # noqa: E402

from tap.modules.knowledge.adapters.blob_artifacts import (  # noqa: E402
    ARTIFACTS_CONTAINER,
    ORIGINALS_CONTAINER,
    ArtifactIntegrityError,
    AzureBlobArtifactConfig,
    AzureBlobArtifactStore,
)
from tap.modules.knowledge.domain.documents import (  # noqa: E402
    BlockKind,
    ChunkDraft,
    DocumentId,
    MediaType,
    NormalizedArtifact,
    NormalizedBlock,
    canonical_sha256,
)
from tap.modules.knowledge.ports.documents import EmbeddingArtifact  # noqa: E402

REVISION = "rev_task5_contract"
SOURCE_HASH = "sha256:" + "a" * 64

AZURITE_CONNECTION = os.getenv(
    "AZURITE_CONNECTION_STRING",
    "UseDevelopmentStorage=true",
)


def normalized_artifact() -> NormalizedArtifact:
    return NormalizedArtifact(
        filename="policy.md",
        media_type=MediaType.MARKDOWN,
        source_hash=SOURCE_HASH,
        document_id=DocumentId("doc_a"),
        revision_id=REVISION,  # type: ignore[arg-type]
        blocks=(
            NormalizedBlock(
                block_id="block-1",
                kind=BlockKind.PARAGRAPH,
                text="Athena policy.",
                heading_path=("Policy",),
                page=None,
                paragraph_index=0,
                start_offset=0,
                end_offset=14,
            ),
        ),
    )


def chunk_artifact() -> tuple[ChunkDraft, ...]:
    content = "Athena policy."
    return (
        ChunkDraft(
            chunk_id="h_" + "1" * 64,  # type: ignore[arg-type]
            logical_chunk_id="lc_" + "2" * 64,  # type: ignore[arg-type]
            root_id=DocumentId("doc_a"),
            parent_id=None,
            content=content,
            anchor_json='{"blockId":"block-1"}',
            source_content_hash=SOURCE_HASH,
            chunk_content_hash=canonical_sha256(content.encode()),
        ),
    )


class Upload:
    filename = "policy.md"
    media_type = "text/markdown"

    @property
    def content(self):  # type: ignore[no-untyped-def]
        async def parts():  # type: ignore[no-untyped-def]
            yield b"Athena policy."

        return parts()


@pytest_asyncio.fixture
async def store() -> AzureBlobArtifactStore:
    await cleanup_revision()
    value = AzureBlobArtifactStore(
        AzureBlobArtifactConfig(connection_string=SecretStr(AZURITE_CONNECTION))
    )
    await value.ensure_containers()
    yield value
    await value.close()
    await cleanup_revision()


async def service_client() -> BlobServiceClient:
    return BlobServiceClient.from_connection_string(
        AZURITE_CONNECTION,
        api_version="2023-11-03",
    )


async def cleanup_revision() -> None:
    service = await service_client()
    try:
        for container_name in (ORIGINALS_CONTAINER, ARTIFACTS_CONTAINER):
            container = service.get_container_client(container_name)
            try:
                async for item in container.list_blobs(name_starts_with=f"revisions/{REVISION}/"):
                    await container.delete_blob(item.name, delete_snapshots="include")
            except ResourceNotFoundError:
                pass
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_real_azurite_round_trips_all_artifact_kinds_and_keeps_containers_private(
    store: AzureBlobArtifactStore,
) -> None:
    staged = await store.stage_original(Upload(), max_bytes=1024)
    original = await store.commit_original(staged, REVISION)
    normalized = await store.write_normalized(REVISION, normalized_artifact())
    chunks = await store.write_chunks(REVISION, chunk_artifact())
    embeddings = await store.write_embeddings(
        REVISION,
        EmbeddingArtifact("athena-embedding", 3, ((0.1, 0.2, 0.3),)),
        source_content_hash=SOURCE_HASH,
    )

    assert await store.read_original(original) == b"Athena policy."
    assert await store.read_normalized(normalized) == normalized_artifact()
    assert await store.read_chunks(chunks) == chunk_artifact()
    assert await store.read_embeddings(embeddings) == EmbeddingArtifact(
        "athena-embedding", 3, ((0.1, 0.2, 0.3),)
    )
    for container in (ORIGINALS_CONTAINER, ARTIFACTS_CONTAINER):
        properties = await store.container_properties(container)
        assert properties.get("public_access") is None
    service = await service_client()
    try:
        with pytest.raises(ResourceNotFoundError):
            await service.get_blob_client(
                ORIGINALS_CONTAINER, staged.staging_key
            ).get_blob_properties()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_real_azurite_blob_hash_tampering_is_rejected(store: AzureBlobArtifactStore) -> None:
    locator = await store.write_chunks(REVISION, chunk_artifact())
    service = await service_client()
    try:
        container, blob_name = str(locator).split("/", 1)
        await service.get_blob_client(container, blob_name).upload_blob(
            b"not-the-committed-gzip",
            overwrite=True,
        )
    finally:
        await service.close()

    with pytest.raises(ArtifactIntegrityError):
        await store.read_chunks(locator)


@pytest.mark.asyncio
async def test_real_azurite_copy_failure_retains_staging_and_exposes_no_final(
    store: AzureBlobArtifactStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staged = await store.stage_original(Upload(), max_bytes=1024)
    monkeypatch.setattr(store, "_source_copy_url", lambda _source: "http://127.0.0.1:1/missing")

    with pytest.raises(ArtifactIntegrityError):
        await store.commit_original(staged, REVISION)

    service = await service_client()
    try:
        assert await service.get_blob_client(
            ORIGINALS_CONTAINER, staged.staging_key
        ).get_blob_properties()
        assert not await service.get_blob_client(
            ORIGINALS_CONTAINER,
            f"revisions/{REVISION}/{staged.source_content_hash.removeprefix('sha256:')}",
        ).exists()
        await service.get_blob_client(ORIGINALS_CONTAINER, staged.staging_key).delete_blob()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_real_azurite_scavenger_is_bounded_and_respects_visibility_windows(
    store: AzureBlobArtifactStore,
) -> None:
    now = datetime.now(timezone.utc)
    invisible = "staging/task5-invisible-orphan"
    visible_recent = "staging/task5-visible-recent"
    visible_expired = "staging/task5-visible-expired"
    service = await service_client()
    try:
        container = service.get_container_client(ORIGINALS_CONTAINER)
        for name, age in (
            (invisible, timedelta(hours=2)),
            (visible_recent, timedelta(hours=2)),
            (visible_expired, timedelta(hours=25)),
        ):
            await container.get_blob_client(name).upload_blob(
                b"x",
                overwrite=True,
                metadata={
                    "blobsha256": (
                        "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"
                    ),
                    "size": "1",
                    "stagedat": (now - age).isoformat(),
                },
            )

        receipt = await store.scavenge_staging(
            now=now,
            visible_staging_keys=frozenset({visible_recent, visible_expired}),
            limit=3,
        )

        assert receipt.scanned == 3
        assert set(receipt.removed) == {invisible, visible_expired}
        assert await container.get_blob_client(visible_recent).exists()
    finally:
        for name in (invisible, visible_recent, visible_expired):
            try:
                await service.get_blob_client(ORIGINALS_CONTAINER, name).delete_blob()
            except ResourceNotFoundError:
                pass
        await service.close()
