"""Real MinIO evidence requires a validated owned container receipt before client construction."""

import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from apps.backend.tests.contract.artifact_store_conformance import exercise_artifact_round_trip
from apps.backend.tests.contract.object_store_conformance import (
    exercise_object_round_trip,
    exercise_staging_visibility,
    parts,
)
from pydantic import SecretStr
from scripts.azurite_test_support import require_owned_azurite
from scripts.minio_test_support import require_owned_minio

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.knowledge.adapters.object_artifacts import KnowledgeArtifactStore
from tap.platform.storage.objects import ObjectIntegrityError, PutObjectRequest
from tap.platform.storage.s3 import S3ObjectConfig, S3ObjectStore


@pytest.fixture
def owned():
    if os.getenv("TAP_RUN_MINIO_INTEGRATION") != "1":
        pytest.skip("requires owned MinIO integration wrapper")
    return require_owned_minio()


def config(owned):
    return S3ObjectConfig(
        endpoint=owned.endpoint,
        bucket=owned.bucket,
        region="us-east-1",
        access_key=SecretStr(owned.access_key_id),
        secret_key=SecretStr(owned.secret_access_key),
        store_id="owned-integration",
    )


@pytest_asyncio.fixture
async def store(owned):
    value = S3ObjectStore(config(owned), scope=VALIDATION_SCOPE)
    try:
        await value.ensure_bucket()
        yield value
    finally:
        await value.aclose()


@pytest.mark.asyncio
async def test_owned_minio_round_trip(store):
    await exercise_object_round_trip(store)
    assert await store.is_private()


@pytest.mark.asyncio
async def test_owned_minio_shared_knowledge_contract(store):
    await exercise_artifact_round_trip(KnowledgeArtifactStore(store))


@pytest.mark.asyncio
async def test_owned_minio_staging_scavenger_and_foreign_namespace(store, owned):
    foreign = S3ObjectStore(config(owned), scope=replace(VALIDATION_SCOPE, project_id="foreign"))
    try:
        staged = await foreign.put_staged(PutObjectRequest(parts(), 1024, "text/plain"))
        await exercise_staging_visibility(store)
        receipt = await store.scavenge_staging(
            now=datetime.now(timezone.utc) + timedelta(hours=2), visible_refs=frozenset(), limit=100
        )
        assert staged.ref not in receipt.removed
        assert (await foreign.open_verified(staged.ref)).data == b"verified original"
        await foreign.delete(staged.ref)
    finally:
        await foreign.aclose()


@pytest.mark.asyncio
async def test_owned_minio_restart_retains_manifest_digest_and_recovers_staging(owned):
    from uuid import uuid4

    first = S3ObjectStore(config(owned), scope=VALIDATION_SCOPE)
    await first.ensure_bucket()
    staged = await first.put_staged(PutObjectRequest(parts(), 1024, "text/plain"))
    identity = uuid4().hex + "/original"
    ref = await first.promote(
        staged, staged.sha256, identity=identity, attributes={"kind": "original"}
    )
    await first.aclose()
    owned.restart()
    # Revalidate actual container, image, credentials and port after the restart before I/O.
    second = S3ObjectStore(config(require_owned_minio(owned.receipt_path)), scope=VALIDATION_SCOPE)
    try:
        assert (await second.open_verified(ref)).data == b"verified original"
        assert (
            await second.promote(
                staged, staged.sha256, identity=identity, attributes={"kind": "original"}
            )
            == ref
        )
        await second.delete(staged.ref)
        await second.delete(ref)
    finally:
        await second.aclose()


@pytest.mark.asyncio
async def test_owned_minio_actual_payload_tamper_rejected(store):
    from uuid import uuid4

    staged = await store.put_staged(PutObjectRequest(parts(), 1024, "text/plain"))
    ref = await store.promote(
        staged, staged.sha256, identity=uuid4().hex + "/original", attributes={}
    )
    manifest = await store._manifest(ref)
    client = await store._get_client()
    await client.put_object(
        Bucket=store._config.bucket, Key=store._payload_key(manifest), Body=b"tamper"
    )
    with pytest.raises(ObjectIntegrityError):
        await store.open_verified(ref)
    await store.delete(ref)
    await store.delete(staged.ref)


@pytest.mark.asyncio
async def test_owned_mixed_azure_recovery_and_minio_artifacts_preserve_legacy_refs(store):
    from apps.backend.tests.contract.artifact_store_conformance import (
        DOCUMENT_ID,
        REVISION,
        Upload,
        normalized_artifact,
    )

    from tap.modules.knowledge.adapters.blob_artifacts import (
        AzureBlobArtifactConfig,
        AzureBlobArtifactStore,
    )
    from tap.modules.knowledge.ports.documents import DeletionTarget
    from tap.modules.knowledge.ports.errors import ArtifactIntegrityFailure

    if os.getenv("TAP_RUN_AZURITE_INTEGRATION") != "1":
        pytest.fail("mixed conformance requires the owned Azure wrapper")
    azure = require_owned_azurite()
    legacy = AzureBlobArtifactStore(
        AzureBlobArtifactConfig(connection_string=SecretStr(azure.connection_string)),
        scope=VALIDATION_SCOPE,
    )
    try:
        await legacy.ensure_containers()
        staged = await legacy.stage_original(Upload(), max_bytes=1024)
        combined = KnowledgeArtifactStore(store, legacy=legacy)
        old_ref = await combined.recover_original(staged.staging_key, REVISION)
        assert old_ref.startswith("tapper-originals/")
        assert await combined.read_original(old_ref) == b"Tapper policy."
        new_ref = await combined.write_normalized(REVISION, normalized_artifact())
        assert new_ref.startswith("art1.")
        await combined.delete_revision_artifacts(
            DeletionTarget(str(DOCUMENT_ID), REVISION, (), (old_ref, new_ref))
        )
        with pytest.raises(ArtifactIntegrityFailure):
            await combined.read_original(old_ref)
        with pytest.raises(ArtifactIntegrityFailure):
            await combined.read_normalized(new_ref)
    finally:
        await legacy.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("caller_cancel", [False, True])
async def test_native_s3_partial_body_cancel_closes_socket_and_settles(store, owned, caller_cancel):
    """Real pinned SDK and MinIO; owned TCP proxy stalls only the response body."""
    import asyncio
    from urllib.parse import urlsplit

    from tap.platform.storage.objects import ObjectUnavailable

    staged = await store.put_staged(PutObjectRequest(parts(), 1024, "text/plain"))
    upstream = urlsplit(require_owned_minio(owned.receipt_path).endpoint)
    started, disconnected = asyncio.Event(), asyncio.Event()
    handlers = set()

    async def stall(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        upstream_writer = None
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            upstream_reader, upstream_writer = await asyncio.open_connection(
                upstream.hostname, upstream.port
            )
            upstream_writer.write(request)
            await upstream_writer.drain()
            headers = await upstream_reader.readuntil(b"\r\n\r\n")
            assert headers.startswith(b"HTTP/1.1 200")
            writer.write(headers + await upstream_reader.readexactly(1))
            await writer.drain()
            started.set()
            assert await reader.read() == b""
            disconnected.set()
        finally:
            if upstream_writer is not None:
                upstream_writer.close()
                await upstream_writer.wait_closed()
            writer.close()
            await writer.wait_closed()
            handlers.discard(task)

    server = await asyncio.start_server(stall, "127.0.0.1", 0)
    endpoint = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    target = S3ObjectStore(
        replace(config(owned), endpoint=endpoint, timeout_seconds=0.3), scope=VALIDATION_SCOPE
    )
    try:
        client = await target._get_client()
        operation = asyncio.create_task(target.open_verified(staged.ref))
        await asyncio.wait_for(started.wait(), 3)
        if caller_cancel:
            operation.cancel()
            await asyncio.sleep(0)
            operation.cancel()
        with pytest.raises(asyncio.CancelledError if caller_cancel else ObjectUnavailable):
            await operation
        await asyncio.wait_for(disconnected.wait(), 2)
        assert not target._pending
        # Read-only observation of the pinned native SDK's actual connector pools.
        assert all(
            not session.connector._acquired
            for session in client._endpoint.http_session._sessions.values()
        )
    finally:
        await target.aclose()
        server.close()
        await server.wait_closed()
        for task in tuple(handlers):
            task.cancel()
        await asyncio.gather(*tuple(handlers), return_exceptions=True)
        await store.delete(staged.ref)
