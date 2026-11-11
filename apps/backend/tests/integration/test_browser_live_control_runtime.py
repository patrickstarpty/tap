"""Owned loopback Chromium, synthetic data, no Docker or model provider."""

import io
import os
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from test_browser_local_runtime_integration import Sink, fixture_site  # noqa: F401
from tap_platform.browser_automation.local import LocalBrowserRuntime

pytestmark = pytest.mark.skipif(
    os.environ.get("TAP_RUN_BROWSER_LOCAL_INTEGRATION") != "1",
    reason="explicit owned local Chromium probe",
)


@pytest.mark.asyncio
async def test_controlled_runtime_handoff_frames_and_trace_isolation(fixture_site):  # noqa: F811
    sink = Sink()
    runtime = LocalBrowserRuntime(
        evidence_sink=sink, project_id="p1", test_target=fixture_site
    )
    handle = await runtime.start_inspector(
        "live1",
        ("http://example.test",),
        60,
        session_id="live-session",
        control_enabled=True,
    )
    agent = {"actor_kind": "agent", "control_epoch": 0}
    try:
        assert handle.cdp_url is None
        assert not (Path(handle.container) / "profile/DevToolsActivePort").exists()
        initial = await runtime.observe(
            handle, {**agent, "type": "navigate", "url": "http://example.test/quote"}
        )
        assert initial.error is None
        human = await runtime.synchronize_control(handle, "human", 1)
        assert human.sequence > initial.sequence
        frame = await runtime.read_frame(handle)
        assert frame["control_epoch"] == 1
        assert frame["media_type"] == "image/jpeg"
        assert len(sink.items) == 0
        action = {
            "actor_kind": "human",
            "actor_expires_at": (datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
            "control_epoch": 1,
            "type": "fill",
            "selector": "#age",
            "value": "35",
            "frame_sequence": frame["frame_sequence"],
            "viewport": frame["viewport"],
        }
        bound = await runtime.preflight_human_action(handle, action)
        filled = await runtime.observe(handle, {**action, **bound})
        assert filled.error is None
        assert filled.extracted["control_receipt"]["target"]["value"] == "textbox"
        resumed = await runtime.synchronize_control(handle, "agent", 2)
        assert resumed.sequence > filled.sequence
        rejected = await runtime.observe(handle, action)
        assert (
            rejected.error
            == "controlled browser request rejected: control_generation_stale"
        )
        agent = {"actor_kind": "agent", "control_epoch": 2}
        await runtime.observe(handle, {**agent, "type": "click", "selector": "button"})
        await runtime.observe(
            handle, {**agent, "type": "read", "selector": "#quote", "name": "premium"}
        )
        finished = await runtime.observe(handle, {**agent, "type": "finish"})
        assert finished.error is None
        assert {item["kind"] for item in finished.evidence} == {
            "screenshot",
            "trace",
            "result",
        }
        trace = next(data for key, data in sink.items.items() if "/trace/" in key)
        with zipfile.ZipFile(io.BytesIO(trace)) as archive:
            events = b"\n".join(
                archive.read(n) for n in archive.namelist() if n.endswith(".trace")
            )
        assert b'"method":"fill"' not in events
    finally:
        assert (await runtime.cancel(handle))["stopped"] is True
    with pytest.raises(ValueError):
        await runtime.read_frame(handle)


@pytest.mark.asyncio
async def test_postwrite_sensitive_flip_returns_no_value_or_trace(tmp_path):
    """The real inspector must not serialize a success after a synchronous flip."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            content = b"""<label>Name<input id="name" oninput="this.type='password'"></label>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    sink = Sink()
    runtime = LocalBrowserRuntime(
        evidence_sink=sink,
        project_id="p1",
        test_target=f"http://127.0.0.1:{server.server_port}",
    )
    handle = None
    try:
        handle = await runtime.start_inspector(
            "flip1",
            ("http://example.test",),
            30,
            session_id="flip-session",
            control_enabled=True,
        )
        actor = {"actor_kind": "agent", "control_epoch": 0}
        await runtime.observe(
            handle, {**actor, "type": "navigate", "url": "http://example.test/"}
        )
        proof = await runtime.observe(
            handle,
            {
                **actor,
                "type": "preflight",
                "operation": "fill",
                "selector": "#name",
            },
        )
        assert proof.extracted["preflight"]["safe"] is True
        marker = "POSTWRITE_VALUE_MUST_NOT_PERSIST"
        result = await runtime.observe(
            handle,
            {
                **actor,
                "type": "fill",
                "selector": "#name",
                "value": marker,
            },
        )
        assert result.error == "controlled action outcome unknown"
        assert result.extracted is None
        assert not result.fields and not result.bounded_snapshot and not result.evidence
        assert marker not in json.dumps(result.__dict__)
        assert not sink.items
        # The controlled agent's traced preflight is discarded BEFORE passing
        # the value to browser evaluation, even on a failed write.
        assert not (Path(handle.container) / "session-trace.zip").exists()
    finally:
        if handle is not None:
            assert (await runtime.cancel(handle))["stopped"] is True
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.asyncio
async def test_human_entry_navigation_is_declared_once_and_select_is_canonical():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    entries = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/entry":
                entries.append(self.path)
            content = b"""<label>Plan<select data-testid="plan"><option value="basic">Basic</option><option value="pro">Professional</option></select></label>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runtime = LocalBrowserRuntime(test_target=f"http://127.0.0.1:{server.server_port}")
    handle = None
    try:
        handle = await runtime.start_inspector(
            "entry1",
            ("http://example.test",),
            30,
            session_id="entry-session",
            control_enabled=True,
            network_mode="declared_origins",
        )
        await runtime.synchronize_control(handle, "human", 1)
        frame = await runtime.read_frame(handle)
        actor = {
            "actor_kind": "human",
            "control_epoch": 1,
            "actor_expires_at": (datetime.now(UTC) + timedelta(seconds=20)).isoformat(),
        }
        entry = {
            **actor,
            "type": "navigate",
            "url": "http://example.test/entry",
            "frame_sequence": frame["frame_sequence"],
            "viewport": frame["viewport"],
        }
        for url in (
            "http://other.test/entry",
            "javascript:alert(1)",
            "http://user:pass@example.test/entry",
        ):
            with pytest.raises(ValueError):
                await runtime.observe(handle, {**entry, "url": url})
        assert entries == []
        expired = await runtime.observe(
            handle,
            {
                **entry,
                "actor_expires_at": (
                    datetime.now(UTC) - timedelta(seconds=1)
                ).isoformat(),
            },
        )
        assert expired.error
        assert entries == []
        navigated = await runtime.observe(handle, entry)
        assert navigated.error is None
        receipt = navigated.extracted["control_receipt"]
        assert receipt["target"] is None
        assert receipt["args"] == {"url": "http://example.test/entry"}
        assert receipt["pre_observation_sequence"] >= 1
        assert entries == ["/entry"]
        frame = await runtime.read_frame(handle)
        selected = await runtime.observe(
            handle,
            {
                **actor,
                "type": "select",
                "selector": "select",
                "value": "Professional",
                "frame_sequence": frame["frame_sequence"],
                "viewport": frame["viewport"],
            },
        )
        assert selected.error is None
        assert selected.extracted["control_receipt"]["args"] == {"value": "pro"}
        duplicate = await runtime.observe(handle, entry)
        assert duplicate.error
        assert entries == ["/entry"]
    finally:
        if handle is not None:
            assert (await runtime.cancel(handle))["stopped"] is True
        server.shutdown()
        server.server_close()
        thread.join()
