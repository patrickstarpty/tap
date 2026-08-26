"""Closed, deterministic, and sanitized Milvus experiment fixtures."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast, overload

from tap.modules.access.domain.policy import Classification

CONTENT_ANALYZER = {
    "tokenizer": {
        "type": "language_identifier",
        "identifier": "whatlang",
        "analyzers": {
            "default": {"tokenizer": "standard"},
            "English": {"type": "english"},
            "Mandarin": {"tokenizer": "jieba", "filter": ["cnalphanumonly"]},
        },
    }
}
BM25_FUNCTION = {
    "name": "content_bm25_v1",
    "function_type": "BM25",
    "input_field_names": ("content",),
    "output_field_names": ("bm25_sparse",),
}
INDEXES: dict[str, dict[str, object]] = {
    "dense_vector": {"index_type": "FLAT", "metric_type": "COSINE", "params": {}},
    "bm25_sparse": {
        "index_type": "SPARSE_INVERTED_INDEX",
        "metric_type": "BM25",
        "params": {
            "inverted_index_algo": "DAAT_MAXSCORE",
            "bm25_k1": 1.2,
            "bm25_b": 0.75,
        },
    },
    **{
        field: {"index_type": "INVERTED"}
        for field in (
            "tenant_id",
            "project_id",
            "allowed_group_ids",
            "classification_rank",
            "environment",
            "corpus_version",
            "deleted",
        )
    },
}

EXPECTED_SOURCE_IDS = frozenset(
    {
        "blob:fixture/payment/refund",
        "blob:fixture/payment/limit",
        "blob:fixture/payment/archive",
        "blob:fixture/payment/root",
        "blob:fixture/payment/card",
        "blob:fixture/payment/wire",
        "blob:fixture/release/rollback",
        "blob:fixture/release/canary",
        "blob:fixture/security/keys",
        "blob:fixture/other-project/budget",
        "blob:fixture/other-tenant/refund",
        "blob:fixture/public/support",
    }
)
EXPECTED_QUERY_CASE_IDS = frozenset(
    {
        "refund-allowed",
        "payment-global-allowed",
        "payment-wrong-group",
        "payment-wrong-project",
        "payment-wrong-tenant",
        "security-over-classification",
        "release-wrong-environment",
        "payment-subtree-card-only",
    }
)

_DOC_SCHEMA_VERSION = "doc-schema-v1"
_CORPUS_VERSION = "corpus-fixture-v1"
_EMBEDDING_MODEL_VERSION = "research-embedding-v1"
_VECTOR_DIMENSION = 1536
_PHYSICAL_COLLECTION = "kb_doc_v1_corpus_fixture_v1"
_ALIAS = "kb_doc_active"
_SOURCE_REVISION = "fixture-blob-v1"
_SAFE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,254}\Z")
_HASH_ID = re.compile(r"h_[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_METADATA_PREFIX = "tap-collection-metadata-v1:"
_METADATA_KEYS = frozenset(
    {
        "family",
        "schemaVersion",
        "schemaSha256",
        "corpusVersion",
        "embeddingModelVersion",
        "vectorDimension",
    }
)
_MANIFEST_KEYS = frozenset(
    {
        "schemaVersion",
        "schemaSha256",
        "corpusVersion",
        "embeddingModelVersion",
        "vectorDimension",
        "physicalCollection",
        "alias",
        "chunks",
    }
)
_CHUNK_KEYS = frozenset(
    {
        "chunkId",
        "logicalChunkId",
        "rootId",
        "parentId",
        "title",
        "content",
        "contentRole",
        "tenantId",
        "projectId",
        "allowedGroupIds",
        "classificationRank",
        "environment",
        "deleted",
        "sourceId",
        "sourceRevision",
        "sourceContentHash",
        "chunkContentHash",
        "anchorJson",
    }
)
_QUERY_DOCUMENT_KEYS = frozenset({"schemaVersion", "cases"})
_QUERY_KEYS = frozenset(
    {
        "caseId",
        "query",
        "tenantId",
        "projectId",
        "groupIds",
        "classificationCeiling",
        "environment",
        "expectedSourceIds",
    }
)
_ANCHOR_KEYS = frozenset({"type", "headingPath", "page", "bbox", "startOffset", "endOffset"})


@dataclass(frozen=True, slots=True)
class DocFixtureChunk:
    chunk_id: str
    logical_chunk_id: str
    root_id: str
    parent_id: str | None
    title: str | None
    content: str
    content_role: Literal["source"]
    tenant_id: str
    project_id: str
    allowed_group_ids: tuple[str, ...]
    classification_rank: int
    environment: str
    deleted: bool
    source_id: str
    source_revision: str
    source_content_hash: str
    chunk_content_hash: str
    anchor_json: str


@dataclass(frozen=True, slots=True)
class QueryCase:
    case_id: str
    query: str
    tenant_id: str
    project_id: str
    group_ids: tuple[str, ...]
    classification_ceiling: Classification
    environment: str | None
    expected_source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DocFixtureManifest:
    schema_version: str
    schema_sha256: str
    corpus_version: str
    embedding_model_version: str
    vector_dimension: int
    physical_collection: str
    alias: str
    chunks: tuple[DocFixtureChunk, ...]


def sha256_id(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).replace("\r\n", "\n")
    return "h_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def content_hash(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).replace("\r\n", "\n")
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def load_doc_fixture(path: Path) -> DocFixtureManifest:
    raw = _load_closed_json(path)
    value = _exact_mapping(raw, _MANIFEST_KEYS, "fixture manifest")
    raw_chunks = _sequence(value["chunks"], "fixture chunks")
    chunks = tuple(_load_chunk(item) for item in raw_chunks)
    manifest = DocFixtureManifest(
        schema_version=_string(value["schemaVersion"], "schemaVersion", maximum=256),
        schema_sha256=_digest(value["schemaSha256"], "schemaSha256"),
        corpus_version=_string(value["corpusVersion"], "corpusVersion", maximum=256),
        embedding_model_version=_string(
            value["embeddingModelVersion"],
            "embeddingModelVersion",
            maximum=256,
        ),
        vector_dimension=_strict_int(value["vectorDimension"], "vectorDimension", 1, 4096),
        physical_collection=_string(
            value["physicalCollection"],
            "physicalCollection",
            maximum=255,
        ),
        alias=_string(value["alias"], "alias", maximum=255),
        chunks=chunks,
    )
    _validate_manifest(manifest)
    return manifest


def load_query_cases(path: Path) -> tuple[QueryCase, ...]:
    raw = _exact_mapping(_load_closed_json(path), _QUERY_DOCUMENT_KEYS, "query fixture")
    if raw["schemaVersion"] != "query-cases-v1":
        raise ValueError("query fixture schema version is unsupported")
    cases = tuple(_load_query_case(item) for item in _sequence(raw["cases"], "query cases"))
    if len(cases) != 8 or {case.case_id for case in cases} != EXPECTED_QUERY_CASE_IDS:
        raise ValueError("query fixture case identities are not exact")
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("query fixture case identities are duplicated")
    return cases


def build_collection_schema(manifest: DocFixtureManifest) -> dict[str, object]:
    _validate_manifest(manifest)
    return {
        "auto_id": False,
        "enable_dynamic_field": False,
        "enable_namespace": False,
        "consistency_level": "Strong",
        "description": collection_description(manifest),
        "vector_dimension": manifest.vector_dimension,
        "fields": tuple(copy.deepcopy(_canonical_fields())),
        "functions": (copy.deepcopy(BM25_FUNCTION),),
        "indexes": copy.deepcopy(INDEXES),
    }


def schema_sha256() -> str:
    canonical = {
        "consistency_level": "Strong",
        "fields": _canonical_fields(),
        "functions": [
            {
                "input_field_names": ["content"],
                "name": BM25_FUNCTION["name"],
                "output_field_names": ["bm25_sparse"],
                "params": {},
                "type": 1,
            }
        ],
        "indexes": _canonical_indexes(),
    }
    return _canonical_digest(canonical)


def collection_description(manifest: DocFixtureManifest) -> str:
    _validate_manifest(manifest)
    metadata = {
        "family": "doc",
        "schemaVersion": manifest.schema_version,
        "schemaSha256": manifest.schema_sha256,
        "corpusVersion": manifest.corpus_version,
        "embeddingModelVersion": manifest.embedding_model_version,
        "vectorDimension": manifest.vector_dimension,
    }
    return _METADATA_PREFIX + json.dumps(metadata, sort_keys=True, separators=(",", ":"))


def manifest_sha256(manifest: DocFixtureManifest) -> str:
    _validate_manifest(manifest)
    chunks = []
    for chunk in manifest.chunks:
        value = asdict(chunk)
        value["allowed_group_ids"] = list(chunk.allowed_group_ids)
        chunks.append(value)
    return _canonical_digest(
        {
            "schema_version": manifest.schema_version,
            "schema_sha256": manifest.schema_sha256,
            "corpus_version": manifest.corpus_version,
            "embedding_model_version": manifest.embedding_model_version,
            "vector_dimension": manifest.vector_dimension,
            "physical_collection": manifest.physical_collection,
            "alias": manifest.alias,
            "chunks": chunks,
        }
    )


def fixture_rows(
    manifest: DocFixtureManifest,
    vectors_by_chunk_id: Mapping[str, tuple[float, ...]],
) -> tuple[dict[str, object], ...]:
    _validate_manifest(manifest)
    expected_ids = {chunk.chunk_id for chunk in manifest.chunks}
    if set(vectors_by_chunk_id) != expected_ids:
        raise ValueError("fixture vectors must match chunk identities exactly")
    rows = []
    for chunk in manifest.chunks:
        vector = vectors_by_chunk_id[chunk.chunk_id]
        if (
            not isinstance(vector, tuple)
            or len(vector) != manifest.vector_dimension
            or any(
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(item)
                for item in vector
            )
        ):
            raise ValueError("fixture vector does not match the manifest model space")
        rows.append(
            {
                **asdict(chunk),
                "allowed_group_ids": list(chunk.allowed_group_ids),
                "index_family": "doc",
                "physical_collection": manifest.physical_collection,
                "corpus_version": manifest.corpus_version,
                "schema_version": manifest.schema_version,
                "embedding_model_version": manifest.embedding_model_version,
                "source_type": "doc",
                "revision_kind": "blob_version",
                "derived_from_chunk_ids": [],
                "dense_vector": list(vector),
            }
        )
    return tuple(rows)


def validate_collection_descriptor(
    manifest: DocFixtureManifest,
    descriptor: Mapping[str, object] | object,
) -> None:
    _validate_manifest(manifest)
    required = {
        "collection_name": manifest.physical_collection,
        "family": "doc",
        "schema_version": manifest.schema_version,
        "schema_sha256": manifest.schema_sha256,
        "corpus_version": manifest.corpus_version,
        "embedding_model_version": manifest.embedding_model_version,
        "vector_dimension": manifest.vector_dimension,
        "dynamic_fields_enabled": False,
        "consistency_level": "Strong",
    }
    if isinstance(descriptor, Mapping):
        allowed = frozenset(required) | ({"description"} if "description" in descriptor else set())
        if set(descriptor) != allowed:
            raise ValueError("collection descriptor fields are not closed")
        actual = dict(descriptor)
    else:
        actual = {name: getattr(descriptor, name, None) for name in required}
    family = actual.get("family")
    actual["family"] = getattr(family, "value", family)
    if any(actual.get(name) != expected for name, expected in required.items()):
        raise ValueError("collection descriptor does not match the fixture manifest")
    if "description" in actual:
        if _parse_description(actual["description"]) != _parse_description(
            collection_description(manifest)
        ):
            raise ValueError("collection description does not match the fixture manifest")


def _validate_manifest(manifest: DocFixtureManifest) -> None:
    if not isinstance(manifest, DocFixtureManifest):
        raise TypeError("fixture manifest type is invalid")
    if (
        manifest.schema_version != _DOC_SCHEMA_VERSION
        or manifest.schema_sha256 != schema_sha256()
        or manifest.corpus_version != _CORPUS_VERSION
        or manifest.embedding_model_version != _EMBEDDING_MODEL_VERSION
        or manifest.vector_dimension != _VECTOR_DIMENSION
        or manifest.physical_collection != _PHYSICAL_COLLECTION
        or manifest.alias != _ALIAS
        or _SAFE_NAME.fullmatch(manifest.physical_collection) is None
        or _SAFE_NAME.fullmatch(manifest.alias) is None
        or not manifest.physical_collection.startswith("kb_doc_v1_")
    ):
        raise ValueError("fixture manifest target or model contract does not match doc-schema-v1")
    if len(manifest.chunks) != 12:
        raise ValueError("fixture manifest must contain exactly twelve chunks")
    source_ids = {chunk.source_id for chunk in manifest.chunks}
    chunk_ids = {chunk.chunk_id for chunk in manifest.chunks}
    if source_ids != EXPECTED_SOURCE_IDS or len(chunk_ids) != len(manifest.chunks):
        raise ValueError("fixture manifest identities are not exact and unique")
    by_id = {chunk.chunk_id: chunk for chunk in manifest.chunks}
    for chunk in manifest.chunks:
        _validate_chunk(chunk)
        root = by_id.get(chunk.root_id)
        if root is None or root.parent_id is not None or root.root_id != root.chunk_id:
            raise ValueError("fixture root provenance is malformed")
        if chunk.parent_id is not None:
            parent = by_id.get(chunk.parent_id)
            if parent is None or parent.root_id != chunk.root_id:
                raise ValueError("fixture parent provenance is malformed")


def _validate_chunk(chunk: DocFixtureChunk) -> None:
    if (
        _HASH_ID.fullmatch(chunk.chunk_id) is None
        or chunk.chunk_id
        != sha256_id(f"{chunk.source_id}\0{chunk.source_revision}\0{chunk.content}")
        or chunk.logical_chunk_id != sha256_id(chunk.source_id)
        or _HASH_ID.fullmatch(chunk.root_id) is None
        or (chunk.parent_id is not None and _HASH_ID.fullmatch(chunk.parent_id) is None)
        or chunk.source_content_hash != content_hash(chunk.content)
        or chunk.chunk_content_hash != content_hash(chunk.content)
        or chunk.source_revision != _SOURCE_REVISION
        or chunk.content_role != "source"
        or type(chunk.classification_rank) is not int
        or not 0 <= chunk.classification_rank <= 3
        or chunk.environment not in {"production", "staging", "global"}
        or type(chunk.deleted) is not bool
    ):
        raise ValueError("fixture identity, hash, or immutable provenance is malformed")
    _string(chunk.title, "title", maximum=1024, optional=True)
    _string(chunk.content, "content", maximum=32768)
    _string(chunk.tenant_id, "tenant_id", maximum=256)
    _string(chunk.project_id, "project_id", maximum=256)
    _string(chunk.source_id, "source_id", maximum=1024)
    if (
        not isinstance(chunk.allowed_group_ids, tuple)
        or not 1 <= len(chunk.allowed_group_ids) <= 128
        or len(set(chunk.allowed_group_ids)) != len(chunk.allowed_group_ids)
    ):
        raise ValueError("fixture allowed groups are malformed")
    for group_id in chunk.allowed_group_ids:
        _string(group_id, "allowed_group_id", maximum=256)
    anchor = _exact_or_subset_anchor(chunk.anchor_json)
    if anchor.get("type") != "document":
        raise ValueError("fixture anchor type is not document")


def _load_chunk(raw: object) -> DocFixtureChunk:
    value = _exact_mapping(raw, _CHUNK_KEYS, "fixture chunk")
    groups = tuple(
        _string(item, "allowedGroupId", maximum=256)
        for item in _sequence(value["allowedGroupIds"], "allowedGroupIds")
    )
    parent = value["parentId"]
    title = value["title"]
    return DocFixtureChunk(
        chunk_id=_string(value["chunkId"], "chunkId", maximum=66),
        logical_chunk_id=_string(value["logicalChunkId"], "logicalChunkId", maximum=66),
        root_id=_string(value["rootId"], "rootId", maximum=66),
        parent_id=None if parent is None else _string(parent, "parentId", maximum=66),
        title=cast(str | None, _string(title, "title", maximum=1024, optional=True)),
        content=_string(value["content"], "content", maximum=32768),
        content_role=cast(Literal["source"], value["contentRole"]),
        tenant_id=_string(value["tenantId"], "tenantId", maximum=256),
        project_id=_string(value["projectId"], "projectId", maximum=256),
        allowed_group_ids=groups,
        classification_rank=_strict_int(value["classificationRank"], "classificationRank", 0, 3),
        environment=_string(value["environment"], "environment", maximum=128),
        deleted=_strict_bool(value["deleted"], "deleted"),
        source_id=_string(value["sourceId"], "sourceId", maximum=1024),
        source_revision=_string(value["sourceRevision"], "sourceRevision", maximum=512),
        source_content_hash=_digest(value["sourceContentHash"], "sourceContentHash"),
        chunk_content_hash=_digest(value["chunkContentHash"], "chunkContentHash"),
        anchor_json=_string(value["anchorJson"], "anchorJson", maximum=16384),
    )


def _load_query_case(raw: object) -> QueryCase:
    value = _exact_mapping(raw, _QUERY_KEYS, "query case")
    expected_source_ids = tuple(
        _string(item, "expectedSourceId", maximum=1024)
        for item in _sequence(value["expectedSourceIds"], "expectedSourceIds", allow_empty=True)
    )
    if not set(expected_source_ids) <= EXPECTED_SOURCE_IDS or len(set(expected_source_ids)) != len(
        expected_source_ids
    ):
        raise ValueError("query expected sources are malformed")
    environment = value["environment"]
    return QueryCase(
        case_id=_string(value["caseId"], "caseId", maximum=128),
        query=_string(value["query"], "query", maximum=8000),
        tenant_id=_string(value["tenantId"], "tenantId", maximum=256),
        project_id=_string(value["projectId"], "projectId", maximum=256),
        group_ids=tuple(
            _string(item, "groupId", maximum=256)
            for item in _sequence(value["groupIds"], "groupIds", allow_empty=True)
        ),
        classification_ceiling=Classification(
            _string(value["classificationCeiling"], "classificationCeiling", maximum=32)
        ),
        environment=(
            None if environment is None else _string(environment, "environment", maximum=128)
        ),
        expected_source_ids=expected_source_ids,
    )


def _canonical_fields() -> list[dict[str, object]]:
    def field(
        name: str,
        data_type: int,
        params: Mapping[str, object] | None = None,
        *,
        element_type: int | None = None,
        primary: bool = False,
        nullable: bool = False,
        function_output: bool = False,
    ) -> dict[str, object]:
        return {
            "auto_id": False,
            "element_type": element_type,
            "is_function_output": function_output,
            "is_primary": primary,
            "name": name,
            "nullable": nullable,
            "params": dict(params or {}),
            "type": data_type,
        }

    return [
        field("chunk_id", 21, {"max_length": 66}, primary=True),
        field("logical_chunk_id", 21, {"max_length": 66}),
        field("root_id", 21, {"max_length": 66}),
        field("parent_id", 21, {"max_length": 66}, nullable=True),
        field("title", 21, {"max_length": 1024}, nullable=True),
        field(
            "content",
            21,
            {
                "analyzer_params": copy.deepcopy(CONTENT_ANALYZER),
                "enable_analyzer": True,
                "max_length": 32768,
            },
        ),
        field("content_role", 21, {"max_length": 32}),
        field("tenant_id", 21, {"max_length": 256}),
        field("project_id", 21, {"max_length": 256}),
        field(
            "allowed_group_ids",
            22,
            {"max_capacity": 128, "max_length": 256},
            element_type=21,
        ),
        field("classification_rank", 2),
        field("environment", 21, {"max_length": 128}),
        field("deleted", 1),
        field("index_family", 21, {"max_length": 16}),
        field("physical_collection", 21, {"max_length": 255}),
        field("corpus_version", 21, {"max_length": 256}),
        field("schema_version", 21, {"max_length": 256}),
        field("embedding_model_version", 21, {"max_length": 256}),
        field("source_id", 21, {"max_length": 1024}),
        field("source_type", 21, {"max_length": 32}),
        field("revision_kind", 21, {"max_length": 32}),
        field("source_revision", 21, {"max_length": 512}),
        field("source_content_hash", 21, {"max_length": 71}),
        field("chunk_content_hash", 21, {"max_length": 71}),
        field("anchor_json", 21, {"max_length": 16384}),
        field(
            "derived_from_chunk_ids",
            22,
            {"max_capacity": 256, "max_length": 66},
            element_type=21,
        ),
        field("bm25_sparse", 104, function_output=True),
        field("dense_vector", 101, {"dim": 1536}),
    ]


def _canonical_indexes() -> list[dict[str, object]]:
    return sorted(
        (
            {
                "field_name": field_name,
                "index_name": field_name,
                "index_type": value["index_type"],
                "metric_type": value.get("metric_type"),
                "params": copy.deepcopy(value.get("params", {})),
            }
            for field_name, value in INDEXES.items()
        ),
        key=lambda item: cast(str, item["field_name"]),
    )


def _parse_description(raw: object) -> dict[str, object]:
    if not isinstance(raw, str) or not raw.startswith(_METADATA_PREFIX):
        raise ValueError("collection description is malformed")
    value = json.loads(
        raw.removeprefix(_METADATA_PREFIX),
        object_pairs_hook=_closed_pairs,
        parse_constant=_reject_constant,
    )
    metadata = _exact_mapping(value, _METADATA_KEYS, "collection description")
    if metadata["family"] != "doc":
        raise ValueError("collection description family is not doc")
    _digest(metadata["schemaSha256"], "schemaSha256")
    return dict(metadata)


def _exact_or_subset_anchor(raw: str) -> Mapping[str, object]:
    value = json.loads(raw, object_pairs_hook=_closed_pairs, parse_constant=_reject_constant)
    if not isinstance(value, Mapping) or not set(value) <= _ANCHOR_KEYS:
        raise ValueError("fixture document anchor is widened")
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if canonical != raw:
        raise ValueError("fixture document anchor is not canonical JSON")
    heading = value.get("headingPath", [])
    if (
        not isinstance(heading, list)
        or not heading
        or any(not isinstance(item, str) for item in heading)
    ):
        raise ValueError("fixture document anchor heading path is malformed")
    return value


def _load_closed_json(path: Path) -> object:
    if not isinstance(path, Path):
        raise TypeError("fixture path must be a Path")
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_closed_pairs,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("fixture JSON is unavailable or malformed") from error


def _closed_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("fixture JSON contains a duplicate key")
        value[key] = item
    return value


def _reject_constant(value: str) -> object:
    raise ValueError(f"fixture JSON constant is unsupported: {value}")


def _exact_mapping(raw: object, keys: frozenset[str], name: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping) or set(raw) != keys:
        raise ValueError(f"{name} fields are not closed")
    return cast(Mapping[str, object], raw)


def _sequence(raw: object, name: str, *, allow_empty: bool = False) -> Sequence[object]:
    if (
        isinstance(raw, (str, bytes))
        or not isinstance(raw, Sequence)
        or (not raw and not allow_empty)
    ):
        raise ValueError(f"{name} must be a JSON array")
    return cast(Sequence[object], raw)


@overload
def _string(
    raw: object,
    name: str,
    *,
    maximum: int,
    optional: Literal[False] = False,
) -> str: ...


@overload
def _string(
    raw: object,
    name: str,
    *,
    maximum: int,
    optional: Literal[True],
) -> str | None: ...


def _string(
    raw: object,
    name: str,
    *,
    maximum: int,
    optional: bool = False,
) -> str | None:
    if raw is None and optional:
        return None
    if (
        not isinstance(raw, str)
        or not raw
        or len(raw) > maximum
        or any(ord(character) < 0x20 and character not in "\t\n\r" for character in raw)
    ):
        raise ValueError(f"{name} must be bounded text")
    return raw


def _digest(raw: object, name: str) -> str:
    value = _string(raw, name, maximum=71)
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical SHA-256 digest")
    return value


def _strict_int(raw: object, name: str, minimum: int, maximum: int) -> int:
    if type(raw) is not int or not minimum <= raw <= maximum:
        raise ValueError(f"{name} must be a bounded integer")
    return raw


def _strict_bool(raw: object, name: str) -> bool:
    if type(raw) is not bool:
        raise ValueError(f"{name} must be a boolean")
    return raw


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
