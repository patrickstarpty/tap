import pytest
from apps.backend.tests.contract.artifact_store_conformance import exercise_artifact_round_trip
from apps.backend.tests.contract.test_s3_object_store import MemoryS3, config

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.adapters.object_artifacts import KnowledgeArtifactStore
from tap.modules.knowledge.ports.documents import ArtifactLocator
from tap.modules.knowledge.ports.errors import ArtifactUnavailable
from tap.platform.storage.s3 import S3ObjectStore


@pytest.mark.asyncio
async def test_composed_artifacts_share_canonical_contract():
    store = KnowledgeArtifactStore(
        S3ObjectStore(config(), scope=VALIDATION_SCOPE, client=MemoryS3())
    )
    await exercise_artifact_round_trip(store)


@pytest.mark.asyncio
async def test_legacy_reference_never_silently_becomes_s3_key():
    client = MemoryS3()
    store = KnowledgeArtifactStore(S3ObjectStore(config(), scope=VALIDATION_SCOPE, client=client))
    old = ArtifactLocator("tapper-originals/revisions/revision-1/original/source.txt")
    with pytest.raises(ArtifactUnavailable):
        await store.read_original(old)
    assert client.calls == []


@pytest.mark.asyncio
async def test_explicit_legacy_keeps_persisted_reference_bytes():
    received = []

    class Legacy:
        scope = VALIDATION_SCOPE

        async def read_original(self, ref):
            received.append(ref)
            return b"legacy"

    client = MemoryS3()
    store = KnowledgeArtifactStore(
        S3ObjectStore(config(), scope=VALIDATION_SCOPE, client=client), legacy=Legacy()
    )
    ref = ArtifactLocator("tapper-originals/revisions/revision-1/original/source.txt")
    assert await store.read_original(ref) == b"legacy"
    assert received == [ref]
    assert client.calls == []


def test_legacy_composition_rejects_different_project_before_provider_io():
    from dataclasses import replace
    from types import SimpleNamespace

    client = MemoryS3()
    with pytest.raises(ValueError, match="scope"):
        KnowledgeArtifactStore(
            S3ObjectStore(config(), scope=VALIDATION_SCOPE, client=client),
            legacy=SimpleNamespace(scope=replace(VALIDATION_SCOPE, project_id="foreign")),
        )
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_payload", [False, True])
async def test_forged_wrapper_cannot_delete_other_revision_or_part_of_batch(missing_payload):
    from apps.backend.tests.contract.artifact_store_conformance import Upload

    from tap.modules.knowledge.adapters.object_artifacts import _locator, _parse
    from tap.modules.knowledge.ports.documents import DeletionTarget
    from tap.modules.knowledge.ports.errors import ArtifactIntegrityFailure

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
    from apps.backend.tests.contract.artifact_store_conformance import Upload

    from tap.modules.knowledge.adapters.object_artifacts import _parse
    from tap.modules.knowledge.ports.documents import DeletionTarget

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
