#!/usr/bin/env python3
"""Validate, download and upload the Graph real-corpus manifest.

Publication into the Library review flow stays a manual, human step (see the
2026-10-05 decision record); this script only gets ready-to-review documents and
their revision/graph-version bindings onto a running TAP AI backend.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from tap.quality.graph_corpus import (
    CorpusEntry,
    download_target,
    manifest_digest,
    validate_manifest,
    verify_local_file,
)

_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
_DEFAULT_TIMEOUT_SECONDS = 1800
_POLL_INTERVAL_SECONDS = 5


def _load_manifest(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _print_entry_counts(raw: object) -> None:
    entries_raw = raw.get("entries", []) if isinstance(raw, dict) else []
    policy_count = sum(
        1 for item in entries_raw if isinstance(item, dict) and item.get("kind") == "policy"
    )
    process_count = sum(
        1 for item in entries_raw if isinstance(item, dict) and item.get("kind") == "process"
    )
    print(f"policy entries: {policy_count}/8")
    print(f"process entries: {process_count}/2-3")


def cmd_validate(arguments: argparse.Namespace) -> int:
    raw = _load_manifest(arguments.manifest)
    _print_entry_counts(raw)
    try:
        entries = validate_manifest(raw)
    except ValueError as error:
        print(f"error: {error}")
        return 1
    print(f"manifest valid: {len(entries)} entries, digest={manifest_digest(raw)}")
    return 0


def _download_to(url: str, target: Path) -> None:
    request = urllib.request.Request(url)  # noqa: S310 - manifest URLs are https:// only
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        content_length = response.headers.get("Content-Length")
        if content_length is not None and int(content_length) > _MAX_DOWNLOAD_BYTES:
            raise ValueError(f"remote file exceeds {_MAX_DOWNLOAD_BYTES} bytes")
        data = response.read(_MAX_DOWNLOAD_BYTES + 1)
    if len(data) > _MAX_DOWNLOAD_BYTES:
        raise ValueError(f"remote file exceeds {_MAX_DOWNLOAD_BYTES} bytes")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def cmd_download(arguments: argparse.Namespace) -> int:
    raw = _load_manifest(arguments.manifest)
    entries = validate_manifest(raw)
    failures: list[str] = []
    for entry in entries:
        target = download_target(entry)
        if entry.kind == "process":
            try:
                verify_local_file(entry)
                print(f"ok (local): {entry.id}")
            except ValueError as error:
                failures.append(f"{entry.id}: {error}")
            continue
        if target.is_file():
            try:
                verify_local_file(entry)
                print(f"ok (cached): {entry.id}")
                continue
            except ValueError:
                pass  # stale or tampered cache; re-download below
        try:
            assert entry.url is not None
            _download_to(entry.url, target)
            verify_local_file(entry)
        except (urllib.error.URLError, ValueError) as error:
            failures.append(f"{entry.id}: {error}")
            continue
        print(f"ok (downloaded): {entry.id}")
    if failures:
        print("failed entries:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    return 0


def _multipart_body(field_name: str, filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = f"graph-real-{uuid.uuid4().hex}"
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    body += content
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


def _runtime_project_id(api: str) -> str:
    request = urllib.request.Request(f"{api}/api/v1/runtime-mode")  # noqa: S310
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        body = json.loads(response.read().decode("utf-8"))
    return str(body["projectId"])


def _upload_source(api: str, project_id: str, entry: CorpusEntry, target: Path) -> str:
    filename = f"{entry.id}.pdf" if entry.kind == "policy" else target.name
    body, content_type = _multipart_body("upload", filename, target.read_bytes())
    idempotency_key = f"graph-real-{entry.id}-{entry.sha256.removeprefix('sha256:')[:12]}"
    url = f"{api}/api/v1/projects/{project_id}/knowledge/sources"
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": content_type, "Idempotency-Key": idempotency_key},
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        accepted = json.loads(response.read().decode("utf-8"))
    return str(accepted["source"]["sourceId"])


def _poll_documents_ready(
    api: str, project_id: str, source_ids: set[str], deadline: float
) -> dict[str, str]:
    url = f"{api}/api/v1/projects/{project_id}/knowledge/documents?limit=200"
    while True:
        request = urllib.request.Request(url)  # noqa: S310
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8"))
        by_source: dict[str, list[dict[str, Any]]] = {}
        for item in body.get("items", []):
            by_source.setdefault(str(item.get("sourceId")), []).append(item)
        revisions: dict[str, str] = {}
        for source_id in source_ids:
            ready = next(
                (
                    item
                    for item in by_source.get(source_id, [])
                    if str(item.get("status", "")).lower() == "ready"
                ),
                None,
            )
            if ready is not None:
                revisions[source_id] = str(ready.get("revisionId"))
        if len(revisions) == len(source_ids):
            return revisions
        if time.monotonic() > deadline:
            raise TimeoutError("knowledge documents did not become ready before the deadline")
        time.sleep(_POLL_INTERVAL_SECONDS)


def _poll_graph_ready(api: str, project_id: str, deadline: float) -> str:
    url = f"{api}/api/v1/projects/{project_id}/knowledge/graph/project"
    while True:
        request = urllib.request.Request(url)  # noqa: S310
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8"))
        if str(body.get("status")) == "READY":
            return str(body.get("graphVersion"))
        if time.monotonic() > deadline:
            raise TimeoutError("project graph did not become READY before the deadline")
        time.sleep(_POLL_INTERVAL_SECONDS)


def cmd_upload(arguments: argparse.Namespace) -> int:
    raw = _load_manifest(arguments.manifest)
    entries = validate_manifest(raw)
    for entry in entries:
        verify_local_file(entry)

    project_id = arguments.project_id or _runtime_project_id(arguments.api)
    deadline = time.monotonic() + arguments.timeout_seconds

    sources: list[dict[str, str]] = []
    for entry in entries:
        target = download_target(entry)
        source_id = _upload_source(arguments.api, project_id, entry, target)
        sources.append({"id": entry.id, "sourceId": source_id, "revisionId": ""})

    revisions = _poll_documents_ready(
        arguments.api, project_id, {item["sourceId"] for item in sources}, deadline
    )
    for item in sources:
        item["revisionId"] = revisions[item["sourceId"]]

    graph_version = _poll_graph_ready(arguments.api, project_id, deadline)
    output = {
        "manifestDigest": manifest_digest(raw),
        "graphVersion": graph_version,
        "sources": sources,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(output, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(output, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="validate the manifest shape only")
    validate_parser.add_argument("--manifest", type=Path, required=True)
    validate_parser.set_defaults(handler=cmd_validate)

    download_parser = subparsers.add_parser(
        "download", help="download missing policy PDFs and verify every local file"
    )
    download_parser.add_argument("--manifest", type=Path, required=True)
    download_parser.set_defaults(handler=cmd_download)

    upload_parser = subparsers.add_parser(
        "upload", help="upload every corpus file to a running TAP AI backend"
    )
    upload_parser.add_argument("--manifest", type=Path, required=True)
    upload_parser.add_argument("--api", required=True)
    upload_parser.add_argument("--project-id", default=None)
    upload_parser.add_argument("--output", type=Path, required=True)
    upload_parser.add_argument("--timeout-seconds", type=int, default=_DEFAULT_TIMEOUT_SECONDS)
    upload_parser.set_defaults(handler=cmd_upload)

    arguments = parser.parse_args()
    return int(arguments.handler(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
