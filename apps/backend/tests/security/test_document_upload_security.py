"""Reject hostile multipart before unrestricted FastAPI spooling."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import validation_http_services
from fastapi import UploadFile
from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app

ORIGIN = "http://127.0.0.1:15175"
PATH = "/api/v1/projects/tapper-demo/knowledge/documents"


def client():
    return TestClient(
        create_app(validation_http_services(), allowed_origins=frozenset({ORIGIN})),
        headers={"Origin": ORIGIN},
    )


@pytest.mark.parametrize(
    "body",
    [
        b'--x\r\nContent-Disposition: form-data; name="upload"; filename="a.txt"\r\n'
        b"Content-Type: text/plain\r\n\r\nx",
        b'--x\r\nContent-Disposition: form-data; name="upload"; filename="a.txt"\r\nX-Padding: '
        + b"x" * 8193
        + b"\r\n\r\nx\r\n--x--\r\n",
    ],
    ids=["truncated-boundary", "oversized-part-headers"],
)
def test_upload_rejects_truncated_boundary_and_oversized_part_headers(body):
    response = client().post(
        PATH, content=body, headers={"Content-Type": "multipart/form-data; boundary=x"}
    )
    assert response.status_code == 400
    assert response.json()["type"] == "https://tap.example/problems/unsupported-document"
    assert response.json()["correlationId"] == response.headers["x-correlation-id"]


def test_upload_rejects_declared_total_before_spooling(monkeypatch):
    import starlette.formparsers

    def forbidden(*args, **kwargs):
        pytest.fail("spool was created before declared body bound")

    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", forbidden)
    response = client().post(
        PATH,
        content=b"--x\r\n",
        headers={
            "Content-Type": "multipart/form-data; boundary=x",
            "Content-Length": str(26 * 1024 * 1024),
        },
    )
    assert response.status_code == 413


def test_upload_authority_field_stays_forbidden():
    response = client().post(
        PATH, files={"actor": (None, "evil"), "upload": ("x.txt", b"hello", "text/plain")}
    )
    assert response.status_code == 403


def test_upload_hostile_fixture_builder_is_deterministic(tmp_path):
    root = Path(__file__).resolve().parents[4]
    builder = root / "scripts/build-hostile-document-fixtures.py"
    assert builder.is_file(), "deterministic hostile fixture builder is missing"
    for name in ("first", "second"):
        subprocess.run([sys.executable, str(builder), str(tmp_path / name)], check=True)
    first = json.loads((tmp_path / "first/manifest.json").read_text())
    second = json.loads((tmp_path / "second/manifest.json").read_text())
    assert first == second
    assert {
        "macro.docx",
        "external.docx",
        "traversal.docx",
        "ratio.docx",
        "pages.pdf",
        "encrypted.pdf",
        "valid.docx",
    } <= {row["name"] for row in first}


@pytest.mark.parametrize(
    "name",
    [
        "macro.docx",
        "ole.docx",
        "script.docx",
        "external.docx",
        "absolute.docx",
        "ratio.docx",
        "oversize-entry.docx",
        "entries.docx",
        "entity.docx",
        "pages.pdf",
        "fake.pdf",
        "fake.txt",
        "fake.docx",
    ],
)
def test_parser_rejects_finite_inert_security_envelopes(name, tmp_path):
    """These bounded synthetic envelopes contain no executable exploit or resource probe."""
    from tap.modules.knowledge.adapters.document_parsers import ParserRegistry
    from tap.modules.knowledge.domain.documents import (
        DocumentParseRejected,
        DocumentSource,
        MediaType,
    )

    root = Path(__file__).resolve().parents[4]
    spec = importlib.util.spec_from_file_location(
        "fixture_builder", root / "scripts/build-hostile-document-fixtures.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build(tmp_path)
    row = next(
        row for row in json.loads((tmp_path / "manifest.json").read_text()) if row["name"] == name
    )
    with pytest.raises(DocumentParseRejected) as caught:
        ParserRegistry().parse(
            DocumentSource(name, MediaType(row["mediaType"]), (tmp_path / name).read_bytes())
        )
    assert caught.value.code == row["expectedError"]


def test_upload_rejects_data_after_terminal_boundary():
    body = (
        b'--x\r\nContent-Disposition: form-data; name="upload"; filename="a.txt"\r\n'
        b"Content-Type: text/plain\r\n\r\nx\r\n--x--\r\nforbidden"
    )
    response = client().post(
        PATH, content=body, headers={"Content-Type": "multipart/form-data; boundary=x"}
    )
    assert response.status_code == 400


def test_parser_rejects_utf16_doctype_before_docx_library(tmp_path):
    from tap.modules.knowledge.adapters.document_parsers import ParserRegistry
    from tap.modules.knowledge.domain.documents import (
        DocumentParseRejected,
        DocumentSource,
        MediaType,
    )

    root = Path(__file__).resolve().parents[4]
    spec = importlib.util.spec_from_file_location(
        "fixture_builder", root / "scripts/build-hostile-document-fixtures.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    content = module.docx(
        {
            "word/document.xml": (
                '<?xml version="1.0" encoding="UTF-16"?>'
                '<!DOCTYPE x [<!ENTITY bad SYSTEM "file:///forbidden">]><x>&bad;</x>'
            ).encode("utf-16")
        }
    )
    with pytest.raises(DocumentParseRejected, match="invalid-document"):
        ParserRegistry().parse(DocumentSource("entity.docx", MediaType.DOCX, content))


@pytest.mark.parametrize(
    "tail", [b"--x\r\ninvalid-header\r\n\r\nx\r\n--x--\r\n", b"wrong boundary"]
)
def test_upload_malformed_multipart_is_closed_problem(tail):
    response = client().post(
        PATH, content=tail, headers={"Content-Type": "multipart/form-data; boundary=x"}
    )
    assert response.status_code == 400
    assert response.json()["correlationId"] == response.headers["x-correlation-id"]


def test_upload_closes_created_spool_on_truncated_body(monkeypatch):
    import starlette.formparsers

    original = starlette.formparsers.SpooledTemporaryFile
    files = []

    def spool(*args, **kwargs):
        result = original(*args, **kwargs)
        files.append(result)
        return result

    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", spool)
    body = (
        b'--x\r\nContent-Disposition: form-data; name="upload"; filename="a.txt"\r\n'
        b"Content-Type: text/plain\r\n\r\nx"
    )
    assert (
        client()
        .post(PATH, content=body, headers={"Content-Type": "multipart/form-data; boundary=x"})
        .status_code
        == 400
    )
    assert files and all(file.closed for file in files)


def test_upload_chunked_actual_bytes_exceeding_bound_close_spool(monkeypatch):
    import starlette.formparsers

    from tap.interfaces.http import multipart

    original = starlette.formparsers.SpooledTemporaryFile
    files = []

    def spool(*args, **kwargs):
        result = original(*args, **kwargs)
        files.append(result)
        return result

    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", spool)
    monkeypatch.setattr(multipart, "MAX_FILE", 1024)

    def chunks():
        yield (
            b'--x\r\nContent-Disposition: form-data; name="upload"; filename="a.txt"\r\n'
            b"Content-Type: text/plain\r\n\r\n"
        )
        yield b"x" * 1025
        yield b"\r\n--x--\r\n"

    response = client().post(
        PATH, content=chunks(), headers={"Content-Type": "multipart/form-data; boundary=x"}
    )
    assert response.status_code == 413
    assert files and all(file.closed for file in files)


def test_upload_wrong_origin_rejects_before_spool_or_body(monkeypatch):
    import starlette.formparsers

    def forbidden(*args, **kwargs):
        pytest.fail("untrusted Origin reached spooling")

    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", forbidden)

    def body():
        pytest.fail("untrusted Origin consumed body")
        yield b""

    response = client().post(
        PATH,
        content=body(),
        headers={
            "Origin": "http://untrusted.invalid",
            "Content-Type": "multipart/form-data; boundary=x",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [b"", b"forbidden\r\n--x--\r\n", b"--x--\r\n"])
@pytest.mark.parametrize("split", ["single", "after-terminal", "inside-terminal"])
async def test_real_upload_route_rejects_every_first_terminal_epilogue(suffix, split):
    import httpx
    from fastapi import FastAPI, File
    from starlette.responses import JSONResponse

    from tap.interfaces.http.multipart import BoundedUploadRoute
    from tap.interfaces.http.problems import InvalidDocumentUpload

    app = FastAPI()
    app.router.route_class = BoundedUploadRoute
    calls = []

    @app.exception_handler(InvalidDocumentUpload)
    async def rejected(_request, _error):
        return JSONResponse({"error": "closed-upload-rejection"}, status_code=400)

    @app.post("/upload")
    async def upload_document(upload: UploadFile = File(...)):
        calls.append(True)
        return {"bytes": len(await upload.read())}

    valid = (
        b'--x\r\nContent-Disposition: form-data; name="upload"; filename="a.txt"\r\n'
        b"Content-Type: text/plain\r\n\r\na\r\n--x--\r\n"
    )
    chunks = (
        [valid + suffix]
        if split == "single"
        else [valid, suffix]
        if split == "after-terminal"
        else [valid[:-4], valid[-4:] + suffix]
    )

    async def stream():
        for chunk in chunks:
            yield chunk

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.post(
            "/upload", content=stream(), headers={"Content-Type": "multipart/form-data; boundary=x"}
        )
    assert response.status_code == (400 if suffix else 200)
    assert calls == ([] if suffix else [True])
