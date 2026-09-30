import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.adapters.object_artifacts import KnowledgeArtifactStore
from tap.modules.knowledge.domain.documents import canonical_sha256
from tap.modules.knowledge.ports.documents import ArtifactLocator
from tap.modules.knowledge.ports.errors import ArtifactUnavailable
from tap.platform.storage.s3 import S3ObjectStore
from tests.contract.artifact_store_conformance import exercise_artifact_round_trip
from tests.contract.test_s3_object_store import MemoryS3, config


@pytest.mark.asyncio
async def test_composed_artifacts_share_canonical_contract():
    store = KnowledgeArtifactStore(
        S3ObjectStore(config(), scope=VALIDATION_SCOPE, client=MemoryS3())
    )
    await exercise_artifact_round_trip(store)


@pytest.mark.asyncio
async def test_large_original_excerpt_uses_one_late_bounded_s3_range():
    prefix = b"A" * 1_100_000
    excerpt = "后部条款🙂".encode()
    payload = prefix + excerpt + b"suffix"

    class Upload:
        filename = "large.txt"
        media_type = "text/plain"

        @property
        def content(self):  # type: ignore[no-untyped-def]
            async def stream():  # type: ignore[no-untyped-def]
                yield payload

            return stream()

    client = MemoryS3()
    store = KnowledgeArtifactStore(S3ObjectStore(config(), scope=VALIDATION_SCOPE, client=client))
    staged = await store.stage_original(Upload(), max_bytes=len(payload))
    locator = await store.commit_original(staged, "revision-large")

    value = await store.read_original_excerpt(
        locator,
        revision_id="revision-large",
        source_digest=canonical_sha256(payload),
        start_byte=len(prefix),
        end_byte=len(prefix) + len(excerpt),
        excerpt_digest=canonical_sha256(excerpt),
    )

    assert value == excerpt
    assert client.ranges == [f"bytes={len(prefix)}-{len(prefix) + len(excerpt) - 1}"]


@pytest.mark.asyncio
async def test_legacy_reference_never_silently_becomes_s3_key():
    client = MemoryS3()
    store = KnowledgeArtifactStore(S3ObjectStore(config(), scope=VALIDATION_SCOPE, client=client))
    old = ArtifactLocator("tapper-originals/revisions/revision-1/original/source.txt")
    with pytest.raises(ArtifactUnavailable):
        await store.read_original(old)
    assert client.calls == []


@pytest.mark.asyncio
async def test_legacy_locator_is_unavailable():
    from tap.modules.knowledge.ports.documents import DeletionTarget

    client = MemoryS3()
    store = KnowledgeArtifactStore(S3ObjectStore(config(), scope=VALIDATION_SCOPE, client=client))
    old = ArtifactLocator("documents/x/y")
    for read in (store.read_original, store.read_normalized, store.read_chunks):
        with pytest.raises(ArtifactUnavailable):
            await read(old)
    with pytest.raises(ArtifactUnavailable):
        await store.read_embeddings(old)
    with pytest.raises(ArtifactUnavailable):
        await store.read_original_excerpt(
            old,
            revision_id="revision-1",
            source_digest="0" * 64,
            start_byte=0,
            end_byte=1,
            excerpt_digest="0" * 64,
        )
    with pytest.raises(ArtifactUnavailable):
        await store.delete_revision_artifacts(DeletionTarget("doc", "revision-1", (), (old,)))
    assert client.calls == []


@pytest.mark.asyncio
async def test_legacy_staging_key_is_unavailable():
    client = MemoryS3()
    store = KnowledgeArtifactStore(S3ObjectStore(config(), scope=VALIDATION_SCOPE, client=client))
    with pytest.raises(ArtifactUnavailable):
        await store.recover_original("legacy-key", "revision-1")
    with pytest.raises(ArtifactUnavailable):
        await store.discard_staging("staging/legacy-key")
    assert client.calls == []


def test_store_composes_only_the_object_store():
    client = MemoryS3()
    with pytest.raises(TypeError):
        KnowledgeArtifactStore(  # type: ignore[call-arg]
            S3ObjectStore(config(), scope=VALIDATION_SCOPE, client=client), legacy=object()
        )
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_payload", [False, True])
async def test_forged_wrapper_cannot_delete_other_revision_or_part_of_batch(missing_payload):
    from tap.modules.knowledge.adapters.object_artifacts import _locator, _parse
    from tap.modules.knowledge.ports.documents import DeletionTarget
    from tap.modules.knowledge.ports.errors import ArtifactIntegrityFailure
    from tests.contract.artifact_store_conformance import Upload

    client = MemoryS3()
    store = KnowledgeArtifactStore(S3ObjectStore(config(), scope=VALIDATION_SCOPE, client=client))
    staged = await store.stage_original(Upload(), max_bytes=1024)
    first = await store.commit_original(staged, "revision-1")
    foreign = await store.commit_original(staged, "revision-2")
    foreign_ref = _parse(foreign)[2]
    manifest = await store.objects._manifest(foreign_ref)
    if missing_payload:
        client.objects.pop(store.objects._payload_key(manifest))
    forged = _locator("revision-1", "original", foreign_ref)
    calls = len(client.calls)
    with pytest.raises(ArtifactIntegrityFailure):
        await store.delete_revision_artifacts(
            DeletionTarget("doc", "revision-1", (), (first, forged))
        )
    assert all(op != "delete" for op, _ in client.calls[calls:])
    assert await store.read_original(first) == b"Tapper policy."
    assert store.objects._manifest_key(store.objects._parse(foreign_ref)) in client.objects
    if not missing_payload:
        assert await store.read_original(first) == await store.read_original(foreign)


@pytest.mark.asyncio
async def test_delete_with_missing_manifest_leaves_unreferenced_payload_untouched():
    from tap.modules.knowledge.adapters.object_artifacts import _parse
    from tap.modules.knowledge.ports.documents import DeletionTarget
    from tests.contract.artifact_store_conformance import Upload

    client = MemoryS3()
    store = KnowledgeArtifactStore(S3ObjectStore(config(), scope=VALIDATION_SCOPE, client=client))
    staged = await store.stage_original(Upload(), max_bytes=1024)
    locator = await store.commit_original(staged, "revision-1")
    ref = _parse(locator)[2]
    manifest = await store.objects._manifest(ref)
    client.objects.pop(store.objects._manifest_key(store.objects._parse(ref)))
    before = dict(client.objects)
    await store.delete_revision_artifacts(DeletionTarget("doc", "revision-1", (), (locator,)))
    assert client.objects == before
    assert store.objects._payload_key(manifest) in client.objects
