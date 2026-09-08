"""Provider-neutral byte-storage behavior, shared with real owned S3 tests."""

from datetime import datetime, timedelta, timezone

import pytest

from tap.platform.storage.objects import ObjectIntegrityError, ObjectRef, PutObjectRequest


async def parts(data=b"verified original"):
    yield data[:3]
    yield data[3:]


async def exercise_object_round_trip(store):
    staged = await store.put_staged(PutObjectRequest(parts(), 1024, "text/plain"))
    ref = await store.promote(
        staged, staged.sha256, identity="revision-1/original", attributes={"kind": "original"}
    )
    assert isinstance(ref, ObjectRef)
    assert "/" not in ref and "http" not in ref and "bucket" not in ref
    result = await store.open_verified(ref)
    assert result.data == b"verified original"
    assert result.sha256 == staged.sha256
    assert result.identity == "revision-1/original"
    assert dict(result.attributes) == {"kind": "original"}
    replay = await store.promote(
        staged, staged.sha256, identity="revision-1/original", attributes={"kind": "original"}
    )
    assert replay == ref
    await store.delete(ref)
    with pytest.raises(ObjectIntegrityError):
        await store.open_verified(ref)
    await store.delete(ref)


async def exercise_staging_visibility(store):
    pinned = await store.put_staged(PutObjectRequest(parts(b"pinned"), 1024, "text/plain"))
    orphan = await store.put_staged(PutObjectRequest(parts(b"orphan"), 1024, "text/plain"))
    receipt = await store.scavenge_staging(
        now=datetime.now(timezone.utc) + timedelta(hours=2),
        visible_refs=frozenset({pinned.ref}),
        limit=20,
    )
    assert orphan.ref in receipt.removed
    assert pinned.ref not in receipt.removed
    assert (await store.open_verified(pinned.ref)).data == b"pinned"
