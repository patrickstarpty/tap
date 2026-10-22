"""Corpus upload scripts send the web app's exact Origin on their POST.

The Tapper API rejects every non-GET request whose `Origin` is not the configured
loopback web origin (`OriginPolicyMiddleware`), so a corpus upload without it
fails with 403 against a real `make demo-dev` stack.
"""

from __future__ import annotations

import importlib.util
import io
import json
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tap.quality import graph_corpus

ROOT = Path(__file__).resolve().parents[5]


def _load(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[urllib.request.Request]:
    seen: list[urllib.request.Request] = []

    def urlopen(request: urllib.request.Request, timeout: float = 0) -> Any:
        seen.append(request)
        return _Response(json.dumps({"source": {"sourceId": "src_1"}}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return seen


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({}, "http://127.0.0.1:5173"),
        ({"TAPPER_WEB_HOST": "localhost", "TAPPER_WEB_PORT": "15173"}, "http://localhost:15173"),
    ],
)
def test_answer_corpus_upload_sends_the_web_origin(
    monkeypatch: pytest.MonkeyPatch, environment: dict[str, str], expected: str
) -> None:
    for key in ("TAPPER_WEB_HOST", "TAPPER_WEB_PORT"):
        monkeypatch.delenv(key, raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    seen = _capture(monkeypatch)
    script = _load("run_answer_golden", "run-answer-golden.py")

    assert script._upload_source("http://127.0.0.1:8000", "p", "doc", b"# Doc\n") == "src_1"
    assert seen[0].get_header("Origin") == expected


def test_graph_corpus_upload_sends_the_web_origin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TAPPER_WEB_HOST", raising=False)
    monkeypatch.delenv("TAPPER_WEB_PORT", raising=False)
    seen = _capture(monkeypatch)
    script = _load("load_graph_real_corpus", "load-graph-real-corpus.py")
    target = tmp_path / "process-01.pdf"
    target.write_bytes(b"pdf")
    entry = graph_corpus.CorpusEntry(
        id="process-01",
        title="Process",
        kind="process",
        sha256="sha256:" + "0" * 64,
        url=None,
        path=None,
    )

    assert script._upload_source("http://127.0.0.1:8000", "p", entry, target) == "src_1"
    assert seen[0].get_header("Origin") == "http://127.0.0.1:5173"
