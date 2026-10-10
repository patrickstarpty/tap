"""Real-corpus manifest structure and fail-closed validation for the Graph golden set.

The manifest enumerates the external documents (insurer policy PDFs and internal
process documents) used to label `graph-relations` golden questions. Manifest
entries never embed document bytes or point into the repository: this module only
validates shape, uniqueness and local-file integrity.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from tap.quality.evidence import canonical_digest

LOCAL_CORPUS_DIR = Path(".local/graph-real")

_ID = re.compile(r"[a-z0-9-]+\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HTTPS_URL = re.compile(r"https://\S+\Z")
_REPOSITORY_FIXTURES_MARKERS = ("tests/fixtures/", "tests\\fixtures\\")

_POLICY_REQUIRED_COUNT = 8
_PROCESS_MIN_COUNT = 2
_PROCESS_MAX_COUNT = 3


@dataclass(frozen=True)
class CorpusEntry:
    id: str
    title: str
    kind: Literal["policy", "process"]
    sha256: str
    url: str | None
    path: str | None


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _array(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _reject_repository_reference(value: str | None) -> None:
    if value is None:
        return
    normalized = value.replace("\\", "/")
    if any(marker.replace("\\", "/") in normalized for marker in _REPOSITORY_FIXTURES_MARKERS):
        raise ValueError("corpus files must not live in the repository")


def validate_manifest(value: object) -> tuple[CorpusEntry, ...]:
    """Parse and validate a real-corpus manifest, returning its entries in order."""
    manifest = _mapping(value, "manifest")
    if manifest.get("schemaVersion") != "graph-real-corpus-manifest-v1":
        raise ValueError("unsupported corpus manifest schema")
    raw_entries = [
        _mapping(item, "corpus entry") for item in _array(manifest.get("entries"), "entries")
    ]

    entries: list[CorpusEntry] = []
    seen_ids: set[str] = set()
    policy_count = 0
    process_count = 0
    for raw in raw_entries:
        kind = raw.get("kind")
        if kind not in ("policy", "process"):
            raise ValueError(f"corpus entry {raw.get('id')!r} kind must be 'policy' or 'process'")

        entry_id = raw.get("id")
        if not isinstance(entry_id, str) or _ID.fullmatch(entry_id) is None:
            raise ValueError(f"{kind} entry {entry_id!r} id must match [a-z0-9-]+")
        if entry_id in seen_ids:
            raise ValueError(f"{kind} entry {entry_id} id is not unique")
        seen_ids.add(entry_id)

        title = raw.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"{kind} entry {entry_id} must have a nonblank title")

        sha256 = raw.get("sha256")
        if not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
            raise ValueError(f"{kind} entry {entry_id} sha256 must match sha256:[0-9a-f]{{64}}")

        url = raw.get("url")
        path = raw.get("path")
        _reject_repository_reference(url)
        _reject_repository_reference(path)

        if kind == "policy":
            policy_count += 1
            if not isinstance(url, str) or _HTTPS_URL.fullmatch(url) is None:
                raise ValueError(f"policy entry {entry_id} must have an https:// url")
            path = None
        else:
            process_count += 1
            if not isinstance(path, str) or not path.strip():
                raise ValueError(f"process entry {entry_id} must have a path")
            resolved = Path(path).resolve()
            local_root = LOCAL_CORPUS_DIR.resolve()
            if local_root not in (resolved, *resolved.parents):
                raise ValueError(
                    f"process entry {entry_id} path must resolve within {LOCAL_CORPUS_DIR}"
                )
            url = None

        entries.append(
            CorpusEntry(
                id=entry_id,
                title=title,
                kind=kind,
                sha256=sha256,
                url=url,
                path=path,
            )
        )

    if policy_count != _POLICY_REQUIRED_COUNT:
        raise ValueError(
            f"manifest must contain exactly {_POLICY_REQUIRED_COUNT} policy entries, "
            f"got {policy_count}"
        )
    if not (_PROCESS_MIN_COUNT <= process_count <= _PROCESS_MAX_COUNT):
        raise ValueError(
            f"manifest must contain {_PROCESS_MIN_COUNT}-{_PROCESS_MAX_COUNT} process entries, "
            f"got {process_count}"
        )

    return tuple(entries)


def download_target(entry: CorpusEntry) -> Path:
    """Return the local file path a corpus entry's bytes are expected at."""
    if entry.kind == "process":
        if entry.path is None:
            raise ValueError(f"process entry {entry.id} has no path")
        return Path(entry.path)
    return LOCAL_CORPUS_DIR / f"{entry.id}.pdf"


def verify_local_file(entry: CorpusEntry) -> None:
    """Require the entry's local file to exist and match its declared sha256."""
    target = download_target(entry)
    if not target.is_file():
        raise ValueError(f"corpus entry {entry.id} is missing its local file at {target}")
    digest = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != entry.sha256:
        raise ValueError(f"corpus entry {entry.id} local file does not match its sha256")


def manifest_digest(value: object) -> str:
    """Return the canonical digest binding a manifest's exact content."""
    return canonical_digest(value)
