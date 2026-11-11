"""Real service/worker/generated Chromium upload/download and published replay."""

import asyncio
import base64
from dataclasses import replace
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import threading

import pytest
from sqlalchemy import create_engine

from tap_platform.access import AccessPrincipal
from tap_platform.browser_automation.local import LocalBrowserRuntime
from tap_platform.browser_automation.objects import (
    BrowserEvidenceSink,
    BrowserObjectStore,
)
from tap_platform.browser_automation.service import BrowserService
from tap_platform.browser_automation.store import (
    BrowserStore,
    metadata as browser_metadata,
)
from tap_platform.browser_automation.worker import BrowserWorker
from tap_platform.lca.service import LcaService
from tap_platform.lca.store import LcaNotFound, LcaStore, metadata

pytestmark = pytest.mark.skipif(
    os.getenv("TAP_RUN_LCA_LOCAL_BROWSER") != "1",
    reason="local Chromium opt-in required",
)


@pytest.fixture
def file_site():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "http://undeclared.invalid/download")
                self.end_headers()
                return
            if self.path.startswith("/download"):
                content = (
                    b"x" * 5_000_001
                    if self.path.endswith("large")
                    else b"verified download bytes"
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header(
                    "Content-Disposition", 'attachment; filename="fixture-result.txt"'
                )
            else:
                content = b"""<input data-testid="file" type="file" onchange="this.files[0].text().then(value=>document.querySelector('output').textContent=value+':'+this.files[0].type)"><output data-testid="content"></output><a data-testid="download" href="/download" download>Save file</a><a data-testid="large" href="/download-large" download>Save large file</a><button data-testid="none">No download</button><a data-testid="redirect" href="/redirect" download>Undeclared redirect</a>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def principal():
    return AccessPrincipal(
        project_id="files-project",
        principal_id="alice",
        principal_type="user",
        audience="tap-lca",
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        enabled=True,
        actions=frozenset(
            {
                "lca.assets.create",
                "lca.assets.read",
                "lca.flows.create",
                "lca.flows.read",
                "lca.drafts.save",
                "lca.drafts.generate",
                "lca.runs.create",
                "lca.runs.read",
                "lca.evidence.read",
                "lca.releases.publish",
                "lca.releases.read",
            }
        ),
    )


def definition(asset, download_target="download"):
    return {
        "schema_version": "lca-debug-v1",
        "allowed_origins": ["http://example.test"],
        "parameters": [],
        "targets": [
            {"id": name, "kind": "test_id", "value": name}
            for name in ("file", "content", "download", "large", "none", "redirect")
        ],
        "steps": [
            {
                "id": "open",
                "operation": "navigate",
                "arguments": {"url": "http://example.test/files"},
                "timeout_ms": 3000,
            },
            {
                "id": "upload",
                "operation": "upload_file",
                "target_id": "file",
                "arguments": {"asset_id": asset["id"], "sha256": asset["sha256"]},
                "timeout_ms": 3000,
            },
            {
                "id": "verify",
                "operation": "assert_text_exact",
                "target_id": "content",
                "arguments": {"text": "selected immutable bytes:text/plain"},
                "timeout_ms": 3000,
            },
            {
                "id": "download",
                "operation": "download_file",
                "target_id": download_target,
                "arguments": {},
                "timeout_ms": 1500 if download_target in {"none", "redirect"} else 5000,
            },
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("download_target", ["download", "large", "none", "redirect"])
async def test_actual_file_bytes_replay_and_download_evidence(
    tmp_path, file_site, download_target
):
    engine = create_engine(f"sqlite:///{tmp_path / 'files.db'}")
    metadata.create_all(engine)
    browser_metadata.create_all(engine)
    browser = BrowserService(
        BrowserStore(engine), BrowserObjectStore(tmp_path / "objects")
    )
    service = LcaService(LcaStore(engine), browser)
    who = principal()
    asset = service.create_asset(
        who,
        "upload",
        "selected.txt",
        base64.b64encode(b"selected immutable bytes").decode(),
    )
    raw = definition(asset, download_target)
    flow = service.create_flow(who, "flow", "File fixture", raw)
    source = service.generate_draft(who, flow["id"]).source
    assert "c2VsZWN0ZWQgaW1tdXRhYmxlIGJ5dGVz" not in source
    run = service.debug_run(who, flow["id"], 1, {}, "debug")
    errors = []

    class DiagnosticRuntime(LocalBrowserRuntime):
        async def _execute_work(self, *args, **kwargs):
            try:
                return await super()._execute_work(*args, **kwargs)
            except Exception as exc:
                errors.append(repr(exc))
                raise

        async def _cleanup(self, resources):
            try:
                result = await super()._cleanup(resources)
                if not result:
                    errors.append("Cleanup could not confirm termination")
                return result
            except Exception as exc:
                errors.append(repr(exc))
                raise

    runtime = DiagnosticRuntime(
        evidence_sink=BrowserEvidenceSink(browser.store, browser.objects),
        project_id=who.project_id,
        test_target=file_site,
    )
    worker = BrowserWorker(browser, runtime, who.project_id)

    async def finish(run):
        await worker.tick()
        async with asyncio.timeout(45):
            while True:
                await asyncio.sleep(0.05)
                current = service.get_run(who, run["id"])
                if current["execution"]["status"] in {
                    "passed",
                    "failed",
                    "unknown",
                    "canceled",
                }:
                    return current
                await worker.tick()

    result = await finish(run)
    expected = "passed" if download_target == "download" else "failed"
    assert result["execution"]["status"] == expected, (errors, result["execution"])
    assert result["canonical_results"][-1]["status"] == expected, (
        errors,
        result["execution"],
    )
    downloads = [
        item for item in result["execution"]["evidence"] if item["kind"] == "download"
    ]
    if download_target != "download":
        assert downloads == []
        return
    assert len(downloads) == 1
    evidence_id = downloads[0]["object_ref"]
    row, data = service.read_evidence(who, run["id"], evidence_id)
    assert data == b"verified download bytes"
    assert row["filename"] == "fixture-result.txt"
    for outsider in (
        replace(who, principal_id="bob"),
        replace(who, project_id="other-project"),
    ):
        with pytest.raises(LcaNotFound):
            service.read_evidence(outsider, run["id"], evidence_id)
    release = service.publish_release(who, flow["id"], 1, run["id"], "publish")
    replacement = service.create_asset(
        who, "new-upload", "new.txt", base64.b64encode(b"different bytes").decode()
    )
    service.save_draft(who, flow["id"], 1, definition(replacement))
    assert service.debug_run(who, flow["id"], 1, {}, "debug")["id"] == run["id"]
    replay = service.run_release(who, release["id"], {}, "published-replay")
    replay_result = await finish(replay)
    assert replay_result["execution"]["status"] == "passed", replay_result
    assert (
        replay_result["definition_snapshot"]["definition"]["steps"][1]["arguments"][
            "asset_id"
        ]
        == asset["id"]
    )
    assert (
        service.run_release(who, release["id"], {}, "published-replay")["id"]
        == replay["id"]
    )
    assert not runtime._local_processes
