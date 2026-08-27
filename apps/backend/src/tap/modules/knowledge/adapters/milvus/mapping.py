"""Strictly map provider-free Milvus rows into Knowledge search hits."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping

from tap.modules.knowledge.adapters.milvus.targets import BoundMilvusTarget
from tap.modules.knowledge.domain.models import (
    ContentRole,
    DocumentAnchor,
    IndexRevision,
    RevisionKind,
    SourceFamily,
    SourceRevisionRef,
)
from tap.modules.knowledge.ports.errors import SearchUnavailable
from tap.modules.knowledge.ports.models import SearchHit

_CHUNK_ID = re.compile(r"h_[0-9a-f]{64}\Z")
_ROW_FIELDS = frozenset(
    {
        "chunk_id",
        "logical_chunk_id",
        "root_id",
        "parent_id",
        "title",
        "content",
        "content_role",
        "index_family",
        "physical_collection",
        "schema_version",
        "corpus_version",
        "embedding_model_version",
        "source_id",
        "source_type",
        "revision_kind",
        "source_revision",
        "source_content_hash",
        "chunk_content_hash",
        "anchor_json",
        "derived_from_chunk_ids",
        "score",
        "provider_request_id",
    }
)
_DOCUMENT_ANCHOR_FIELDS = frozenset(
    {"type", "headingPath", "page", "bbox", "startOffset", "endOffset"}
)


def map_milvus_hit(
    row: Mapping[str, object],
    bound: BoundMilvusTarget,
    local_rank: int,
) -> SearchHit:
    """Fail closed unless every row value matches the bound doc target."""
    try:
        if not isinstance(row, Mapping) or set(row) != _ROW_FIELDS:
            raise ValueError("row fields are not closed")
        if not isinstance(bound, BoundMilvusTarget):
            raise ValueError("target is not bound")
        if type(local_rank) is not int or local_rank < 1:
            raise ValueError("local rank is malformed")

        configured = bound.configured
        family = SourceFamily(_required_string(row, "index_family", maximum=16))
        physical_collection = _required_string(row, "physical_collection", maximum=255)
        schema_version = _required_string(row, "schema_version", maximum=256)
        corpus_version = _required_string(row, "corpus_version", maximum=256)
        embedding_model_version = _required_string(
            row,
            "embedding_model_version",
            maximum=256,
        )
        if (
            family is not configured.family
            or physical_collection != bound.physical_collection
            or schema_version != configured.schema_version
            or corpus_version != configured.corpus_version
            or embedding_model_version != configured.embedding_model_version
        ):
            raise SearchUnavailable("Milvus row does not match bound target")
        if family is not SourceFamily.DOC:
            raise ValueError("only doc rows are supported")

        source_type = _required_string(row, "source_type", maximum=32)
        revision_kind = RevisionKind(_required_string(row, "revision_kind", maximum=32))
        if source_type != "doc" or revision_kind is not RevisionKind.BLOB_VERSION:
            raise ValueError("source provenance is incompatible with doc")

        anchor = _document_anchor(row["anchor_json"])
        source = SourceRevisionRef(
            source_id=_required_string(row, "source_id", maximum=1_024),
            source_type=source_type,
            revision_kind=revision_kind,
            revision=_required_string(row, "source_revision", maximum=512),
            source_content_hash=_required_sha256(row, "source_content_hash"),
            anchor=anchor,
        )
        derived_raw = row["derived_from_chunk_ids"]
        if not isinstance(derived_raw, list) or len(derived_raw) > 256:
            raise ValueError("derived chunk identifiers exceed the bound")
        derived = tuple(_chunk_id(value) for value in derived_raw)
        if len(set(derived)) != len(derived):
            raise ValueError("derived chunk identifiers must be unique")

        score = row["score"]
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
        ):
            raise ValueError("score must be finite")

        content = _required_content(row["content"])
        chunk_content_hash = _required_sha256(row, "chunk_content_hash")
        if "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest() != chunk_content_hash:
            raise ValueError("content does not match immutable chunk hash")

        return SearchHit(
            family=family,
            chunk_id=_chunk_id(row["chunk_id"]),
            logical_chunk_id=_chunk_id(row["logical_chunk_id"]),
            root_id=_chunk_id(row["root_id"]),
            parent_id=_optional_chunk_id(row["parent_id"]),
            title=_optional_string(row, "title", maximum=1_024),
            content=content,
            source=source,
            chunk_content_hash=chunk_content_hash,
            content_role=ContentRole(_required_string(row, "content_role", maximum=32)),
            index_revision=IndexRevision(
                physical_index=bound.physical_collection,
                schema_version=schema_version,
                corpus_version=corpus_version,
            ),
            embedding_model_version=embedding_model_version,
            score=float(score),
            local_rank=local_rank,
            derived_from_chunk_ids=derived,
            provider_request_id=_optional_string(
                row,
                "provider_request_id",
                maximum=256,
            ),
        )
    except SearchUnavailable:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SearchUnavailable("Milvus result lacks strict immutable provenance") from error


def _required_string(
    row: Mapping[str, object],
    name: str,
    *,
    maximum: int,
) -> str:
    value = row[name]
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 0x20 for character in value)
    ):
        raise ValueError(f"{name} must be a bounded string")
    return value


def _optional_string(
    row: Mapping[str, object],
    name: str,
    *,
    maximum: int,
) -> str | None:
    value = row[name]
    if value is None:
        return None
    return _required_string(row, name, maximum=maximum)


def _required_sha256(row: Mapping[str, object], name: str) -> str:
    value = row[name]
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{name} must be a canonical SHA-256 digest")
    return value


def _required_content(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 32_768
        or any(ord(character) < 0x20 and character not in "\t\n\r" for character in value)
    ):
        raise ValueError("content must be bounded document text")
    return value


def _chunk_id(value: object) -> str:
    if not isinstance(value, str) or _CHUNK_ID.fullmatch(value) is None:
        raise ValueError("chunk identity is malformed")
    return value


def _optional_chunk_id(value: object) -> str | None:
    if value is None:
        return None
    return _chunk_id(value)


def _document_anchor(raw: object) -> DocumentAnchor:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 16_384:
        raise ValueError("document anchor exceeds the bound")
    value = json.loads(raw, parse_constant=_reject_json_constant)
    if (
        not isinstance(value, dict)
        or set(value) - _DOCUMENT_ANCHOR_FIELDS
        or value.get("type") != "document"
    ):
        raise ValueError("document anchor uses a widened schema")

    heading_path = value.get("headingPath", [])
    bbox = value.get("bbox", [])
    if not isinstance(heading_path, list) or not isinstance(bbox, list):
        raise ValueError("document anchor arrays are malformed")
    return DocumentAnchor(
        heading_path=tuple(heading_path),
        page=_optional_int(value, "page", minimum=1),
        bbox=tuple(bbox),
        start_offset=_optional_int(value, "startOffset", minimum=0),
        end_offset=_optional_int(value, "endOffset", minimum=0),
    )


def _optional_int(value: Mapping[str, object], name: str, *, minimum: int) -> int | None:
    item = value.get(name)
    if item is None:
        return None
    if type(item) is not int or item < minimum:
        raise ValueError(f"{name} must be a bounded integer")
    return item


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"unsupported JSON constant: {value}")
