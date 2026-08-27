"""Reusable canonical doc-family Milvus schema construction."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

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

_METADATA_PREFIX = "tap-collection-metadata-v1:"
_DIGEST_LENGTH = 71


@dataclass(frozen=True, slots=True)
class DocCollectionMetadata:
    schema_version: str
    schema_sha256: str
    corpus_version: str
    embedding_model_version: str
    vector_dimension: int

    def __post_init__(self) -> None:
        for name in (
            "schema_version",
            "corpus_version",
            "embedding_model_version",
        ):
            _bounded_text(name, getattr(self, name), maximum=256)
        _canonical_digest("schema_sha256", self.schema_sha256)
        if type(self.vector_dimension) is not int or not 1 <= self.vector_dimension <= 4096:
            raise ValueError("vector_dimension must be an integer from one through 4096")
        if self.schema_sha256 != doc_schema_sha256():
            raise ValueError("doc collection metadata does not match the canonical schema")


def build_doc_collection_schema(metadata: DocCollectionMetadata) -> dict[str, object]:
    _metadata(metadata)
    fields = _canonical_fields()
    dense = next(field for field in fields if field["name"] == "dense_vector")
    dense["params"] = {"dim": metadata.vector_dimension}
    return {
        "auto_id": False,
        "enable_dynamic_field": False,
        "enable_namespace": False,
        "consistency_level": "Strong",
        "description": doc_collection_description(metadata),
        "vector_dimension": metadata.vector_dimension,
        "fields": tuple(fields),
        "functions": (copy.deepcopy(BM25_FUNCTION),),
        "indexes": copy.deepcopy(INDEXES),
    }


def doc_schema_sha256() -> str:
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
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def doc_collection_description(metadata: DocCollectionMetadata) -> str:
    _metadata(metadata)
    value = {
        "family": "doc",
        "schemaVersion": metadata.schema_version,
        "schemaSha256": metadata.schema_sha256,
        "corpusVersion": metadata.corpus_version,
        "embeddingModelVersion": metadata.embedding_model_version,
        "vectorDimension": metadata.vector_dimension,
    }
    return _METADATA_PREFIX + json.dumps(value, sort_keys=True, separators=(",", ":"))


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


def _metadata(value: object) -> DocCollectionMetadata:
    if not isinstance(value, DocCollectionMetadata):
        raise TypeError("doc collection metadata type is invalid")
    return value


def _bounded_text(name: str, value: object, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 0x20 for character in value)
    ):
        raise ValueError(f"{name} must be bounded text")
    return value


def _canonical_digest(name: str, value: object) -> str:
    text = _bounded_text(name, value, maximum=_DIGEST_LENGTH)
    if (
        len(text) != _DIGEST_LENGTH
        or not text.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in text[7:])
    ):
        raise ValueError(f"{name} must be a canonical SHA-256 digest")
    return text
