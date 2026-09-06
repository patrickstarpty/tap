"""Stable Project-owned Source identities and immutable ingestion receipt digests."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4


def legacy_source_id(project_id: str, document_id: str) -> str:
    if not project_id or not document_id or "\0" in project_id + document_id:
        raise ValueError("invalid legacy source identity")
    return (
        "src_"
        + hashlib.sha256(f"legacy-source-v1\0{project_id}\0{document_id}".encode()).hexdigest()[:32]
    )


def new_source_id() -> str:
    return "src_" + uuid4().hex


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    name: str
    created_at: datetime
    deleted_at: datetime | None


class SourceUnavailable(ValueError):
    """A Source is absent, foreign, or tombstoned."""


def source_facts_digest(values: object) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
    )


def chunk_manifest_digest(manifest: tuple) -> str:
    from dataclasses import asdict

    return source_facts_digest(["knowledge-chunk-manifest-v1", [asdict(item) for item in manifest]])


def projection_digest(
    revision_id: str, schema_version: str, index_version: str, manifest: tuple
) -> str:
    from dataclasses import asdict

    return source_facts_digest(
        [
            "knowledge-logical-projection-v1",
            revision_id,
            schema_version,
            index_version,
            [asdict(item) for item in manifest],
        ]
    )
