"""Native async S3 bytes with conditional immutable publication and verified manifests."""

from __future__ import annotations

import asyncio
import json
import math
import re
import tempfile
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import wraps
from hashlib import sha256
from typing import Any, ParamSpec, TypeVar, cast
from urllib.parse import urlsplit
from uuid import uuid4

from aiobotocore.config import AioConfig  # type: ignore[import-untyped]
from aiobotocore.session import get_session  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from pydantic import SecretStr

from tap.modules.access.domain.context import ProjectScopeContext
from tap.platform.db.project_scope import require_project_scope
from tap.platform.storage.objects import (
    MAX_OBJECT_BYTES,
    ObjectDescriptor,
    ObjectIntegrityError,
    ObjectMissingError,
    ObjectRef,
    ObjectStorePort,
    ObjectUnavailable,
    PutObjectRequest,
    StagedObject,
    StagingRef,
    StagingScavengeReceipt,
    VerifiedObject,
)

_P = ParamSpec("_P")
_T = TypeVar("_T")
_DEADLINE: ContextVar[float | None] = ContextVar("s3_object_deadline", default=None)
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_SEGMENT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,255}\Z")
_MISSING = {"NoSuchKey", "NotFound", "404"}


class _ArgumentValueError(ValueError):
    """Caller arguments rejected before provider translation."""


class _ArgumentTypeError(TypeError):
    """Caller argument types rejected before provider translation."""


def _hash(data: bytes) -> str:
    return sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _identity(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) > 512
        or any(not _SEGMENT.fullmatch(part) or part in {".", ".."} for part in value.split("/"))
    ):
        raise _ArgumentValueError("object logical identity is invalid")


def _attributes(value: Mapping[str, str]) -> None:
    if not isinstance(value, Mapping) or len(value) > 16:
        raise _ArgumentValueError("object attributes are invalid")
    for key, item in value.items():
        if (
            not isinstance(key, str)
            or not _SEGMENT.fullmatch(key)
            or not isinstance(item, str)
            or not 1 <= len(item) <= 512
            or any(ord(c) < 32 for c in item)
        ):
            raise _ArgumentValueError("object attribute is invalid")


def _content_type(value: str) -> None:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+", value) is None
    ):
        raise _ArgumentValueError("object content type is invalid")


def _boundary(operation: Callable[_P, Awaitable[_T]]) -> Callable[_P, Coroutine[Any, Any, _T]]:
    @wraps(operation)
    async def call(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        owner = cast(S3ObjectStore, args[0])
        inherited = _DEADLINE.get()
        deadline = asyncio.get_running_loop().time() + owner._config.timeout_seconds
        token = _DEADLINE.set(deadline if inherited is None else min(inherited, deadline))
        try:
            owner._ensure_open()
            return await operation(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except ObjectMissingError:
            raise ObjectMissingError("object does not exist") from None
        except ObjectIntegrityError:
            raise ObjectIntegrityError("object integrity verification failed") from None
        except ObjectUnavailable:
            raise ObjectUnavailable("object provider operation failed") from None
        except (_ArgumentValueError, _ArgumentTypeError):
            raise
        except Exception:
            raise ObjectUnavailable("object provider operation failed") from None
        finally:
            _DEADLINE.reset(token)

    return call


@dataclass(frozen=True, slots=True)
class S3ObjectConfig:
    endpoint: str
    bucket: str
    region: str
    access_key: SecretStr = field(repr=False)
    secret_key: SecretStr = field(repr=False)
    store_id: str
    timeout_seconds: float = 15

    def __post_init__(self) -> None:
        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or (
                parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            )
        ):
            raise ValueError("object endpoint must be an explicit secure or loopback endpoint")
        if re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", self.bucket) is None:
            raise ValueError("object bucket is invalid")
        for value in (self.region, self.store_id):
            if not isinstance(value, str) or _SEGMENT.fullmatch(value) is None:
                raise ValueError("object configuration identity is invalid")
        if any(
            not isinstance(value, SecretStr) or not value.get_secret_value().strip()
            for value in (self.access_key, self.secret_key)
        ):
            raise ValueError("explicit object credentials are required")
        if (
            isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 60
        ):
            raise ValueError("object timeout is invalid")


class S3ObjectStore(ObjectStorePort):
    def __init__(
        self, config: S3ObjectConfig, *, scope: ProjectScopeContext, client: Any = None
    ) -> None:
        if not isinstance(config, S3ObjectConfig):
            raise TypeError("object store requires validated configuration")
        self.scope = require_project_scope(scope)
        self._config = config
        self._namespace = _hash(f"{scope.enterprise_id}\0{scope.project_id}".encode())
        self._store = _hash(config.store_id.encode())
        self._staging_prefix = f"staging/{self._namespace}/"
        self._client = client
        self._client_context: Any = None
        self._client_lock = asyncio.Lock()
        self._closed = False
        self._pending: set[asyncio.Future[Any]] = set()
        self._transport_close: asyncio.Task[None] | None = None
        self._settlements: dict[asyncio.Future[Any], asyncio.Task[None]] = {}

    def _ensure_open(self) -> None:
        if self._closed:
            raise ObjectUnavailable("object store is closed")

    async def _get_client(self) -> Any:
        self._ensure_open()
        if self._client is None:
            async with self._client_lock:
                self._ensure_open()
                if self._client is None:
                    session = get_session()
                    # Empty explicit config and credentials prevent ambient profile/IMDS fallback.
                    session.set_config_variable("config_file", "/dev/null")
                    session.set_config_variable("credentials_file", "/dev/null")
                    session.set_config_variable("profile", None)
                    context = session.create_client(
                        "s3",
                        endpoint_url=self._config.endpoint,
                        region_name=self._config.region,
                        aws_access_key_id=self._config.access_key.get_secret_value(),
                        aws_secret_access_key=self._config.secret_key.get_secret_value(),
                        verify=self._config.endpoint.startswith("https:"),
                        config=AioConfig(
                            signature_version="s3v4",
                            connect_timeout=min(5, self._config.timeout_seconds),
                            read_timeout=self._config.timeout_seconds,
                            max_pool_connections=10,
                            retries={"mode": "standard", "total_max_attempts": 1},
                            proxies={},
                            s3={"addressing_style": "path"},
                            request_checksum_calculation="when_required",
                            response_checksum_validation="when_required",
                        ),
                    )
                    self._client_context = context
                    try:
                        self._client = await context.__aenter__()
                        caller = asyncio.current_task()
                        if self._closed or (caller is not None and caller.cancelling()):
                            await self._client.close()
                            if caller is not None and caller.cancelling():
                                raise asyncio.CancelledError
                            raise ObjectUnavailable("object store closed during initialization")
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        raise ObjectUnavailable("object provider initialization failed") from None
        return self._client

    async def _call(self, method: str, **kwargs: Any) -> Any:
        deadline = _DEADLINE.get()
        remaining = (
            self._config.timeout_seconds
            if deadline is None
            else deadline - asyncio.get_running_loop().time()
        )
        if remaining <= 0:
            raise ObjectUnavailable("object operation exceeded deadline")

        async def perform() -> Any:
            client = await self._get_client()
            return await getattr(client, method)(Bucket=self._config.bucket, **kwargs)

        return await self._bounded(perform(), remaining)

    async def _finish_transport(self) -> None:
        # Closing the owning client aborts its pooled connections, including
        # requests which have not returned a Body to the adapter yet.
        self._closed = True
        if self._client is not None:
            await self._client.close()
        elif self._client_context is not None:
            await self._client_context.__aexit__(None, None, None)

    async def _close_transport(self) -> None:
        if self._transport_close is None:
            self._transport_close = asyncio.create_task(self._finish_transport())
        await asyncio.shield(self._transport_close)

    def _settlement_for(self, task: asyncio.Future[Any]) -> asyncio.Task[None]:
        cleanup = self._settlements.get(task)
        if cleanup is None:
            cleanup = asyncio.create_task(self._settle(task))
            self._settlements[task] = cleanup
            cleanup.add_done_callback(lambda _: self._settlements.pop(task, None))
        return cleanup

    async def _settle(self, task: asyncio.Future[Any]) -> None:
        task.cancel()
        # Settlement has a separate bounded allowance after the operation's
        # absolute deadline. Repeated caller cancellation cannot interrupt it.
        done, _ = await asyncio.wait({task}, timeout=0.1)
        if not done:
            close = asyncio.create_task(self._close_transport())
            self._pending.add(close)
            done_close, _ = await asyncio.wait({close}, timeout=0.5)
            if not done_close:
                # Keep both close tasks owned; aclose can join the same close.
                if self._transport_close is not None:
                    self._pending.add(self._transport_close)
            else:
                _dispose_result(close)
                self._pending.discard(close)
            done, _ = await asyncio.wait({task}, timeout=0.1)
            if not done:
                task.cancel()
                done, _ = await asyncio.wait({task}, timeout=0.1)
            if not done or not done_close:
                # Retain ownership and reject reuse. aclose must drain these
                # tasks; never replace them with an exception-consuming callback.
                self._closed = True
                task.add_done_callback(_dispose_result)
                raise ObjectUnavailable("object transport cleanup did not settle")
        _dispose_result(task)
        self._pending.discard(task)

    async def _bounded(self, operation: Awaitable[_T], remaining: float) -> _T:
        task = asyncio.ensure_future(operation)
        self._pending.add(task)
        try:
            async with asyncio.timeout(max(0, remaining)):
                return await asyncio.shield(task)
        except (TimeoutError, asyncio.CancelledError) as error:
            caller = asyncio.current_task()
            caller_cancel = (
                isinstance(error, asyncio.CancelledError)
                and caller is not None
                and caller.cancelling()
            )
            cleanup = self._settlement_for(task)
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    caller_cancel = True
                except Exception:
                    break
            try:
                cleanup.result()
            except Exception:
                if caller_cancel:
                    raise asyncio.CancelledError from None
                raise ObjectUnavailable("object transport cleanup failed") from None
            if caller_cancel:
                raise asyncio.CancelledError from None
            raise ObjectUnavailable("object operation exceeded deadline") from None
        except ClientError:
            raise
        except (ObjectUnavailable, ObjectIntegrityError):
            raise
        except Exception:
            raise ObjectUnavailable("object provider operation failed") from None
        finally:
            if task.done():
                self._pending.discard(task)

    def _parse(self, ref: ObjectRef | StagingRef) -> tuple[str, ...]:
        if not isinstance(ref, (ObjectRef, StagingRef)):
            raise _ArgumentTypeError("object reference type is invalid")
        parts = tuple(str(ref).split("."))
        if len(parts) < 3 or parts[1:3] != (self._store, self._namespace):
            raise _ArgumentValueError("object reference scope or store does not match")
        valid: object
        if isinstance(ref, ObjectRef):
            valid = len(parts) == 4 and parts[0] == "obj1" and _HEX.fullmatch(parts[3])
        else:
            valid = (
                len(parts) == 6
                and parts[0] == "stg1"
                and re.fullmatch(r"[0-9a-f]{32}", parts[3])
                and _HEX.fullmatch(parts[4])
                and parts[5].isascii()
                and parts[5].isdecimal()
                and 1 <= int(parts[5]) <= MAX_OBJECT_BYTES
            )
        if not valid:
            raise _ArgumentValueError("object reference is malformed")
        return parts

    def _staging_key(self, parts: tuple[str, ...]) -> str:
        return self._staging_prefix + ".".join(parts[3:])

    def _manifest_key(self, parts: tuple[str, ...]) -> str:
        return f"objects/{self._namespace}/manifests/{parts[3]}"

    def _payload_key(self, manifest: Mapping[str, Any]) -> str:
        identity_digest = _hash(manifest["identity"].encode())
        return f"objects/{self._namespace}/payloads/{identity_digest}/{manifest['sha256']}"

    def _identity_key(self, manifest: Mapping[str, Any]) -> str:
        return f"objects/{self._namespace}/identities/{_hash(manifest['identity'].encode())}"

    @_boundary
    async def put_staged(self, request: PutObjectRequest) -> StagedObject:
        if not isinstance(request, PutObjectRequest):
            raise _ArgumentTypeError("object staging request is invalid")
        if type(request.max_bytes) is not int or not 1 <= request.max_bytes <= MAX_OBJECT_BYTES:
            raise _ArgumentValueError("object byte bound is invalid")
        _content_type(request.content_type)
        digest = sha256()
        size = 0
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as spool:
            async for part in request.content:
                if not isinstance(part, bytes) or len(part) > request.max_bytes - size:
                    raise ObjectIntegrityError("object upload exceeds its bound")
                size += len(part)
                digest.update(part)
                spool.write(part)
            if not size:
                raise ObjectIntegrityError("object upload is empty")
            ref = StagingRef(
                f"stg1.{self._store}.{self._namespace}.{uuid4().hex}.{digest.hexdigest()}.{size}"
            )
            spool.seek(0)
            try:
                await self._call(
                    "put_object",
                    Key=self._staging_key(self._parse(ref)),
                    Body=spool,
                    ContentLength=size,
                    ContentType=request.content_type,
                    IfNoneMatch="*",
                    Metadata={"stagedat": datetime.now(timezone.utc).isoformat()},
                )
            except ClientError:
                raise ObjectUnavailable("object staging failed") from None
        return StagedObject(ref, "sha256:" + digest.hexdigest(), size, request.content_type)

    async def _read(
        self, key: str, *, maximum: int = MAX_OBJECT_BYTES
    ) -> tuple[bytes, Mapping[str, Any]]:
        try:
            result = await self._call("get_object", Key=key)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in _MISSING:
                raise ObjectMissingError("object does not exist") from None
            raise ObjectUnavailable("object read failed") from None
        body = result.get("Body")
        try:
            length = result.get("ContentLength")
            if type(length) is not int or not 0 < length <= maximum:
                raise ObjectIntegrityError("object size is invalid")
            chunks = bytearray()
            while True:
                deadline = _DEADLINE.get()
                remaining = (
                    self._config.timeout_seconds
                    if deadline is None
                    else deadline - asyncio.get_running_loop().time()
                )
                part = await self._bounded(
                    body.read(min(64 * 1024, maximum - len(chunks) + 1)), remaining
                )
                if not isinstance(part, bytes) or len(chunks) + len(part) > maximum:
                    raise ObjectIntegrityError("object read exceeds bound")
                if not part:
                    break
                chunks.extend(part)
            if len(chunks) != length:
                raise ObjectIntegrityError("object length differs")
            return bytes(chunks), cast(Mapping[str, Any], result)
        finally:
            if body is not None:
                body.close()

    async def _manifest(self, ref: ObjectRef) -> Mapping[str, Any]:
        parts = self._parse(ref)
        data, _ = await self._read(self._manifest_key(parts), maximum=16384)
        try:
            manifest = json.loads(data)
            if (
                _hash(data) != parts[3]
                or _canonical(manifest) != data
                or set(manifest) != {"v", "identity", "attributes", "sha256", "size", "contentType"}
            ):
                raise ValueError
            if (
                type(manifest["v"]) is not int
                or manifest["v"] != 1
                or not _HEX.fullmatch(manifest["sha256"])
            ):
                raise ValueError
            if type(manifest["size"]) is not int or not 1 <= manifest["size"] <= MAX_OBJECT_BYTES:
                raise ValueError
            _identity(manifest["identity"])
            _attributes(manifest["attributes"])
            _content_type(manifest["contentType"])
        except (ValueError, TypeError, KeyError):
            raise ObjectIntegrityError("object manifest is invalid") from None
        return cast(Mapping[str, Any], manifest)

    @_boundary
    async def describe_verified(self, ref: ObjectRef) -> ObjectDescriptor:
        manifest = await self._manifest(ref)
        return ObjectDescriptor(
            "sha256:" + manifest["sha256"],
            manifest["size"],
            manifest["contentType"],
            manifest["identity"],
            tuple(sorted(manifest["attributes"].items())),
        )

    @_boundary
    async def open_verified(self, ref: ObjectRef | StagingRef) -> VerifiedObject:
        parts = self._parse(ref)
        if isinstance(ref, StagingRef):
            data, properties = await self._read(self._staging_key(parts))
            digest, size = parts[4], int(parts[5])
            content_type = properties.get("ContentType", "application/octet-stream")
            identity, attributes = "staged", ()
        else:
            manifest = await self._manifest(ref)
            data, _ = await self._read(self._payload_key(manifest))
            digest, size = manifest["sha256"], manifest["size"]
            content_type = manifest["contentType"]
            identity, attributes = (
                manifest["identity"],
                tuple(sorted(manifest["attributes"].items())),
            )
        if len(data) != size or _hash(data) != digest:
            raise ObjectIntegrityError("object digest differs")
        return VerifiedObject(data, "sha256:" + digest, size, content_type, identity, attributes)

    async def _put_immutable(self, key: str, data: bytes, content_type: str) -> None:
        try:
            await self._call(
                "put_object",
                Key=key,
                Body=data,
                ContentLength=len(data),
                ContentType=content_type,
                IfNoneMatch="*",
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") not in {
                "PreconditionFailed",
                "ConditionalRequestConflict",
                "412",
                "409",
            }:
                raise ObjectUnavailable("object immutable publication failed") from None
        readback, _ = await self._read(key, maximum=max(len(data), 1))
        if readback != data:
            raise ObjectIntegrityError("object immutable publication conflicts")

    @_boundary
    async def promote(
        self,
        staged: StagedObject,
        expected_sha256: str,
        *,
        identity: str,
        attributes: Mapping[str, str],
    ) -> ObjectRef:
        if not isinstance(staged, StagedObject):
            raise _ArgumentTypeError("object promotion requires a staged object")
        self._parse(staged.ref)
        _identity(identity)
        _attributes(attributes)
        if (
            not isinstance(expected_sha256, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", expected_sha256) is None
        ):
            raise _ArgumentValueError("object expected digest is invalid")
        if staged.sha256 != expected_sha256:
            raise ObjectIntegrityError("object expected digest differs")
        verified = await self.open_verified(staged.ref)
        if verified.sha256 != expected_sha256 or verified.size != staged.size:
            raise ObjectIntegrityError("staging facts differ")
        manifest = {
            "v": 1,
            "identity": identity,
            "attributes": dict(attributes),
            "sha256": expected_sha256.removeprefix("sha256:"),
            "size": staged.size,
            "contentType": staged.content_type,
        }
        data = _canonical(manifest)
        ref = ObjectRef(f"obj1.{self._store}.{self._namespace}.{_hash(data)}")
        await self._put_immutable(self._payload_key(manifest), verified.data, staged.content_type)
        await self._put_immutable(self._identity_key(manifest), data, "application/json")
        await self._put_immutable(self._manifest_key(self._parse(ref)), data, "application/json")
        return ref

    async def _delete_key(self, key: str, *, etag: str | None = None) -> bool:
        try:
            await self._call("delete_object", Key=key, **({"IfMatch": etag} if etag else {}))
            return True
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in _MISSING | {
                "PreconditionFailed",
                "412",
            }:
                return False
            raise ObjectUnavailable("object deletion failed") from None

    @_boundary
    async def delete(self, ref: ObjectRef | StagingRef) -> None:
        parts = self._parse(ref)
        if isinstance(ref, StagingRef):
            await self._delete_key(self._staging_key(parts))
            return
        try:
            manifest = await self._manifest(ref)
        except ObjectMissingError:
            return
        await self._delete_key(self._payload_key(manifest))
        await self._delete_key(self._identity_key(manifest))
        await self._delete_key(self._manifest_key(parts))

    @_boundary
    async def scavenge_staging(
        self, *, now: datetime, visible_refs: frozenset[StagingRef], limit: int = 100
    ) -> StagingScavengeReceipt:
        if type(limit) is not int or not 1 <= limit <= 1000 or now.tzinfo is None:
            raise _ArgumentValueError("object scavenger bounds are invalid")
        for ref in visible_refs:
            self._parse(ref)
        scanned = 0
        removed: list[StagingRef] = []
        continuation: str | None = None
        while scanned < limit:
            try:
                page = await self._call(
                    "list_objects_v2",
                    Prefix=self._staging_prefix,
                    MaxKeys=min(1000, limit - scanned),
                    **({"ContinuationToken": continuation} if continuation else {}),
                )
            except ClientError:
                raise ObjectUnavailable("object staging listing failed") from None
            for item in page.get("Contents", []):
                if scanned >= limit:
                    break
                scanned += 1
                try:
                    key = item["Key"]
                    if not isinstance(key, str) or not key.startswith(self._staging_prefix):
                        continue
                    ref = StagingRef(
                        f"stg1.{self._store}.{self._namespace}.{key.removeprefix(self._staging_prefix)}"
                    )
                    self._parse(ref)
                    modified, etag = item["LastModified"], item["ETag"]
                    if (
                        not isinstance(modified, datetime)
                        or modified.tzinfo is None
                        or not isinstance(etag, str)
                        or not etag
                    ):
                        raise ValueError
                except (ValueError, KeyError, TypeError):
                    raise ObjectIntegrityError("staging listing is malformed") from None
                if ref not in visible_refs and now - modified >= timedelta(hours=1):
                    if await self._delete_key(key, etag=etag):
                        removed.append(ref)
            if not page.get("IsTruncated"):
                break
            next_token = page.get("NextContinuationToken")
            if not isinstance(next_token, str) or not next_token or next_token == continuation:
                raise ObjectIntegrityError("staging pagination is malformed")
            continuation = next_token
        return StagingScavengeReceipt(scanned, tuple(removed))

    @_boundary
    async def ensure_bucket(self) -> None:
        try:
            await self._call("head_bucket")
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") not in _MISSING | {"NoSuchBucket"}:
                raise ObjectUnavailable("object bucket probe failed") from None
            try:
                await self._call(
                    "create_bucket",
                    **(
                        {"CreateBucketConfiguration": {"LocationConstraint": self._config.region}}
                        if self._config.region != "us-east-1"
                        else {}
                    ),
                )
            except ClientError as error:
                if error.response.get("Error", {}).get("Code") != "BucketAlreadyOwnedByYou":
                    raise ObjectUnavailable("object bucket creation failed") from None
        if not await self.is_private():
            raise ObjectUnavailable("object bucket is not private")

    @_boundary
    async def is_private(self) -> bool:
        try:
            await self._call("head_bucket")
            await self._call("get_bucket_policy")
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "NoSuchBucketPolicy":
                return True
            raise ObjectUnavailable("object privacy probe failed") from None
        return False

    async def aclose(self) -> None:
        async def drain() -> None:
            self._closed = True
            for task in tuple(self._pending):
                if task is not self._transport_close:
                    await self._settlement_for(task)
            if self._client is not None or self._client_context is not None:
                close = asyncio.create_task(self._close_transport())
                done, _ = await asyncio.wait({close}, timeout=0.8)
                if not done:
                    self._pending.add(close)
                    raise ObjectUnavailable("object transport close did not settle")
                close.result()
            if self._transport_close is not None and self._transport_close.done():
                self._pending.discard(self._transport_close)

        cleanup = asyncio.create_task(drain())
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        cleanup.result()
        if cancelled:
            raise asyncio.CancelledError


def _dispose_result(task: asyncio.Future[Any]) -> None:
    try:
        result = task.result()
        if isinstance(result, Mapping):
            body = result.get("Body")
            if body is not None:
                body.close()
    except BaseException:
        pass
