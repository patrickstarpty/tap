"""Exercise the `upload` subcommand's revision polling against a fake `urlopen`.

Loaded via `importlib.util` because the script's filename has hyphens, following the
same pattern `tests/quality/test_quality_graph_01.py` uses for
`scripts/evaluate-quality-graph.py`.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import urllib.error
import urllib.request
from pathlib import Path
from types import ModuleType

from tap.quality import graph_corpus

ROOT = Path(__file__).resolve().parents[5]
SCRIPT = ROOT / "scripts" / "load-graph-real-corpus.py"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("load_graph_real_corpus", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _write_manifest(tmp_path: Path) -> Path:
    entries: list[dict[str, object]] = []
    for index in range(1, 9):
        content = f"policy-{index}".encode()
        (tmp_path / f"policy-{index:02d}.pdf").write_bytes(content)
        entries.append(
            {
                "id": f"policy-{index:02d}",
                "title": f"Policy {index}",
                "kind": "policy",
                "sha256": _sha(content),
                "url": f"https://example.invalid/policy-{index}.pdf",
            }
        )
    for index in range(1, 3):
        content = f"process-{index}".encode()
        target = tmp_path / f"process-{index:02d}.pdf"
        target.write_bytes(content)
        entries.append(
            {
                "id": f"process-{index:02d}",
                "title": f"Process {index}",
                "kind": "process",
                "sha256": _sha(content),
                "path": str(target),
            }
        )
    manifest = {"schemaVersion": "graph-real-corpus-manifest-v1", "entries": entries}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.headers: dict[str, str] = {}

    def read(self, *_args: object) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc_info: object) -> bool:
        return False


def test_cmd_upload_polls_each_source_for_its_revision_id(tmp_path, monkeypatch):
    monkeypatch.setattr(graph_corpus, "LOCAL_CORPUS_DIR", tmp_path)
    manifest_path = _write_manifest(tmp_path)

    requested: list[tuple[str, str]] = []
    uploaded_source_ids: list[str] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float | None = None):
        method = request.get_method()
        url = request.full_url
        requested.append((method, url))
        if method == "POST" and url.endswith("/knowledge/sources"):
            source_id = f"src-{len(uploaded_source_ids):02d}"
            uploaded_source_ids.append(source_id)
            return _FakeResponse(json.dumps({"source": {"sourceId": source_id}}).encode())
        if method == "GET" and "/knowledge/sources/" in url:
            source_id = url.split("/knowledge/sources/")[1].split("?")[0]
            payload = {
                "documents": {"items": [{"status": "ready", "revisionId": f"rev-{source_id}"}]}
            }
            return _FakeResponse(json.dumps(payload).encode())
        if method == "GET" and url.endswith("/knowledge/graph/project"):
            return _FakeResponse(
                json.dumps({"status": "READY", "graphVersion": "graph-v1"}).encode()
            )
        raise AssertionError(f"unexpected request {method} {url}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    module = _script()
    output_path = tmp_path / "corpus.json"
    arguments = argparse.Namespace(
        manifest=manifest_path,
        api="http://fake-api",
        project_id="proj-1",
        output=output_path,
        timeout_seconds=5,
    )

    exit_code = module.cmd_upload(arguments)

    assert exit_code == 0
    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert output["graphVersion"] == "graph-v1"
    sources = {item["sourceId"]: item for item in output["sources"]}
    assert len(sources) == 10
    for source_id, item in sources.items():
        assert item["revisionId"] == f"rev-{source_id}"

    source_detail_urls = [
        url for method, url in requested if method == "GET" and "/knowledge/sources/" in url
    ]
    assert source_detail_urls, "expected per-source polling requests"
    assert all("limit=50" in url for url in source_detail_urls)
    assert all("limit=200" not in url for method, url in requested)


def test_cmd_upload_reports_network_errors_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(graph_corpus, "LOCAL_CORPUS_DIR", tmp_path)
    manifest_path = _write_manifest(tmp_path)

    def failing_urlopen(request: urllib.request.Request, timeout: float | None = None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", failing_urlopen)

    module = _script()
    arguments = argparse.Namespace(
        manifest=manifest_path,
        api="http://fake-api",
        project_id="proj-1",
        output=tmp_path / "corpus.json",
        timeout_seconds=5,
    )

    assert module.cmd_upload(arguments) == 1
    assert not (tmp_path / "corpus.json").exists()
