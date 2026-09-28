"""Bounded Allure results ZIP reader. Archives are never extracted to disk."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import io
import json
import math
from pathlib import PurePosixPath
import stat
from typing import Any
import zipfile

from tap_platform.insights.adapters.report_errors import ReportSecurityError
from tap_platform.insights.domain.reports import ReportManifest, TestAttemptFact


class AllureSecurityError(ReportSecurityError):
    """Rejection raised by the Allure results parser."""


MAX_FILES = 5000
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 10 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
MAX_DOCUMENT_CHARS = MAX_JSON_BYTES
MAX_IDENTITY_CHARS = 512
TRUNCATED = "… [truncated]"


def _platform_metadata(name: str) -> bool:
    """Archive tool side files (macOS Finder AppleDouble/.DS_Store) carry no results."""
    path = PurePosixPath(name)
    return (
        path.parts[0] == "__MACOSX"
        or path.name.startswith("._")
        or path.name == ".DS_Store"
    )


def _checked_entries(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Validate every member's name and size, returning readable result members."""
    entries = archive.infolist()
    if len(entries) > MAX_FILES:
        raise AllureSecurityError("allure-file-limit")
    total = 0
    readable: list[zipfile.ZipInfo] = []
    seen: set[str] = set()
    for item in entries:
        path = PurePosixPath(item.filename)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in item.filename
            or ":" in item.filename
            or "\x00" in item.filename
            or len(item.filename) > 512
            or str(path) != item.filename.rstrip("/")
            or stat.S_ISLNK(item.external_attr >> 16)
            or item.flag_bits & 1
        ):
            raise AllureSecurityError("allure-unsafe-member")
        if item.filename in seen:
            raise AllureSecurityError("allure-duplicate-member")
        seen.add(item.filename)
        total += item.file_size
        if (
            item.file_size > MAX_MEMBER_BYTES
            or total > MAX_EXPANDED_BYTES
            or item.file_size > max(1, item.compress_size) * 200
        ):
            raise AllureSecurityError("allure-expansion-limit")
        if not item.is_dir() and not _platform_metadata(item.filename):
            readable.append(item)
    return readable


def archive_members(raw: bytes) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            return {
                item.filename: archive.read(item) for item in _checked_entries(archive)
            }
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as exc:
        raise AllureSecurityError("invalid-allure-zip") from exc


def archive_member(raw: bytes, name: str) -> bytes:
    """Read one validated member without expanding the rest of the archive."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for item in _checked_entries(archive):
                if item.filename == name:
                    return archive.read(item)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as exc:
        raise AllureSecurityError("invalid-allure-zip") from exc
    raise KeyError(name)


def _document(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_JSON_BYTES:
        raise AllureSecurityError("allure-json-limit")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise AllureSecurityError("invalid-allure-json") from exc
    if not isinstance(value, dict):
        raise AllureSecurityError("invalid-allure-result")
    return value


def _text(value: Any, limit: int = 10000) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise AllureSecurityError("invalid-allure-text")
    return value


def _display(value: Any, limit: int = 10000) -> str:
    """Report prose is bounded for storage but never rejects the whole run."""
    if not isinstance(value, str):
        raise AllureSecurityError("invalid-allure-text")
    return value if len(value) <= limit else value[:limit] + TRUNCATED


def _list(value: Any) -> list[Any]:
    if not isinstance(value, list) or len(value) > 5000:
        raise AllureSecurityError("invalid-allure-list")
    return value


def _time(value: Any) -> float | None:
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def result_documents(members: dict[str, bytes]) -> list[tuple[str, dict[str, Any]]]:
    values = [
        (name, _document(data))
        for name, data in sorted(members.items())
        if name.endswith("-result.json")
    ]
    if not values:
        raise AllureSecurityError("allure-results-missing")
    seen = set()
    for name, value in values:
        if len(name) > 256:
            raise AllureSecurityError("allure-result-path-limit")
        uuid = _text(value.get("uuid"), 256)
        if not uuid or uuid in seen:
            raise AllureSecurityError("allure-duplicate-uuid")
        seen.add(uuid)
    return values


def _identity(value: dict[str, Any], mapping: dict[str, str]) -> tuple[str, str, str]:
    full = _display(value.get("fullName"), MAX_DOCUMENT_CHARS)
    if not full:
        raise AllureSecurityError("allure-identity-missing")
    digest = hashlib.sha256(full.encode()).hexdigest()
    # Long names stay distinguishable: the bounded display keeps a digest suffix.
    identity = (
        full
        if len(full) <= MAX_IDENTITY_CHARS
        else full[: MAX_IDENTITY_CHARS - 18] + "…#" + digest[:16]
    )
    # pytest Allure names read module#test; JUnit XML reads classname::name.
    stable = mapping.get(full.replace("#", "::", 1)) or (
        "allure-"
        + hashlib.sha256(
            (_text(value.get("testCaseId", ""), 256) or digest).encode()
        ).hexdigest()
    )
    params = []
    for item in _list(value.get("parameters", [])):
        if not isinstance(item, dict):
            raise AllureSecurityError("invalid-allure-parameter")
        if not item.get("excluded", False):
            params.append(
                (
                    _display(item.get("name"), MAX_DOCUMENT_CHARS),
                    _display(item.get("value"), MAX_DOCUMENT_CHARS),
                )
            )
    row = (
        _text(value.get("historyId", ""), 256)
        or hashlib.sha256(json.dumps([full, sorted(params)]).encode()).hexdigest()
    )
    return identity, stable, row


def _attachments(
    node: dict[str, Any], parent: str, members: dict[str, bytes]
) -> list[dict[str, Any]]:
    attachments = []
    for item in _list(node.get("attachments", [])):
        if not isinstance(item, dict):
            raise AllureSecurityError("invalid-allure-attachment")
        source = _text(item.get("source"), 512)
        path = PurePosixPath(source)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in source
            or ":" in source
            or str(path) != source
        ):
            raise AllureSecurityError("allure-unsafe-attachment")
        resolved = str(PurePosixPath(parent).parent / source)
        attachments.append(
            {
                "name": _display(item.get("name", source)),
                "source": resolved,
                "mediaType": _text(item.get("type", "application/octet-stream"), 128),
                "available": resolved in members,
            }
        )
    return attachments


def evidence_node(
    node: dict[str, Any], parent: str, members: dict[str, bytes], depth: int = 0
) -> dict[str, Any]:
    if depth > 20:
        raise AllureSecurityError("allure-step-depth-limit")
    details = node.get("statusDetails", {})
    if not isinstance(details, dict):
        raise AllureSecurityError("invalid-allure-status-details")
    steps = []
    for step in _list(node.get("steps", [])):
        if not isinstance(step, dict):
            raise AllureSecurityError("invalid-allure-step")
        steps.append(evidence_node(step, parent, members, depth + 1))
    return {
        "name": _display(node.get("name", "")),
        "status": _text(node.get("status", "unknown"), 32),
        "message": _display(details.get("message", "")),
        "trace": _display(details.get("trace", ""), 100000),
        "attachments": _attachments(node, parent, members),
        "steps": steps,
    }


def allure_evidence(
    members: dict[str, bytes],
    results: list[tuple[str, dict[str, Any]]] | None = None,
) -> dict[str, dict[str, Any]]:
    if results is None:
        results = result_documents(members)
    nodes = {name: evidence_node(value, name, members) for name, value in results}
    budget = sum(len(json.dumps(node).encode()) for node in nodes.values())
    if budget > MAX_EVIDENCE_BYTES:
        raise AllureSecurityError("allure-evidence-limit")
    by_uuid = {value["uuid"]: name for name, value in results}
    fixture_steps: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for name, data in sorted(members.items()):
        if not name.endswith("-container.json"):
            continue
        container = _document(data)
        children = [_text(child, 256) for child in _list(container.get("children", []))]
        if len(set(children)) != len(children):
            raise AllureSecurityError("allure-duplicate-fixture-child")
        for kind in ("befores", "afters"):
            steps = []
            for fixture in _list(container.get(kind, [])):
                if not isinstance(fixture, dict):
                    raise AllureSecurityError("invalid-allure-fixture")
                steps.append(evidence_node(fixture, name, members))
            budget += len(json.dumps(steps).encode()) * sum(
                child in by_uuid for child in children
            )
            if budget > MAX_EVIDENCE_BYTES:
                raise AllureSecurityError("allure-evidence-limit")
            for child in children:
                if child in by_uuid:
                    fixture_steps.setdefault(child, {"befores": [], "afters": []})[
                        kind
                    ].extend(steps)
    # Bound fixture fan-out as well as ZIP size: one fixture may have many children.
    if (
        sum(len(v["befores"]) + len(v["afters"]) for v in fixture_steps.values())
        > 10000
    ):
        raise AllureSecurityError("allure-fixture-limit")
    for uuid, fixtures in fixture_steps.items():
        node = nodes[by_uuid[uuid]]
        node["steps"] = fixtures["befores"] + node["steps"] + fixtures["afters"]
    return nodes


def parse_allure(raw: bytes, manifest: ReportManifest) -> list[TestAttemptFact]:
    return parse_allure_members(archive_members(raw), manifest)[0]


def parse_allure_members(
    members: dict[str, bytes], manifest: ReportManifest
) -> tuple[list[TestAttemptFact], dict[str, dict[str, Any]]]:
    """Map facts and build the bounded evidence tree from one archive read."""
    results = result_documents(members)
    nodes = allure_evidence(members, results)
    mapping = dict(manifest.external_test_id_mapping)
    groups: dict[tuple[str, str], list[tuple[str, dict[str, Any], str]]] = defaultdict(
        list
    )
    for name, value in results:
        identity, stable, row = _identity(value, mapping)
        groups[stable, row].append((name, value, identity))
    facts = []
    outcomes = {
        "passed": "pass",
        "failed": "fail",
        "broken": "error",
        "skipped": "skipped",
        "unknown": "unknown",
    }
    for (stable, row), items in sorted(groups.items()):
        items.sort(key=lambda item: (_time(item[1].get("start")) or 0, item[0]))
        times = [(_time(v.get("start")), _time(v.get("stop"))) for _, v, _ in items]
        ordered = all(a is not None and b is not None and b >= a for a, b in times)
        for previous, current in zip(times, times[1:]):
            previous_stop, current_start = previous[1], current[0]
            if (
                previous_stop is None
                or current_start is None
                or current_start < previous_stop
                or current_start == previous[0]
            ):
                ordered = False
        for index, (name, value, identity) in enumerate(items, 1):
            start, stop = _time(value.get("start")), _time(value.get("stop"))
            valid_time = start is not None and stop is not None and stop >= start
            attempt = index if manifest.contains_complete_attempts and ordered else None
            missing = []
            if attempt is None:
                missing.append(
                    "attempt-history-incomplete"
                    if not manifest.contains_complete_attempts
                    else "allure-attempt-order-ambiguous"
                )
            if not valid_time:
                missing.append("duration-invalid")
            status = value.get("status", "unknown")
            if not isinstance(status, str) or status not in outcomes:
                raise AllureSecurityError("invalid-allure-status")
            facts.append(
                TestAttemptFact(
                    source_test_identity=identity,
                    source_locator=name,
                    stable_test_id=stable,
                    data_row=row,
                    attempt=attempt,
                    result=outcomes[status],
                    duration_seconds=(stop - start) / 1000
                    if start is not None and stop is not None and valid_time
                    else None,
                    evidence_refs=(),
                    missing_reasons=tuple(missing),
                    first_attempt_eligible=attempt == 1,
                )
            )
    return facts, nodes
