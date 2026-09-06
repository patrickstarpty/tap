"""S3 contract uses a deterministic wire double; integration tests use owned MinIO."""

import asyncio
import hashlib
import io
import traceback
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from apps.backend.tests.contract.object_store_conformance import (
    exercise_object_round_trip,
    exercise_staging_visibility,
    parts,
)
from botocore.exceptions import ClientError
from pydantic import SecretStr

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.platform.storage.objects import (
    ObjectIntegrityError,
    ObjectRef,
    ObjectUnavailable,
    PutObjectRequest,
)
from tap.platform.storage.s3 import S3ObjectConfig, S3ObjectStore


class Body:
    def __init__(self, data):
        self.stream = io.BytesIO(data)
        self.closed = False

    async def read(self, size=-1):
        return self.stream.read(size)

    def close(self):
        self.closed = True


class MemoryS3:
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.bodies = []
        self.fail = None
        self.closed = False

    async def close(self):
        self.closed = True

    def observe(self, operation, kwargs):
        self.calls.append((operation, kwargs.get("Key")))
        if self.fail:
            raise self.fail

    async def put_object(self, **kwargs):
        self.observe("put", kwargs)
        key = kwargs["Key"]
        if key in self.objects and kwargs.get("IfNoneMatch") == "*":
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        content = kwargs["Body"]
        data = content.read() if hasattr(content, "read") else content
        self.objects[key] = {
            "data": data,
            "Metadata": kwargs.get("Metadata", {}),
            "ContentType": kwargs.get("ContentType", "application/octet-stream"),
            "ContentLength": len(data),
            "ETag": '"' + hashlib.md5(data).hexdigest() + '"',
            "LastModified": datetime.now(timezone.utc),
        }
        return {"ETag": self.objects[key]["ETag"]}

    async def get_object(self, **kwargs):
        self.observe("get", kwargs)
        if kwargs["Key"] not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        item = self.objects[kwargs["Key"]]
        body = Body(item["data"])
        self.bodies.append(body)
        return {**item, "Body": body}

    async def delete_object(self, **kwargs):
        self.observe("delete", kwargs)
        item = self.objects.get(kwargs["Key"])
        if item and kwargs.get("IfMatch") not in (None, item["ETag"]):
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "DeleteObject")
        self.objects.pop(kwargs["Key"], None)
        return {}

    async def list_objects_v2(self, **kwargs):
        self.observe("list", kwargs)
        return {
            "Contents": [
                {"Key": key, "LastModified": item["LastModified"], "ETag": item["ETag"]}
                for key, item in self.objects.items()
                if key.startswith(kwargs["Prefix"])
            ][: kwargs["MaxKeys"]],
            "IsTruncated": False,
        }

    async def head_bucket(self, **kwargs):
        return {}

    async def create_bucket(self, **kwargs):
        return {}

    async def get_bucket_policy(self, **kwargs):
        raise ClientError({"Error": {"Code": "NoSuchBucketPolicy"}}, "GetBucketPolicy")


def config(**kwargs):
    return S3ObjectConfig(
        endpoint="http://127.0.0.1:29000",
        bucket="owned-bucket",
        region="us-east-1",
        access_key=SecretStr("owned-key"),
        secret_key=SecretStr("owned-secret"),
        store_id="owned-store",
        **kwargs,
    )


@pytest.fixture
def store():
    return S3ObjectStore(config(), scope=VALIDATION_SCOPE, client=MemoryS3())


@pytest.mark.asyncio
async def test_shared_round_trip(store):
    await exercise_object_round_trip(store)
    assert all(body.closed for body in store._client.bodies)


@pytest.mark.asyncio
async def test_scavenger_retains_pins_and_foreign_project(store):
    foreign = S3ObjectStore(
        config(), scope=replace(VALIDATION_SCOPE, project_id="foreign"), client=store._client
    )
    other = await foreign.put_staged(PutObjectRequest(parts(), 100, "text/plain"))
    await exercise_staging_visibility(store)
    assert (await foreign.open_verified(other.ref)).data == b"verified original"
    with pytest.raises(ValueError):
        await store.open_verified(other.ref)


@pytest.mark.asyncio
async def test_hash_mismatch_oversize_and_path_traversal_reject_before_publication(store):
    staged = await store.put_staged(PutObjectRequest(parts(), 100, "text/plain"))
    with pytest.raises(ObjectIntegrityError):
        await store.promote(staged, "sha256:" + "0" * 64, identity="r/original", attributes={})
    with pytest.raises(ObjectIntegrityError):
        await store.put_staged(PutObjectRequest(parts(), 2, "text/plain"))
    count = len(store._client.calls)
    with pytest.raises(ValueError):
        await store.promote(staged, staged.sha256, identity="../escape", attributes={})
    with pytest.raises(ValueError):
        await store.open_verified(ObjectRef("https://secret@provider/bucket/key"))
    assert len(store._client.calls) == count
    assert all("staging/" in key for key in store._client.objects)


@pytest.mark.asyncio
async def test_digest_tamper_closes_body_and_never_returns_unverified_bytes(store):
    staged = await store.put_staged(PutObjectRequest(parts(), 100, "text/plain"))
    ref = await store.promote(staged, staged.sha256, identity="r/original", attributes={})
    payload_key = next(key for key in store._client.objects if "/payloads/" in key)
    store._client.objects[payload_key]["data"] = b"changed"
    with pytest.raises(ObjectIntegrityError):
        await store.open_verified(ref)
    assert all(body.closed for body in store._client.bodies)


@pytest.mark.asyncio
async def test_delete_cannot_remove_other_revision_equal_content(store):
    staged = await store.put_staged(PutObjectRequest(parts(), 100, "text/plain"))
    first = await store.promote(staged, staged.sha256, identity="r1/original", attributes={})
    second = await store.promote(staged, staged.sha256, identity="r2/original", attributes={})
    await store.delete(first)
    assert (await store.open_verified(second)).data == b"verified original"


@pytest.mark.asyncio
async def test_logical_artifact_slot_cannot_publish_changed_content(store):
    first = await store.put_staged(PutObjectRequest(parts(b"first"), 100, "text/plain"))
    second = await store.put_staged(PutObjectRequest(parts(b"other"), 100, "text/plain"))
    ref = await store.promote(first, first.sha256, identity="r1/normalized", attributes={})
    with pytest.raises(ObjectIntegrityError):
        await store.promote(second, second.sha256, identity="r1/normalized", attributes={})
    assert (await store.open_verified(ref)).data == b"first"


@pytest.mark.asyncio
async def test_provider_errors_are_redacted(store):
    store._client.fail = RuntimeError("owned-secret http://provider/bucket/key")
    with pytest.raises(ObjectUnavailable) as caught:
        await store.put_staged(PutObjectRequest(parts(), 100, "text/plain"))
    rendered = "".join(traceback.format_exception(caught.value))
    assert "owned-secret" not in rendered and "http://provider" not in rendered


@pytest.mark.asyncio
async def test_deadline_cancels_and_settles_native_request():
    cancelled = asyncio.Event()

    class Hanging(MemoryS3):
        async def put_object(self, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    store = S3ObjectStore(config(timeout_seconds=0.02), scope=VALIDATION_SCOPE, client=Hanging())
    with pytest.raises(ObjectUnavailable):
        await store.put_staged(PutObjectRequest(parts(), 100, "text/plain"))
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_caller_cancellation_preserved():
    started, cancelled = asyncio.Event(), asyncio.Event()

    class Hanging(MemoryS3):
        async def put_object(self, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    store = S3ObjectStore(config(), scope=VALIDATION_SCOPE, client=Hanging())
    task = asyncio.create_task(store.put_staged(PutObjectRequest(parts(), 100, "text/plain")))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_deadline_waits_for_delayed_request_cleanup():
    settled = asyncio.Event()

    class Delayed(MemoryS3):
        async def put_object(self, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.05)
                settled.set()

    value = S3ObjectStore(config(timeout_seconds=0.02), scope=VALIDATION_SCOPE, client=Delayed())
    with pytest.raises(ObjectUnavailable):
        await value.put_staged(PutObjectRequest(parts(), 100, "text/plain"))
    assert settled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("force_transport", [False, True])
async def test_cancelled_get_disposes_late_body_and_settles_transport(force_transport):
    released, settled = asyncio.Event(), asyncio.Event()
    late_body = Body(b"late")

    class Delayed(MemoryS3):
        async def get_object(self, **kwargs):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if force_transport:
                    await released.wait()
                else:
                    await asyncio.sleep(0.05)
                settled.set()
                return {"Body": late_body, "ContentLength": 4}

        async def close(self):
            released.set()

    client = Delayed()
    value = S3ObjectStore(config(timeout_seconds=0.02), scope=VALIDATION_SCOPE, client=client)
    staged = await value.put_staged(PutObjectRequest(parts(), 100, "text/plain"))
    try:
        with pytest.raises(ObjectUnavailable):
            await value.open_verified(staged.ref)
        assert settled.is_set()
        assert late_body.closed
        if force_transport:
            assert value._closed
        assert not value._pending
    finally:
        released.set()
        await asyncio.sleep(0.06)


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent_close", [False, True])
async def test_partial_body_repeated_caller_cancel_cannot_interrupt_cleanup(concurrent_close):
    reading, cleaning, settled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Partial(Body):
        async def read(self, size=-1):
            if self.stream.tell() == 0:
                return self.stream.read(2)
            reading.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await asyncio.sleep(0.05)
                settled.set()

    body = Partial(b"verified original")
    client = MemoryS3()
    value = S3ObjectStore(config(), scope=VALIDATION_SCOPE, client=client)
    staged = await value.put_staged(PutObjectRequest(parts(), 100, "text/plain"))

    async def get_object(**kwargs):
        return {"Body": body, "ContentLength": 17}

    client.get_object = get_object
    task = asyncio.create_task(value.open_verified(staged.ref))
    await reading.wait()
    task.cancel()
    await cleaning.wait()
    closing = asyncio.create_task(value.aclose()) if concurrent_close else None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    if closing is not None:
        await closing
        assert client.closed
    assert settled.is_set()
    assert body.closed
    assert not value._pending


@pytest.mark.asyncio
@pytest.mark.parametrize("closing", [False, True])
async def test_initialization_cancel_or_close_disposes_late_client(monkeypatch, closing):
    import tap.platform.storage.s3 as module

    started, settled = asyncio.Event(), asyncio.Event()
    client = MemoryS3()
    client.closed = False

    async def close():
        client.closed = True

    client.close = close

    class Context:
        async def __aenter__(self):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0.05)
                settled.set()
                return client

        async def __aexit__(self, *args):
            await close()

    class Session:
        def set_config_variable(self, *args):
            pass

        def create_client(self, *args, **kwargs):
            return Context()

    monkeypatch.setattr(module, "get_session", Session)
    value = S3ObjectStore(config(), scope=VALIDATION_SCOPE)
    task = asyncio.create_task(value.put_staged(PutObjectRequest(parts(), 100, "text/plain")))
    await started.wait()
    if closing:
        await value.aclose()
    else:
        task.cancel()
    with pytest.raises((asyncio.CancelledError, ObjectUnavailable)):
        await task
    assert settled.is_set() and client.closed
    assert client.calls == []
    assert not value._pending


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_replace_caller_cancellation():
    started, settled = asyncio.Event(), asyncio.Event()

    class Hanging(MemoryS3):
        async def put_object(self, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                settled.set()

    value = S3ObjectStore(config(), scope=VALIDATION_SCOPE, client=Hanging())
    settle = value._settle

    async def failing_cleanup(task):
        await settle(task)
        raise ObjectUnavailable("simulated cleanup failure")

    value._settle = failing_cleanup
    operation = asyncio.create_task(value.put_staged(PutObjectRequest(parts(), 100, "text/plain")))
    await started.wait()
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert settled.is_set()
    assert not value._pending
