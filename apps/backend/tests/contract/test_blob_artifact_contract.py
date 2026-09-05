"""Provider-neutral Tapper artifact integrity contract."""

from __future__ import annotations

import asyncio
import gzip
import json
import traceback
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from azure.core.exceptions import (
    ResourceExistsError,
    ResourceModifiedError,
    ResourceNotFoundError,
    ServiceRequestError,
)
from azure.storage.blob import ContainerProperties
from pydantic import SecretStr

from tap.modules.knowledge.adapters import blob_artifacts
from tap.modules.knowledge.adapters.blob_artifacts import (
    ARTIFACTS_CONTAINER,
    ORIGINALS_CONTAINER,
    ArtifactIntegrityError,
    ArtifactProviderUnavailable,
    AzureBlobArtifactConfig,
    AzureBlobArtifactStore,
    artifact_locator,
    decode_chunks_artifact,
    decode_embeddings_artifact,
    decode_normalized_artifact,
    encode_chunks_artifact,
    encode_embeddings_artifact,
    encode_normalized_artifact,
)
from tap.modules.knowledge.domain.documents import (
    PARSER_VERSION,
    BlockKind,
    ChunkDraft,
    DocumentId,
    MediaType,
    NormalizedArtifact,
    NormalizedBlock,
    RevisionId,
    canonical_sha256,
    chunk_id_for,
    logical_chunk_id_for,
    revision_id_for,
)
from tap.modules.knowledge.ports.documents import (
    ArtifactLocator,
    DeletionTarget,
    EmbeddingArtifact,
    StagedOriginal,
)
from tap.modules.knowledge.ports.errors import ArtifactIntegrityFailure, ArtifactUnavailable

SOURCE_HASH = "sha256:" + "a" * 64
DOCUMENT_ID = DocumentId("doc_a")
REVISION = str(revision_id_for(DOCUMENT_ID, SOURCE_HASH, PARSER_VERSION))


def normalized_artifact() -> NormalizedArtifact:
    return NormalizedArtifact(
        filename="policy.md",
        media_type=MediaType.MARKDOWN,
        source_hash=SOURCE_HASH,
        document_id=DOCUMENT_ID,
        revision_id=RevisionId(REVISION),
        blocks=(
            NormalizedBlock(
                block_id="block-1",
                kind=BlockKind.PARAGRAPH,
                text="Tapper policy.",
                heading_path=("Policy",),
                page=None,
                paragraph_index=0,
                start_offset=0,
                end_offset=14,
            ),
        ),
    )


def chunk_artifact() -> tuple[ChunkDraft, ...]:
    content = "Tapper policy."
    anchor = '{"blockId":"block-1"}'
    content_hash = canonical_sha256(content.encode())
    return (
        ChunkDraft(
            chunk_id=chunk_id_for(RevisionId(REVISION), anchor, content_hash),
            logical_chunk_id=logical_chunk_id_for(DOCUMENT_ID, anchor),
            root_id=DOCUMENT_ID,
            parent_id=None,
            content=content,
            anchor_json=anchor,
            source_content_hash=SOURCE_HASH,
            chunk_content_hash=content_hash,
        ),
    )


def test_canonical_artifact_envelopes_are_deterministic_and_round_trip_exactly() -> None:
    """Noncanonical or timestamped encoding would break content-addressed retries."""
    normalized = normalized_artifact()
    chunks = chunk_artifact()
    embeddings = EmbeddingArtifact(
        "tapper-embedding",
        3,
        ((0.1, 0.2, 0.3),),
        tuple(str(chunk.chunk_id) for chunk in chunks),
    )

    normalized_bytes = encode_normalized_artifact(REVISION, normalized)
    chunks_bytes = encode_chunks_artifact(REVISION, chunks)
    embedding_bytes = encode_embeddings_artifact(REVISION, SOURCE_HASH, embeddings)

    assert normalized_bytes.endswith(b"\n")
    assert chunks_bytes == encode_chunks_artifact(REVISION, chunks)
    assert embedding_bytes == encode_embeddings_artifact(REVISION, SOURCE_HASH, embeddings)
    assert gzip.decompress(chunks_bytes).endswith(b"\n")
    assert gzip.decompress(embedding_bytes).endswith(b"\n")
    assert decode_normalized_artifact(normalized_bytes, expected_revision=REVISION) == normalized
    assert decode_chunks_artifact(chunks_bytes, expected_revision=REVISION) == chunks
    assert decode_embeddings_artifact(embedding_bytes, expected_revision=REVISION) == embeddings


@pytest.mark.parametrize("kind", ("normalized", "chunks", "embeddings"))
def test_artifact_reads_reject_payload_hash_or_envelope_widening(kind: str) -> None:
    """A syntactically valid but rebound artifact must not cross the provider port."""
    if kind == "normalized":
        raw = encode_normalized_artifact(REVISION, normalized_artifact())
        envelope = json.loads(raw)
        envelope["payloadSha256"] = "sha256:" + "f" * 64
        corrupted = (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode()
        decoder = decode_normalized_artifact
    elif kind == "chunks":
        raw = gzip.decompress(encode_chunks_artifact(REVISION, chunk_artifact()))
        lines = raw.splitlines()
        envelope = json.loads(lines[0])
        envelope["extra"] = True
        corrupted = gzip.compress(
            (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode()
            + b"\n".join(lines[1:])
            + b"\n",
            mtime=0,
        )
        decoder = decode_chunks_artifact
    else:
        artifact = EmbeddingArtifact(
            "tapper-embedding",
            3,
            ((0.1, 0.2, 0.3),),
            tuple(str(chunk.chunk_id) for chunk in chunk_artifact()),
        )
        raw = gzip.decompress(encode_embeddings_artifact(REVISION, SOURCE_HASH, artifact))
        lines = raw.splitlines()
        envelope = json.loads(lines[0])
        envelope["payloadSha256"] = "sha256:" + "e" * 64
        corrupted = gzip.compress(
            (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode()
            + b"\n".join(lines[1:])
            + b"\n",
            mtime=0,
        )
        decoder = decode_embeddings_artifact

    with pytest.raises(ArtifactIntegrityError):
        decoder(corrupted, expected_revision=REVISION)


def test_artifact_locators_are_closed_identity_only_values() -> None:
    """Credentials or SAS query text in a durable locator would leak provider authority."""
    original = artifact_locator(
        ORIGINALS_CONTAINER,
        f"revisions/{REVISION}/{SOURCE_HASH.removeprefix('sha256:')}",
    )
    artifact = artifact_locator(
        ARTIFACTS_CONTAINER,
        f"revisions/{REVISION}/normalized-v1.json",
    )

    assert str(original).startswith("tapper-originals/")
    assert str(artifact).startswith("tapper-artifacts/")
    for invalid in (
        "tapper-originals/blob?sig=secret",
        "https://127.0.0.1/blob",
        "other/blob",
        "tapper-artifacts/../escape",
    ):
        with pytest.raises(ValueError):
            artifact_locator(*invalid.split("/", 1))


def test_chunk_artifact_rejects_semantic_hash_rebinding_with_valid_envelope_hash() -> None:
    """Rehashing a malicious envelope cannot make a false chunk provenance fact valid."""
    decoded = gzip.decompress(encode_chunks_artifact(REVISION, chunk_artifact()))
    lines = decoded.splitlines()
    header = json.loads(lines[0])
    row = json.loads(lines[1])
    row["chunkContentHash"] = "sha256:" + "f" * 64
    payload = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
    header["payloadSha256"] = canonical_sha256(payload)
    corrupted = gzip.compress(
        (json.dumps(header, sort_keys=True, separators=(",", ":")) + "\n").encode() + payload,
        mtime=0,
    )

    with pytest.raises(ArtifactIntegrityError):
        decode_chunks_artifact(corrupted, expected_revision=REVISION)


@pytest.mark.parametrize("field", ("chunk_id", "logical_chunk_id", "root_id"))
def test_chunk_artifact_rejects_stable_identity_rebinding(field: str) -> None:
    """Valid-looking IDs must still be derived from revision/root/anchor/content facts."""
    chunk = chunk_artifact()[0]
    changes: dict[str, object] = {
        "chunk_id": "h_" + "f" * 64,
        "logical_chunk_id": "lc_" + "e" * 64,
        "root_id": DocumentId("doc_other"),
    }

    with pytest.raises(ArtifactIntegrityError):
        encode_chunks_artifact(REVISION, (replace(chunk, **{field: changes[field]}),))


def test_chunk_artifact_rejects_coordinated_document_source_revision_rebinding() -> None:
    """Recomputing every row ID must not detach a revision from its root/source/parser facts."""
    document_b = DocumentId("doc_b")
    source_b = "sha256:" + "b" * 64
    content = "Tapper policy rebound."
    anchor = '{"blockId":"block-b"}'
    content_hash = canonical_sha256(content.encode())
    rebound = ChunkDraft(
        chunk_id=chunk_id_for(RevisionId(REVISION), anchor, content_hash),
        logical_chunk_id=logical_chunk_id_for(document_b, anchor),
        root_id=document_b,
        parent_id=None,
        content=content,
        anchor_json=anchor,
        source_content_hash=source_b,
        chunk_content_hash=content_hash,
    )

    with pytest.raises(ArtifactIntegrityError):
        encode_chunks_artifact(REVISION, (rebound,))

    raw = gzip.decompress(encode_chunks_artifact(REVISION, chunk_artifact()))
    lines = raw.splitlines()
    header = json.loads(lines[0])
    row = json.loads(lines[1])
    header["documentId"] = str(document_b)
    header["sourceContentHash"] = source_b
    row.update(
        {
            "chunkContentHash": content_hash,
            "chunkId": str(rebound.chunk_id),
            "content": content,
            "logicalChunkId": str(rebound.logical_chunk_id),
            "rootId": str(document_b),
        }
    )
    payload = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
    header["payloadSha256"] = canonical_sha256(payload)
    corrupted = gzip.compress(
        (json.dumps(header, sort_keys=True, separators=(",", ":")) + "\n").encode() + payload,
        mtime=0,
    )

    with pytest.raises(ArtifactIntegrityError):
        decode_chunks_artifact(corrupted, expected_revision=REVISION)


def test_normalized_artifact_rejects_revision_rebinding() -> None:
    """A valid payload hash cannot bind one document/source pair to another revision locator."""
    rebound = replace(normalized_artifact(), revision_id=RevisionId("rev_" + "f" * 64))
    with pytest.raises(ArtifactIntegrityError):
        encode_normalized_artifact("rev_" + "f" * 64, rebound)


def test_embedding_rows_bind_exact_chunk_identity_and_order() -> None:
    """Ordinal-only vectors would permit a manifest reorder without changing the envelope."""
    chunks = chunk_artifact()
    artifact = EmbeddingArtifact(
        "tapper-embedding",
        3,
        ((0.1, 0.2, 0.3),),
        (str(chunks[0].chunk_id),),
    )
    decoded = gzip.decompress(encode_embeddings_artifact(REVISION, SOURCE_HASH, artifact))
    row = json.loads(decoded.splitlines()[1])

    assert row["chunkId"] == str(chunks[0].chunk_id)
    assert (
        decode_embeddings_artifact(
            encode_embeddings_artifact(REVISION, SOURCE_HASH, artifact),
            expected_revision=REVISION,
        ).chunk_ids
        == artifact.chunk_ids
    )


class _Upload:
    filename = "policy.md"
    media_type = "text/markdown"

    def __init__(self, parts: tuple[bytes, ...]) -> None:
        self._parts = parts

    @property
    def content(self):  # type: ignore[no-untyped-def]
        async def stream():  # type: ignore[no-untyped-def]
            for part in self._parts:
                yield part

        return stream()


class _BlobDouble:
    def __init__(
        self,
        *,
        fail_upload: bool = False,
        slow_upload: bool = False,
        slow_delete: bool = False,
    ) -> None:
        self.fail_upload = fail_upload
        self.slow_upload = slow_upload
        self.slow_delete = slow_delete
        self.upload_streamed = False
        self.uploaded = b""
        self.delete_cancelled = False
        self.delete_calls = 0

    async def upload_blob(self, data, *args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        if self.fail_upload:
            raise RuntimeError("injected upload failure")
        if self.slow_upload:
            await asyncio.Event().wait()
        self.upload_streamed = not isinstance(data, (bytes, bytearray)) and hasattr(data, "read")
        if not self.upload_streamed:
            raise AssertionError("original upload was materialized before provider streaming")
        parts: list[bytes] = []
        while part := data.read(4):
            parts.append(part)
        self.uploaded = b"".join(parts)

    async def delete_blob(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        self.delete_calls += 1
        if not self.slow_delete:
            return
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            self.delete_cancelled = True


class _ServiceDouble:
    credential = object()
    account_name = "devstoreaccount1"

    def __init__(self, blob: _BlobDouble) -> None:
        self.blob = blob

    def get_blob_client(self, container: str, blob_name: str) -> _BlobDouble:
        del container, blob_name
        return self.blob

    async def close(self) -> None:
        return


class _ProviderFailureBlob:
    container_name = ORIGINALS_CONTAINER
    blob_name = "staging/provider-failure"
    url = "http://provider.invalid/blob"

    @staticmethod
    def _failure() -> ServiceRequestError:
        return ServiceRequestError("azure account-key=secret")

    async def upload_blob(self, *_args: object, **_kwargs: object) -> None:
        raise self._failure()

    async def delete_blob(self, *_args: object, **_kwargs: object) -> None:
        raise self._failure()

    async def get_blob_properties(self, *_args: object, **_kwargs: object) -> None:
        raise self._failure()

    async def download_blob(self, *_args: object, **_kwargs: object) -> None:
        raise self._failure()

    async def exists(self, *_args: object, **_kwargs: object) -> None:
        raise self._failure()

    async def start_copy_from_url(self, *_args: object, **_kwargs: object) -> None:
        raise self._failure()

    async def abort_copy(self, *_args: object, **_kwargs: object) -> None:
        raise self._failure()


class _ProviderFailureContainer:
    def __init__(self, blob: _ProviderFailureBlob) -> None:
        self._blob = blob

    def list_blobs(self, *_args: object, **_kwargs: object) -> _ProviderFailureContainer:
        return self

    def __aiter__(self) -> _ProviderFailureContainer:
        return self

    async def __anext__(self) -> None:
        raise ServiceRequestError("azure account-key=secret")

    async def get_container_properties(self) -> None:
        raise ServiceRequestError("azure account-key=secret")

    def get_blob_client(self, _blob_name: str) -> _ProviderFailureBlob:
        return self._blob


class _ProviderFailureService:
    credential = object()
    account_name = "devstoreaccount1"

    def __init__(self) -> None:
        self.blob = _ProviderFailureBlob()
        self.container = _ProviderFailureContainer(self.blob)

    def get_blob_client(self, _container: str, _blob_name: str) -> _ProviderFailureBlob:
        return self.blob

    def get_container_client(self, _container: str) -> _ProviderFailureContainer:
        return self.container

    async def create_container(self, *_args: object, **_kwargs: object) -> None:
        raise ServiceRequestError("azure account-key=secret")

    async def close(self) -> None:
        raise ServiceRequestError("azure account-key=secret")


def _store_with_double(
    monkeypatch: pytest.MonkeyPatch,
    blob: _BlobDouble,
    *,
    timeout: float = 1,
) -> AzureBlobArtifactStore:
    service = _ServiceDouble(blob)
    monkeypatch.setattr(
        "tap.modules.knowledge.adapters.blob_artifacts.BlobServiceClient.from_connection_string",
        lambda *args, **kwargs: service,
    )
    return AzureBlobArtifactStore(
        AzureBlobArtifactConfig(
            connection_string=SecretStr("UseDevelopmentStorage=true"),
            operation_timeout_seconds=timeout,
        )
    )


def _store_with_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> AzureBlobArtifactStore:
    service = _ProviderFailureService()
    monkeypatch.setattr(
        "tap.modules.knowledge.adapters.blob_artifacts.BlobServiceClient.from_connection_string",
        lambda *args, **kwargs: service,
    )
    return AzureBlobArtifactStore(
        AzureBlobArtifactConfig(connection_string=SecretStr("UseDevelopmentStorage=true"))
    )


def _store_with_container_properties(
    monkeypatch: pytest.MonkeyPatch,
    properties: object,
) -> AzureBlobArtifactStore:
    class Container:
        async def get_container_properties(self) -> object:
            return properties

    class Service(_ServiceDouble):
        def get_container_client(self, _container: str) -> Container:
            return Container()

    service = Service(_BlobDouble())
    monkeypatch.setattr(
        "tap.modules.knowledge.adapters.blob_artifacts.BlobServiceClient.from_connection_string",
        lambda *args, **kwargs: service,
    )
    return AzureBlobArtifactStore(
        AzureBlobArtifactConfig(connection_string=SecretStr("UseDevelopmentStorage=true"))
    )


@pytest.mark.asyncio
async def test_container_properties_normalizes_actual_pinned_sdk_model_to_closed_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store_with_container_properties(monkeypatch, ContainerProperties())

    properties = await store.container_properties(ORIGINALS_CONTAINER)

    assert type(properties) is dict
    assert properties == {"public_access": None}


@pytest.mark.parametrize("public_access", (None, "blob", "container"))
@pytest.mark.asyncio
async def test_container_properties_accepts_only_closed_sdk_public_access_values(
    monkeypatch: pytest.MonkeyPatch,
    public_access: str | None,
) -> None:
    store = _store_with_container_properties(
        monkeypatch,
        {"public_access": public_access},
    )

    properties = await store.container_properties(ORIGINALS_CONTAINER)

    assert properties == {"public_access": public_access}


class _SecretBearingMalformedContainerProperties:
    def __getitem__(self, _key: str) -> object:
        raise RuntimeError("azure-provider-secret-properties")


@pytest.mark.parametrize(
    "properties",
    (
        {},
        {"public_access": "azure-provider-secret-expanded-value"},
        {"public_access": 1},
        _SecretBearingMalformedContainerProperties(),
    ),
)
@pytest.mark.asyncio
async def test_container_properties_fail_closed_and_redact_malformed_provider_payload(
    monkeypatch: pytest.MonkeyPatch,
    properties: object,
) -> None:
    store = _store_with_container_properties(monkeypatch, properties)

    with pytest.raises(ArtifactUnavailable) as captured:
        await store.container_properties(ORIGINALS_CONTAINER)

    assert isinstance(captured.value, ArtifactProviderUnavailable)
    assert captured.value.__cause__ is None
    assert captured.value.__suppress_context__ is True
    rendered = "".join(traceback.format_exception(captured.value))
    assert "provider-secret" not in rendered


@pytest.mark.parametrize("provider_error", (ServiceRequestError, ValueError, TypeError))
def test_constructor_sdk_failure_is_provider_neutral_and_traceback_redacted(
    monkeypatch: pytest.MonkeyPatch,
    provider_error: type[Exception],
) -> None:
    """A credential-bearing SDK constructor failure must not cross composition."""

    def fail_construction(*_args: object, **_kwargs: object) -> None:
        raise provider_error("azure AccountKey=constructor-secret")

    monkeypatch.setattr(
        "tap.modules.knowledge.adapters.blob_artifacts.BlobServiceClient.from_connection_string",
        fail_construction,
    )
    config = AzureBlobArtifactConfig(connection_string=SecretStr("AccountKey=input-secret"))

    with pytest.raises(ArtifactUnavailable) as caught:
        AzureBlobArtifactStore(config)

    assert isinstance(caught.value, ArtifactProviderUnavailable)
    assert not isinstance(caught.value, (ServiceRequestError, TypeError, ValueError))
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    formatted = "".join(traceback.format_exception(caught.value))
    assert "constructor-secret" not in formatted
    assert "input-secret" not in formatted


def test_constructor_caller_argument_errors_remain_type_or_value_errors() -> None:
    """Configuration validation is caller-owned and must not be relabeled as provider outage."""
    with pytest.raises(ValueError):
        AzureBlobArtifactConfig(connection_string=SecretStr(""))
    with pytest.raises(TypeError):
        AzureBlobArtifactStore(object())  # type: ignore[arg-type]


async def _exercise_public_blob_operation(
    store: AzureBlobArtifactStore,
    operation: str,
) -> None:
    staged = StagedOriginal(
        staging_key="staging/provider-failure",
        filename="policy.md",
        media_type="text/markdown",
        size=3,
        source_content_hash=canonical_sha256(b"abc"),
    )
    normalized_locator = ArtifactLocator(
        f"{ARTIFACTS_CONTAINER}/revisions/{REVISION}/normalized-v1.json"
    )
    chunks_locator = ArtifactLocator(
        f"{ARTIFACTS_CONTAINER}/revisions/{REVISION}/chunks-v1.jsonl.gz"
    )
    embeddings_locator = ArtifactLocator(
        f"{ARTIFACTS_CONTAINER}/revisions/{REVISION}/embeddings/model/3-v1.jsonl.gz"
    )
    if operation == "ensure_containers":
        await store.ensure_containers()
    elif operation == "stage_original":
        await store.stage_original(_Upload((b"abc",)), max_bytes=3)
    elif operation == "commit_original":
        await store.commit_original(staged, REVISION)
    elif operation == "recover_original":
        await store.recover_original(staged.staging_key, REVISION)
    elif operation == "discard_staged":
        await store.discard_staged(staged)
    elif operation == "discard_staging":
        await store.discard_staging(staged.staging_key)
    elif operation == "read_original":
        await store.read_original(
            ArtifactLocator(
                f"{ORIGINALS_CONTAINER}/revisions/{REVISION}/{SOURCE_HASH.removeprefix('sha256:')}"
            )
        )
    elif operation == "write_normalized":
        await store.write_normalized(REVISION, normalized_artifact())
    elif operation == "read_normalized":
        await store.read_normalized(normalized_locator)
    elif operation == "write_chunks":
        await store.write_chunks(REVISION, chunk_artifact())
    elif operation == "read_chunks":
        await store.read_chunks(chunks_locator)
    elif operation == "write_embeddings":
        chunks = chunk_artifact()
        await store.write_embeddings(
            REVISION,
            EmbeddingArtifact(
                "model",
                3,
                ((0.1, 0.2, 0.3),),
                (str(chunks[0].chunk_id),),
            ),
            source_content_hash=SOURCE_HASH,
        )
    elif operation == "read_embeddings":
        await store.read_embeddings(embeddings_locator)
    elif operation == "delete_revision_artifacts":
        await store.delete_revision_artifacts(
            DeletionTarget("doc-a", REVISION, (), (normalized_locator,))
        )
    elif operation == "scavenge_staging":
        await store.scavenge_staging(
            now=datetime.now(timezone.utc),
            visible_staging_keys=frozenset(),
        )
    elif operation == "container_properties":
        await store.container_properties(ORIGINALS_CONTAINER)
    elif operation == "close":
        await store.close()
    elif operation == "aclose":
        await store.aclose()
    else:
        raise AssertionError(f"unhandled public Blob operation: {operation}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    (
        "ensure_containers",
        "stage_original",
        "commit_original",
        "recover_original",
        "discard_staged",
        "discard_staging",
        "read_original",
        "write_normalized",
        "read_normalized",
        "write_chunks",
        "read_chunks",
        "write_embeddings",
        "read_embeddings",
        "delete_revision_artifacts",
        "scavenge_staging",
        "container_properties",
        "close",
        "aclose",
    ),
)
async def test_every_public_blob_operation_redacts_raw_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    store = _store_with_provider_failure(monkeypatch)

    with pytest.raises(ArtifactUnavailable) as caught:
        await _exercise_public_blob_operation(store, operation)

    assert isinstance(caught.value, ArtifactProviderUnavailable)
    assert not isinstance(caught.value, ArtifactIntegrityFailure)
    assert not isinstance(caught.value, ServiceRequestError)
    assert "secret" not in str(caught.value)
    assert "secret" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_site",
    ("blob_factory", "container_factory", "list_factory", "iterator_factory"),
)
async def test_synchronous_sdk_factory_failures_are_provider_neutral_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
    failure_site: str,
) -> None:
    class Container:
        def list_blobs(self, **_kwargs: object):  # type: ignore[no-untyped-def]
            if failure_site == "list_factory":
                raise ServiceRequestError("azure account-key=sync-secret")

            class Pages:
                def __aiter__(self):  # type: ignore[no-untyped-def]
                    if failure_site == "iterator_factory":
                        raise ServiceRequestError("azure account-key=sync-secret")
                    return self

                async def __anext__(self) -> None:
                    raise StopAsyncIteration

            return Pages()

    class Service(_ServiceDouble):
        def get_blob_client(self, _container: str, _name: str) -> _BlobDouble:
            if failure_site == "blob_factory":
                raise ServiceRequestError("azure account-key=sync-secret")
            return self.blob

        def get_container_client(self, _name: str) -> Container:
            if failure_site == "container_factory":
                raise ServiceRequestError("azure account-key=sync-secret")
            return Container()

    service = Service(_BlobDouble())
    monkeypatch.setattr(
        "tap.modules.knowledge.adapters.blob_artifacts.BlobServiceClient.from_connection_string",
        lambda *args, **kwargs: service,
    )
    store = AzureBlobArtifactStore(
        AzureBlobArtifactConfig(connection_string=SecretStr("UseDevelopmentStorage=true"))
    )

    with pytest.raises(ArtifactProviderUnavailable) as caught:
        if failure_site == "blob_factory":
            await store.discard_staging("staging/sync-failure")
        else:
            await store.scavenge_staging(
                now=datetime.now(timezone.utc),
                visible_staging_keys=frozenset(),
            )

    assert "sync-secret" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_error", (ValueError, TypeError))
async def test_provider_value_and_type_errors_cannot_escape_as_argument_validation(
    monkeypatch: pytest.MonkeyPatch,
    provider_error: type[Exception],
) -> None:
    class Service(_ServiceDouble):
        async def create_container(self, *_args: object, **_kwargs: object) -> None:
            raise provider_error("azure provider-secret")

    service = Service(_BlobDouble())
    monkeypatch.setattr(
        "tap.modules.knowledge.adapters.blob_artifacts.BlobServiceClient.from_connection_string",
        lambda *args, **kwargs: service,
    )
    store = AzureBlobArtifactStore(
        AzureBlobArtifactConfig(connection_string=SecretStr("UseDevelopmentStorage=true"))
    )

    with pytest.raises(ArtifactProviderUnavailable) as caught:
        await store.ensure_containers()

    assert "provider-secret" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("commit", "recover"))
async def test_promotion_malformed_staging_metadata_is_integrity_not_outage(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    store = _store_with_double(monkeypatch, _BlobDouble())
    staged = StagedOriginal(
        staging_key="staging/malformed-metadata",
        filename="policy.md",
        media_type="text/markdown",
        size=3,
        source_content_hash=canonical_sha256(b"abc"),
    )
    monkeypatch.setattr(
        store,
        "_staging_properties",
        lambda _key: _async_value(SimpleNamespace(size=3, metadata={"size": "3"})),
    )

    with pytest.raises(ArtifactIntegrityFailure) as caught:
        if operation == "commit":
            await store.commit_original(staged, REVISION)
        else:
            await store.recover_original(staged.staging_key, REVISION)

    assert isinstance(caught.value, ArtifactIntegrityError)
    assert not isinstance(caught.value, ArtifactUnavailable)


@pytest.mark.asyncio
async def test_public_commit_uses_one_total_deadline_for_permanently_pending_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeout = 0.04
    store = _store_with_double(monkeypatch, _BlobDouble(), timeout=timeout)
    source = object()

    class PendingDestination:
        metadata: dict[str, str] = {}

        async def exists(self) -> bool:
            return False

        async def start_copy_from_url(self, _url: str, **kwargs: object) -> dict[str, str]:
            self.metadata = dict(kwargs["metadata"])  # type: ignore[arg-type]
            return {"copy_id": "copy-pending"}

        async def get_blob_properties(self):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                size=0,
                metadata=self.metadata,
                copy=SimpleNamespace(status="pending", id="copy-pending"),
            )

    destination = PendingDestination()
    staged = StagedOriginal(
        staging_key="staging/permanent-pending",
        filename="policy.md",
        media_type="text/markdown",
        size=3,
        source_content_hash=canonical_sha256(b"abc"),
    )
    monkeypatch.setattr(
        store,
        "_blob",
        lambda _container, name: source if name == staged.staging_key else destination,
    )
    monkeypatch.setattr(
        store,
        "_staging_properties",
        lambda _key: _async_value(
            SimpleNamespace(
                size=3,
                metadata={
                    "blobsha256": staged.source_content_hash.removeprefix("sha256:"),
                    "size": "3",
                },
            )
        ),
    )
    monkeypatch.setattr(store, "_download", lambda _blob: _async_value(b"abc"))
    monkeypatch.setattr(store, "_source_copy_url", lambda _source: "https://copy.invalid")

    started = asyncio.get_running_loop().time()
    with pytest.raises(ArtifactUnavailable):
        await store.commit_original(staged, REVISION)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed <= timeout + 0.02


@pytest.mark.asyncio
async def test_recovery_child_settlement_cannot_add_a_second_grace_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store_with_double(monkeypatch, _BlobDouble(), timeout=0.03)
    recovery_child_release = asyncio.Event()
    settlement_observations: list[tuple[asyncio.Future[object], float]] = []
    wait_terminal = blob_artifacts._wait_terminal  # pyright: ignore[reportPrivateUsage]
    active_settlement_deadline = blob_artifacts._ACTIVE_COPY_SETTLEMENT_DEADLINE  # pyright: ignore[reportPrivateUsage]

    async def observe_wait_terminal(
        task: asyncio.Future[object],
        *,
        timeout_seconds: float,
    ) -> None:
        settlement_observations.append((task, timeout_seconds))
        await wait_terminal(task, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(blob_artifacts, "_wait_terminal", observe_wait_terminal)

    async def cancellation_resistant_child() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await recovery_child_release.wait()
            raise

    try:
        with pytest.raises(ArtifactProviderUnavailable):
            await store._bounded(cancellation_resistant_child(), timeout_seconds=0.0)

        settlement_token = active_settlement_deadline.set(asyncio.get_running_loop().time())
        try:
            with pytest.raises(ArtifactProviderUnavailable):
                await store._bounded(cancellation_resistant_child(), timeout_seconds=0.0)
        finally:
            active_settlement_deadline.reset(settlement_token)

        assert len(settlement_observations) == 2
        _primary_task, primary_timeout = settlement_observations[0]
        recovery_task, recovery_timeout = settlement_observations[-1]
        assert primary_timeout > 0.0
        assert recovery_timeout == 0.0
        assert isinstance(recovery_task, asyncio.Task)
        assert not recovery_task.done()
        assert recovery_task.cancelling() > 0
    finally:
        recovery_child_release.set()
        await asyncio.gather(
            *(task for task, _timeout in settlement_observations),
            return_exceptions=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "expected_error"),
    (
        ("stage", ValueError),
        ("commit", TypeError),
        ("delete", TypeError),
        ("scavenge", ValueError),
        ("container", ValueError),
    ),
)
async def test_public_blob_argument_validation_precedes_provider_translation(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    expected_error: type[Exception],
) -> None:
    store = _store_with_double(monkeypatch, _BlobDouble())

    with pytest.raises(expected_error):
        if operation == "stage":
            await store.stage_original(_Upload((b"abc",)), max_bytes=0)
        elif operation == "commit":
            await store.commit_original(object(), REVISION)  # type: ignore[arg-type]
        elif operation == "delete":
            await store.delete_revision_artifacts(object())  # type: ignore[arg-type]
        elif operation == "scavenge":
            await store.scavenge_staging(
                now=datetime.now(timezone.utc),
                visible_staging_keys=frozenset(),
                limit=0,
            )
        else:
            await store.container_properties("outside")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "durable_fact",
    ("staging_key", "locator", "revision_id", "source_content_hash"),
)
async def test_malformed_durable_blob_facts_are_integrity_not_caller_or_outage(
    monkeypatch: pytest.MonkeyPatch,
    durable_fact: str,
) -> None:
    """Repository identities are corruption facts, never public argument/provider errors."""
    store = _store_with_double(monkeypatch, _BlobDouble())
    chunks = chunk_artifact()
    embeddings = EmbeddingArtifact(
        "model",
        3,
        ((0.1, 0.2, 0.3),),
        (str(chunks[0].chunk_id),),
    )

    with pytest.raises(ArtifactIntegrityFailure) as caught:
        if durable_fact == "staging_key":
            await store.recover_original("../bad-staging", REVISION)
        elif durable_fact == "locator":
            await store.read_original("not-a-durable-locator")  # type: ignore[arg-type]
        elif durable_fact == "revision_id":
            await store.write_normalized("../bad-revision", normalized_artifact())
        else:
            await store.write_embeddings(
                REVISION,
                embeddings,
                source_content_hash="not-a-source-hash",
            )

    assert isinstance(caught.value, ArtifactIntegrityError)
    assert not isinstance(caught.value, (ArtifactProviderUnavailable, TypeError, ValueError))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "item",
    (
        SimpleNamespace(name=None, metadata={}),
        SimpleNamespace(name="staging/malformed", metadata=None),
    ),
)
async def test_scavenger_malformed_provider_item_is_integrity_not_outage(
    monkeypatch: pytest.MonkeyPatch,
    item: object,
) -> None:
    class Pages:
        def __init__(self) -> None:
            self._yielded = False

        def __aiter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __anext__(self):  # type: ignore[no-untyped-def]
            if self._yielded:
                raise StopAsyncIteration
            self._yielded = True
            return item

    class Container:
        def list_blobs(self, **_kwargs: object) -> Pages:
            return Pages()

    store = _store_with_double(monkeypatch, _BlobDouble())
    monkeypatch.setattr(
        store._service,
        "get_container_client",
        lambda _name: Container(),
        raising=False,
    )

    with pytest.raises(ArtifactIntegrityFailure) as caught:
        await store.scavenge_staging(
            now=datetime.now(timezone.utc),
            visible_staging_keys=frozenset(),
        )

    assert isinstance(caught.value, ArtifactIntegrityError)
    assert not isinstance(caught.value, ArtifactUnavailable)


@pytest.mark.asyncio
async def test_public_recovery_does_not_refresh_the_promotion_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeout = 0.04
    store = _store_with_double(monkeypatch, _BlobDouble(), timeout=timeout)

    async def slow_properties(_key: str) -> SimpleNamespace:
        await asyncio.sleep(0.03)
        return SimpleNamespace(
            size=3,
            metadata={
                "blobsha256": canonical_sha256(b"abc").removeprefix("sha256:"),
                "size": "3",
            },
        )

    monkeypatch.setattr(store, "_staging_properties", slow_properties)
    monkeypatch.setattr(store, "_download", lambda _blob: _async_value(b"abc"))

    started = asyncio.get_running_loop().time()
    with pytest.raises(ArtifactUnavailable):
        await store.recover_original("staging/slow-recovery", REVISION)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed <= timeout + 0.02


@pytest.mark.asyncio
@pytest.mark.parametrize("read_kind", ("normalized", "chunks"))
async def test_artifact_reads_translate_missing_blob_to_neutral_integrity_failure(
    monkeypatch: pytest.MonkeyPatch, read_kind: str
) -> None:
    class MissingBlob(_BlobDouble):
        async def get_blob_properties(self):  # type: ignore[no-untyped-def]
            raise ResourceNotFoundError(message="account/container/provider detail")

    store = _store_with_double(monkeypatch, MissingBlob())
    locator = ArtifactLocator(
        f"tapper-artifacts/revisions/{REVISION}/"
        + ("normalized-v1.json" if read_kind == "normalized" else "chunks-v1.jsonl.gz")
    )

    with pytest.raises(ArtifactIntegrityFailure) as caught:
        if read_kind == "normalized":
            await store.read_normalized(locator)
        else:
            await store.read_chunks(locator)

    assert isinstance(caught.value, ArtifactIntegrityError)
    assert not isinstance(caught.value, ResourceNotFoundError)


@pytest.mark.asyncio
async def test_artifact_reads_translate_provider_failure_to_neutral_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailedBlob(_BlobDouble):
        async def get_blob_properties(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("azure endpoint credential=secret")

    store = _store_with_double(monkeypatch, FailedBlob())
    locator = ArtifactLocator(f"tapper-artifacts/revisions/{REVISION}/normalized-v1.json")

    with pytest.raises(ArtifactUnavailable) as caught:
        await store.read_normalized(locator)

    assert isinstance(caught.value, ArtifactProviderUnavailable)
    assert not isinstance(caught.value, ArtifactIntegrityFailure)
    assert "secret" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_error", (ValueError, TypeError))
async def test_artifact_read_provider_value_and_type_errors_are_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    provider_error: type[Exception],
) -> None:
    class FailedBlob(_BlobDouble):
        async def get_blob_properties(self) -> None:
            raise provider_error("azure read-secret")

    store = _store_with_double(monkeypatch, FailedBlob())

    with pytest.raises(ArtifactProviderUnavailable) as caught:
        await store.read_original(
            ArtifactLocator(
                f"{ORIGINALS_CONTAINER}/revisions/{REVISION}/{SOURCE_HASH.removeprefix('sha256:')}"
            )
        )

    assert "read-secret" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "properties",
    (
        SimpleNamespace(metadata=None, size=3),
        SimpleNamespace(metadata={"blobsha256": "a" * 64, "size": "3"}),
    ),
)
async def test_artifact_read_malformed_provider_properties_are_integrity(
    monkeypatch: pytest.MonkeyPatch,
    properties: object,
) -> None:
    class Stream:
        async def readall(self) -> bytes:
            return b"abc"

    class Blob(_BlobDouble):
        async def get_blob_properties(self) -> object:
            return properties

        async def download_blob(self, **_kwargs: object) -> Stream:
            return Stream()

    store = _store_with_double(monkeypatch, Blob())

    with pytest.raises(ArtifactIntegrityError):
        await store.read_original(
            ArtifactLocator(
                f"{ORIGINALS_CONTAINER}/revisions/{REVISION}/{SOURCE_HASH.removeprefix('sha256:')}"
            )
        )


@pytest.mark.asyncio
async def test_copy_terminal_malformed_provider_properties_are_integrity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store_with_double(monkeypatch, _BlobDouble())

    class Destination:
        async def get_blob_properties(self) -> object:
            return SimpleNamespace(metadata=None, copy=None)

    with pytest.raises(ArtifactIntegrityError):
        await store._wait_copy_terminal(  # pyright: ignore[reportPrivateUsage]
            Destination(),  # type: ignore[arg-type]
            "f" * 64,
            None,
        )


@pytest.mark.asyncio
async def test_closed_artifact_store_is_unavailable_not_stale_integrity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store_with_double(monkeypatch, _BlobDouble())
    locator = ArtifactLocator(f"tapper-artifacts/revisions/{REVISION}/normalized-v1.json")
    await store.close()

    with pytest.raises(ArtifactUnavailable) as caught:
        await store.read_normalized(locator)

    assert isinstance(caught.value, ArtifactProviderUnavailable)
    assert not isinstance(caught.value, ArtifactIntegrityFailure)


@pytest.mark.asyncio
async def test_stage_timeout_remains_provider_unavailable_after_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store_with_double(monkeypatch, _BlobDouble(slow_upload=True), timeout=0.01)

    with pytest.raises(ArtifactUnavailable) as caught:
        await store.stage_original(_Upload((b"abc",)), max_bytes=3)

    assert isinstance(caught.value, ArtifactProviderUnavailable)
    assert not isinstance(caught.value, ArtifactIntegrityFailure)


@pytest.mark.asyncio
async def test_stage_original_streams_from_bounded_spool_without_materializing_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full bytearray copy would violate the 25 MiB streaming upload boundary."""
    blob = _BlobDouble()
    store = _store_with_double(monkeypatch, blob)

    staged = await store.stage_original(_Upload((b"abc", b"def")), max_bytes=6)

    assert blob.upload_streamed
    assert blob.uploaded == b"abcdef"
    assert staged.size == 6
    assert staged.source_content_hash == canonical_sha256(b"abcdef")


@pytest.mark.asyncio
async def test_stage_original_cleanup_deadline_cancels_and_settles_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raw cleanup awaits can outlive Task 4 cancellation and leave an SDK call running."""
    blob = _BlobDouble(fail_upload=True, slow_delete=True)
    store = _store_with_double(monkeypatch, blob, timeout=0.01)

    with pytest.raises(ArtifactUnavailable):
        await store.stage_original(_Upload((b"abc",)), max_bytes=3)

    assert blob.delete_cancelled


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_failure", ("sync", "async", "hang"))
async def test_stage_cleanup_cannot_replace_the_first_caller_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    cleanup_failure: str,
) -> None:
    """Cleanup construction, await failure, and timeout must retain one cancellation object."""

    class Blob:
        def __init__(self) -> None:
            self.upload_started = asyncio.Event()
            self.cleanup_started = asyncio.Event()

        async def upload_blob(self, *_args: object, **_kwargs: object) -> None:
            self.upload_started.set()
            await asyncio.Event().wait()

        def delete_blob(self, **_kwargs: object):  # type: ignore[no-untyped-def]
            if cleanup_failure == "sync":
                raise ServiceRequestError("azure AccountKey=cleanup-secret")

            async def cleanup() -> None:
                self.cleanup_started.set()
                if cleanup_failure == "async":
                    raise ServiceRequestError("azure AccountKey=cleanup-secret")
                await asyncio.Event().wait()

            return cleanup()

    blob = Blob()
    store = _store_with_double(monkeypatch, blob, timeout=0.01)  # type: ignore[arg-type]
    real_bounded = store._bounded  # pyright: ignore[reportPrivateUsage]
    observed: list[asyncio.CancelledError] = []

    async def observe_primary(operation, *, timeout_seconds=None):  # type: ignore[no-untyped-def]
        try:
            return await real_bounded(operation, timeout_seconds=timeout_seconds)
        except asyncio.CancelledError as error:
            observed.append(error)
            raise

    monkeypatch.setattr(store, "_bounded", observe_primary)
    task = asyncio.create_task(store.stage_original(_Upload((b"abc",)), max_bytes=3))
    await blob.upload_started.wait()
    task.cancel("caller-cancel")
    if cleanup_failure == "hang":
        await blob.cleanup_started.wait()
        task.cancel("later-cancel")

    with pytest.raises(asyncio.CancelledError) as caught:
        await task

    assert observed
    assert caught.value is observed[0]
    assert caught.value.args == ("caller-cancel",)
    if cleanup_failure == "hang":
        assert observed[1].args == ("later-cancel",)


@pytest.mark.asyncio
async def test_provider_timeout_with_cancelled_child_keeps_safe_deadline_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled SDK child must not overwrite the provider-neutral timeout verdict."""
    store = _store_with_double(monkeypatch, _BlobDouble(), timeout=0.01)

    async def never() -> None:
        await asyncio.Event().wait()

    with pytest.raises(ArtifactUnavailable, match="deadline") as caught:
        await store._bounded(never())

    assert not isinstance(caught.value, ArtifactIntegrityFailure)


@pytest.mark.asyncio
async def test_provider_deadline_does_not_wait_unbounded_for_cancellation_resistant_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeout = 0.02
    store = _store_with_double(monkeypatch, _BlobDouble(), timeout=timeout)
    child_settled = asyncio.Event()

    async def cancellation_resistant() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0.08)
        finally:
            child_settled.set()

    started = asyncio.get_running_loop().time()
    with pytest.raises(ArtifactProviderUnavailable):
        await store._bounded(cancellation_resistant())
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed <= timeout + 0.02
    await asyncio.wait_for(child_settled.wait(), timeout=0.2)


@pytest.mark.asyncio
async def test_caller_cancellation_waits_for_provider_child_then_preserves_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller cancellation must remain distinct after the SDK child reaches terminal state."""
    store = _store_with_double(monkeypatch, _BlobDouble(), timeout=1)
    started = asyncio.Event()
    settled = asyncio.Event()

    async def blocked() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            settled.set()

    task = asyncio.create_task(store._bounded(blocked()))
    await started.wait()
    task.cancel("caller-cancelled")

    with pytest.raises(asyncio.CancelledError):
        await task
    assert settled.is_set()


@pytest.mark.asyncio
async def test_spontaneously_cancelled_provider_child_is_not_caller_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An SDK child cancelling itself is provider failure, not authority to cancel its caller."""
    store = _store_with_double(monkeypatch, _BlobDouble(), timeout=1)

    async def provider_cancelled() -> None:
        raise asyncio.CancelledError("provider-internal")

    with pytest.raises(ArtifactUnavailable):
        await store._bounded(provider_cancelled())


@pytest.mark.asyncio
@pytest.mark.parametrize("race_error", [ResourceExistsError, ResourceModifiedError])
async def test_original_promotion_race_reuses_exact_existing_blob_without_deleting_it(
    monkeypatch: pytest.MonkeyPatch,
    race_error: type[Exception],
) -> None:
    """A create race must compare immutable bytes, never delete the winner's final Blob."""
    store = _store_with_double(monkeypatch, _BlobDouble())
    source = object()
    copy_conditions: dict[str, object] = {}

    class Destination:
        async def exists(self) -> bool:
            return False

        async def start_copy_from_url(  # type: ignore[no-untyped-def]
            self, url: str, **kwargs: object
        ):
            del url
            copy_conditions.update(kwargs)
            raise race_error("raced")

        async def get_blob_properties(self):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                size=3,
                metadata={
                    "blobsha256": canonical_sha256(b"abc").removeprefix("sha256:"),
                    "size": "3",
                },
                copy=SimpleNamespace(status="success", id="winner-copy"),
            )

    destination = Destination()
    deleted: list[object] = []
    staged = StagedOriginal(
        staging_key="staging/task5-race",
        filename="policy.md",
        media_type="text/markdown",
        size=3,
        source_content_hash=canonical_sha256(b"abc"),
    )
    monkeypatch.setattr(
        store,
        "_blob",
        lambda _container, name: source if name == staged.staging_key else destination,
    )

    async def properties(_key: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            size=3,
            metadata={
                "blobsha256": canonical_sha256(b"abc").removeprefix("sha256:"),
                "size": "3",
            },
        )

    async def download(_blob):  # type: ignore[no-untyped-def]
        return b"abc"

    async def delete(blob):  # type: ignore[no-untyped-def]
        deleted.append(blob)

    monkeypatch.setattr(store, "_staging_properties", properties)
    monkeypatch.setattr(store, "_download", download)
    monkeypatch.setattr(store, "_download_verified", download)
    monkeypatch.setattr(store, "_source_copy_url", lambda _source: "https://copy.invalid")
    monkeypatch.setattr(store, "_delete_if_exists", delete)

    locator = await store.commit_original(staged, "rev_task5_race")

    assert str(locator).startswith("tapper-originals/revisions/rev_task5_race/")
    assert deleted == [source]
    assert "etag" not in copy_conditions
    assert getattr(copy_conditions["match_condition"], "name", None) == "IfMissing"


@pytest.mark.asyncio
async def test_uncertain_copy_without_owned_copy_id_never_deletes_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the returned copy ID the adapter cannot prove ownership of a final Blob."""
    store = _store_with_double(monkeypatch, _BlobDouble())
    deleted: list[object] = []
    destination = object()

    async def delete(blob):  # type: ignore[no-untyped-def]
        deleted.append(blob)

    monkeypatch.setattr(store, "_delete_if_exists", delete)

    await store._abort_and_delete(destination, "f" * 64, None)  # type: ignore[arg-type]

    assert deleted == []


@pytest.mark.asyncio
async def test_uncertain_copy_response_recovers_owned_success_from_destination_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lost start response after server success must converge by durable ownership metadata."""
    store = _store_with_double(monkeypatch, _BlobDouble())
    source = object()
    deleted: list[object] = []

    class Destination:
        metadata: dict[str, str] = {}

        async def exists(self) -> bool:
            return False

        async def start_copy_from_url(self, url: str, **kwargs: object) -> None:
            del url
            self.metadata = dict(kwargs["metadata"])  # type: ignore[arg-type]
            raise RuntimeError("response lost after copy acceptance")

        async def get_blob_properties(self):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                size=3,
                metadata=self.metadata,
                copy=SimpleNamespace(status="success", id="copy-owned"),
            )

    destination = Destination()
    staged = StagedOriginal(
        staging_key="staging/task5-uncertain-success",
        filename="policy.md",
        media_type="text/markdown",
        size=3,
        source_content_hash=canonical_sha256(b"abc"),
    )
    monkeypatch.setattr(
        store,
        "_blob",
        lambda _container, name: source if name == staged.staging_key else destination,
    )
    monkeypatch.setattr(
        store,
        "_staging_properties",
        lambda _key: _async_value(
            SimpleNamespace(
                size=3,
                metadata={
                    "blobsha256": staged.source_content_hash.removeprefix("sha256:"),
                    "size": "3",
                },
            )
        ),
    )
    monkeypatch.setattr(store, "_download", lambda _blob: _async_value(b"abc"))
    monkeypatch.setattr(store, "_download_verified", lambda _blob: _async_value(b"abc"))
    monkeypatch.setattr(store, "_source_copy_url", lambda _source: "https://copy.invalid")

    async def delete(blob):  # type: ignore[no-untyped-def]
        deleted.append(blob)

    monkeypatch.setattr(store, "_delete_if_exists", delete)

    locator = await store.commit_original(staged, "rev_uncertain_success")

    assert str(locator).startswith("tapper-originals/revisions/rev_uncertain_success/")
    assert destination.metadata["copyowner"]
    assert destination.metadata["blobsha256"] == staged.source_content_hash.removeprefix("sha256:")
    assert deleted == [source]


@pytest.mark.asyncio
async def test_copy_recovery_outage_is_not_downgraded_to_the_original_integrity_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store_with_double(monkeypatch, _BlobDouble())
    source = object()

    class Destination:
        async def exists(self) -> bool:
            return False

        async def start_copy_from_url(self, url: str, **kwargs: object) -> None:
            del url, kwargs
            raise ArtifactIntegrityError("malformed copy response")

    destination = Destination()
    staged = StagedOriginal(
        staging_key="staging/task6-recovery-outage",
        filename="policy.md",
        media_type="text/markdown",
        size=3,
        source_content_hash=canonical_sha256(b"abc"),
    )
    monkeypatch.setattr(
        store,
        "_blob",
        lambda _container, name: source if name == staged.staging_key else destination,
    )
    monkeypatch.setattr(
        store,
        "_staging_properties",
        lambda _key: _async_value(
            SimpleNamespace(
                size=3,
                metadata={
                    "blobsha256": staged.source_content_hash.removeprefix("sha256:"),
                    "size": "3",
                },
            )
        ),
    )
    monkeypatch.setattr(store, "_download", lambda _blob: _async_value(b"abc"))
    monkeypatch.setattr(store, "_source_copy_url", lambda _source: "https://copy.invalid")

    async def recovery_unavailable(*_args: object, **_kwargs: object) -> bool:
        raise ArtifactProviderUnavailable("provider offline during recovery")

    monkeypatch.setattr(store, "_resolve_copy_destination", recovery_unavailable)

    with pytest.raises(ArtifactUnavailable) as caught:
        await store.commit_original(staged, "rev_task6_recovery_outage")

    assert isinstance(caught.value, ArtifactProviderUnavailable)
    assert not isinstance(caught.value, ArtifactIntegrityFailure)


@pytest.mark.asyncio
async def test_copy_recovery_integrity_verdict_is_not_discarded_for_original_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store_with_double(monkeypatch, _BlobDouble())
    source = object()

    class Destination:
        async def exists(self) -> bool:
            return False

        async def start_copy_from_url(self, _url: str, **_kwargs: object) -> None:
            raise ArtifactProviderUnavailable("start response unavailable")

    staged = StagedOriginal(
        staging_key="staging/recovery-integrity",
        filename="policy.md",
        media_type="text/markdown",
        size=3,
        source_content_hash=canonical_sha256(b"abc"),
    )
    monkeypatch.setattr(
        store,
        "_blob",
        lambda _container, name: source if name == staged.staging_key else Destination(),
    )
    monkeypatch.setattr(
        store,
        "_staging_properties",
        lambda _key: _async_value(
            SimpleNamespace(
                size=3,
                metadata={
                    "blobsha256": staged.source_content_hash.removeprefix("sha256:"),
                    "size": "3",
                },
            )
        ),
    )
    monkeypatch.setattr(store, "_download", lambda _blob: _async_value(b"abc"))
    monkeypatch.setattr(store, "_source_copy_url", lambda _source: "https://copy.invalid")

    async def recovery_integrity(*_args: object, **_kwargs: object) -> bool:
        raise ArtifactIntegrityError("durable destination metadata is malformed")

    monkeypatch.setattr(store, "_resolve_copy_destination", recovery_integrity)

    with pytest.raises(ArtifactIntegrityError):
        await store.commit_original(staged, REVISION)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("commit", "recover"))
async def test_promotion_preflight_translates_missing_staging_blob_to_integrity(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    store = _store_with_double(monkeypatch, _BlobDouble())
    staged = StagedOriginal(
        staging_key="staging/task6-missing-preflight",
        filename="policy.md",
        media_type="text/markdown",
        size=3,
        source_content_hash=canonical_sha256(b"abc"),
    )

    async def missing(_key: str) -> None:
        raise ResourceNotFoundError(message="azure account-key=secret")

    monkeypatch.setattr(store, "_staging_properties", missing)

    with pytest.raises(ArtifactIntegrityFailure) as caught:
        if operation == "commit":
            await store.commit_original(staged, "rev_task6_missing_preflight")
        else:
            await store.recover_original(staged.staging_key, "rev_task6_missing_preflight")

    assert isinstance(caught.value, ArtifactIntegrityError)
    assert not isinstance(caught.value, ArtifactUnavailable)
    assert "secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_promotion_preflight_translates_destination_outage_to_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store_with_double(monkeypatch, _BlobDouble())
    source = object()

    class Destination:
        async def exists(self) -> bool:
            raise ServiceRequestError("azure account-key=secret")

    staged = StagedOriginal(
        staging_key="staging/task6-destination-outage",
        filename="policy.md",
        media_type="text/markdown",
        size=3,
        source_content_hash=canonical_sha256(b"abc"),
    )
    monkeypatch.setattr(
        store,
        "_blob",
        lambda _container, name: source if name == staged.staging_key else Destination(),
    )
    monkeypatch.setattr(
        store,
        "_staging_properties",
        lambda _key: _async_value(
            SimpleNamespace(
                size=3,
                metadata={
                    "blobsha256": staged.source_content_hash.removeprefix("sha256:"),
                    "size": "3",
                },
            )
        ),
    )
    monkeypatch.setattr(store, "_download", lambda _blob: _async_value(b"abc"))

    with pytest.raises(ArtifactUnavailable) as caught:
        await store.commit_original(staged, "rev_task6_destination_outage")

    assert isinstance(caught.value, ArtifactProviderUnavailable)
    assert not isinstance(caught.value, ArtifactIntegrityFailure)
    assert "secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_copy_terminal_client_deadline_is_unavailable_not_integrity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store_with_double(monkeypatch, _BlobDouble(), timeout=0.01)
    owner_token = "f" * 64

    class PendingDestination:
        async def get_blob_properties(self):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                metadata={"copyowner": owner_token},
                copy=SimpleNamespace(status="pending", id="copy-pending"),
            )

    with pytest.raises(ArtifactUnavailable) as caught:
        await store._wait_copy_terminal(  # pyright: ignore[reportPrivateUsage]
            PendingDestination(),  # type: ignore[arg-type]
            owner_token,
            "copy-pending",
        )

    assert isinstance(caught.value, ArtifactProviderUnavailable)
    assert not isinstance(caught.value, ArtifactIntegrityFailure)


@pytest.mark.asyncio
async def test_cancellation_after_uncertain_copy_acceptance_aborts_and_deletes_owned_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller cancellation must settle an accepted owned copy even when start returned no ID."""
    store = _store_with_double(monkeypatch, _BlobDouble())
    source = object()
    accepted = asyncio.Event()
    deleted: list[object] = []

    class Destination:
        metadata: dict[str, str] = {}
        status = "pending"
        aborted = False

        async def exists(self) -> bool:
            return False

        async def start_copy_from_url(self, url: str, **kwargs: object) -> None:
            del url
            accepted.set()
            self.metadata = dict(kwargs.get("metadata", {}))  # type: ignore[arg-type]
            await asyncio.Event().wait()

        async def get_blob_properties(self):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                size=0,
                metadata=self.metadata,
                copy=SimpleNamespace(status=self.status, id="copy-owned"),
            )

        async def abort_copy(self, copy_id: str) -> None:
            assert copy_id == "copy-owned"
            self.status = "aborted"
            self.aborted = True

    destination = Destination()
    staged = StagedOriginal(
        staging_key="staging/task5-uncertain-cancel",
        filename="policy.md",
        media_type="text/markdown",
        size=3,
        source_content_hash=canonical_sha256(b"abc"),
    )
    monkeypatch.setattr(
        store,
        "_blob",
        lambda _container, name: source if name == staged.staging_key else destination,
    )
    monkeypatch.setattr(
        store,
        "_staging_properties",
        lambda _key: _async_value(
            SimpleNamespace(
                size=3,
                metadata={
                    "blobsha256": staged.source_content_hash.removeprefix("sha256:"),
                    "size": "3",
                },
            )
        ),
    )
    monkeypatch.setattr(store, "_download", lambda _blob: _async_value(b"abc"))
    monkeypatch.setattr(store, "_source_copy_url", lambda _source: "https://copy.invalid")

    async def delete(blob):  # type: ignore[no-untyped-def]
        deleted.append(blob)

    monkeypatch.setattr(store, "_delete_if_exists", delete)
    task = asyncio.create_task(store.commit_original(staged, "rev_uncertain_cancel"))
    await accepted.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert destination.aborted
    assert deleted == [destination]


def _async_value(value):  # type: ignore[no-untyped-def]
    async def result():  # type: ignore[no-untyped-def]
        return value

    return result()


@pytest.mark.asyncio
async def test_delete_rejects_cross_revision_locator_before_provider_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A corrupted ledger locator must never authorize deleting another revision's Blob."""
    blob = _BlobDouble()
    store = _store_with_double(monkeypatch, blob)
    target = DeletionTarget(
        "doc_a",
        "rev_expected",
        (),
        (ArtifactLocator("tapper-artifacts/revisions/rev_other/normalized-v1.json"),),
    )

    with pytest.raises(ArtifactIntegrityError):
        await store.delete_revision_artifacts(target)

    assert blob.delete_calls == 0


@pytest.mark.asyncio
async def test_scavenger_list_page_uses_deadline_cancel_and_terminal_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Async paging is an SDK await and cannot bypass the common terminal deadline path."""

    class Pages:
        def __init__(self) -> None:
            self.cancelled = False

        def __aiter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __anext__(self):  # type: ignore[no-untyped-def]
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                self.cancelled = True
                raise StopAsyncIteration from None
            raise StopAsyncIteration

    pages = Pages()

    class Container:
        def list_blobs(self, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            return pages

    class Service(_ServiceDouble):
        def get_container_client(self, name: str) -> Container:
            del name
            return Container()

    service = Service(_BlobDouble())
    monkeypatch.setattr(
        "tap.modules.knowledge.adapters.blob_artifacts.BlobServiceClient.from_connection_string",
        lambda *args, **kwargs: service,
    )
    store = AzureBlobArtifactStore(
        AzureBlobArtifactConfig(
            connection_string=SecretStr("UseDevelopmentStorage=true"),
            operation_timeout_seconds=0.01,
        )
    )

    with pytest.raises(ArtifactUnavailable, match="deadline"):
        await store.scavenge_staging(
            now=datetime.now(timezone.utc),
            visible_staging_keys=frozenset(),
        )

    assert pages.cancelled


@pytest.mark.asyncio
async def test_scavenger_uses_one_absolute_deadline_across_all_deletes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Individually fast deletes must not reset and exceed the global scavenger budget."""
    cancelled: list[str] = []
    now = datetime.now(timezone.utc)
    items = iter(
        (
            SimpleNamespace(
                name=f"staging/slow-{index}",
                metadata={"stagedat": (now - timedelta(hours=25)).isoformat()},
            )
            for index in range(2)
        )
    )

    class Pages:
        def __aiter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __anext__(self):  # type: ignore[no-untyped-def]
            try:
                return next(items)
            except StopIteration:
                raise StopAsyncIteration from None

    class SlowDelete:
        def __init__(self, name: str) -> None:
            self.name = name

        async def delete_blob(self, **kwargs: object) -> None:
            del kwargs
            try:
                await asyncio.sleep(0.007)
            except asyncio.CancelledError:
                cancelled.append(self.name)
                raise

    class Container:
        def list_blobs(self, **kwargs: object) -> Pages:
            del kwargs
            return Pages()

        def get_blob_client(self, name: str) -> SlowDelete:
            return SlowDelete(name)

    class Service(_ServiceDouble):
        def get_container_client(self, name: str) -> Container:
            del name
            return Container()

    service = Service(_BlobDouble())
    monkeypatch.setattr(
        "tap.modules.knowledge.adapters.blob_artifacts.BlobServiceClient.from_connection_string",
        lambda *args, **kwargs: service,
    )
    store = AzureBlobArtifactStore(
        AzureBlobArtifactConfig(
            connection_string=SecretStr("UseDevelopmentStorage=true"),
            operation_timeout_seconds=0.01,
        )
    )

    with pytest.raises(ArtifactUnavailable, match="deadline"):
        await store.scavenge_staging(now=now, visible_staging_keys=frozenset(), limit=2)

    assert cancelled == ["staging/slow-1"]
