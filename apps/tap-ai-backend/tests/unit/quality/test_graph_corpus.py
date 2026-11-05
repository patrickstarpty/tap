from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tap.quality import graph_corpus
from tap.quality.graph_corpus import (
    CorpusEntry,
    download_target,
    validate_manifest,
    verify_local_file,
)

_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "quality" / "graph-real"


def _sha(byte_value: bytes = b"x") -> str:
    return "sha256:" + hashlib.sha256(byte_value).hexdigest()


def _policy_entry(index: int, **overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "id": f"policy-{index:02d}",
        "title": f"Policy {index}",
        "kind": "policy",
        "sha256": _sha(f"policy-{index}".encode()),
        "url": f"https://example.invalid/policy-{index}.pdf",
    }
    entry.update(overrides)
    return entry


def _process_entry(index: int, **overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "id": f"process-{index:02d}",
        "title": f"Process {index}",
        "kind": "process",
        "sha256": _sha(f"process-{index}".encode()),
        "path": f".local/graph-real/process-{index:02d}.pdf",
    }
    entry.update(overrides)
    return entry


def _valid_manifest(**overrides: object) -> dict[str, object]:
    manifest: dict[str, object] = {
        "schemaVersion": "graph-real-corpus-manifest-v1",
        "entries": [_policy_entry(index) for index in range(1, 9)]
        + [_process_entry(index) for index in range(1, 3)],
    }
    manifest.update(overrides)
    return manifest


def test_manifest_requires_eight_policy_entries_with_url_sha_and_title():
    missing_url = _valid_manifest()
    del missing_url["entries"][0]["url"]
    with pytest.raises(ValueError, match="policy"):
        validate_manifest(missing_url)

    only_seven = _valid_manifest()
    only_seven["entries"] = [entry for entry in only_seven["entries"] if entry["id"] != "policy-08"]
    with pytest.raises(ValueError, match="policy"):
        validate_manifest(only_seven)

    bad_sha = _valid_manifest()
    bad_sha["entries"][0]["sha256"] = "sha256:not-a-valid-digest"
    with pytest.raises(ValueError, match="policy"):
        validate_manifest(bad_sha)

    missing_title = _valid_manifest()
    missing_title["entries"][0]["title"] = ""
    with pytest.raises(ValueError, match="policy"):
        validate_manifest(missing_title)


def test_manifest_rejects_repository_paths_and_outside_local_dir():
    repository_path = _valid_manifest()
    repository_path["entries"][-1]["path"] = "tests/fixtures/x.pdf"
    with pytest.raises(ValueError, match="repository"):
        validate_manifest(repository_path)

    outside_local_dir = _valid_manifest()
    outside_local_dir["entries"][-1]["path"] = "/tmp/x.pdf"
    with pytest.raises(ValueError):
        validate_manifest(outside_local_dir)


def test_valid_manifest_round_trips_to_corpus_entries():
    entries = validate_manifest(_valid_manifest())
    assert len(entries) == 10
    assert sum(entry.kind == "policy" for entry in entries) == 8
    assert sum(entry.kind == "process" for entry in entries) == 2


def test_download_target_and_verify_local_file(tmp_path, monkeypatch):
    monkeypatch.setattr(graph_corpus, "LOCAL_CORPUS_DIR", tmp_path)

    entry = CorpusEntry(
        id="policy-01",
        title="Policy 1",
        kind="policy",
        sha256=_sha(b"content"),
        url="https://example.invalid/policy-1.pdf",
        path=None,
    )
    target = download_target(entry)
    assert target == tmp_path / "policy-01.pdf"

    target.write_bytes(b"content")
    verify_local_file(entry)

    target.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        verify_local_file(entry)


def test_download_target_for_process_entry_returns_its_path(tmp_path):
    process_path = tmp_path / "process-01.pdf"
    entry = CorpusEntry(
        id="process-01",
        title="Process 1",
        kind="process",
        sha256=_sha(b"process-content"),
        url=None,
        path=str(process_path),
    )
    assert download_target(entry) == process_path


def test_committed_manifest_skeleton_has_no_pdf_beside_it():
    assert list(_FIXTURES_DIR.glob("*.pdf")) == []
