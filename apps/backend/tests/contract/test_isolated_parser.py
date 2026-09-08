"""Closed parser transport and owned-container lifecycle contracts."""

from __future__ import annotations

import asyncio
import importlib
import json
import struct
from pathlib import Path

import pytest

from tap.modules.knowledge.domain.documents import (
    PARSER_VERSION,
    DocumentId,
    DocumentSource,
    MediaType,
    canonical_sha256,
    revision_id_for,
)


def protocol():
    return importlib.import_module("tap.modules.knowledge.adapters.parser_protocol")


def source():
    content = b"# Small\nA retained fact."
    document = DocumentId("doc_" + "a" * 32)
    return DocumentSource(
        "small.md",
        MediaType.MARKDOWN,
        content,
        document,
        revision_id_for(document, canonical_sha256(content), PARSER_VERSION),
    )


@pytest.mark.asyncio
async def test_parser_complete_frame_accepts_normal_eof():
    module = protocol()
    reader = asyncio.StreamReader()
    reader.feed_data(module.encode_request(source(), "a" * 32))
    reader.feed_eof()
    request_id, parsed = await module.read_request(reader)
    assert request_id == "a" * 32
    assert parsed == source()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\x00\x00",
        struct.pack("!I", 4097),
        struct.pack("!I", 17) + b'{"v":1,"v":1}',
    ],
)
async def test_parser_rejects_truncated_oversize_or_duplicate_header(payload):
    module = protocol()
    reader = asyncio.StreamReader()
    reader.feed_data(payload)
    reader.feed_eof()
    with pytest.raises(module.ParserProtocolError):
        await module.read_request(reader)


@pytest.mark.asyncio
async def test_parser_rejects_changed_content_digest():
    module = protocol()
    frame = module.encode_request(source(), "a" * 32)
    reader = asyncio.StreamReader()
    reader.feed_data(frame[:-1] + b"?")
    reader.feed_eof()
    with pytest.raises(module.ParserProtocolError):
        await module.read_request(reader)


def test_parser_port_is_async():
    import inspect

    from tap.modules.knowledge.ports import documents

    assert hasattr(documents, "DocumentParserPort"), "async isolated parser Port is missing"
    assert inspect.iscoroutinefunction(documents.DocumentParserPort.parse)


def test_e2e_manifest_preserves_restart_and_security_journeys():
    root = Path(__file__).resolve().parents[4]
    manifest = root / "scripts/tapper-e2e-specs.json"
    assert manifest.is_file(), "closed E2E manifest is missing"
    assert json.loads(manifest.read_text()) == {
        "journey": ["tests/e2e/tapper.spec.ts", "tests/e2e/knowledge-upload-security.spec.ts"],
        "app-restart": ["tests/e2e/persistence.spec.ts"],
        "compose-restart": ["tests/e2e/persistence.spec.ts"],
    }


def test_parser_build_verify_fails_closed_without_receipt(tmp_path):
    import subprocess

    root = Path(__file__).resolve().parents[4]
    script = root / "scripts/build-tapper-parser.sh"
    assert script.is_file(), "pinned minimal parser image builder is missing"
    result = subprocess.run(
        ["bash", str(script), "verify", "--receipt", str(tmp_path / "missing.json")],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "receipt" in result.stderr.lower()


def test_e2e_manifest_rejects_missing_spec_and_native_skip(tmp_path):
    root = Path(__file__).resolve().parents[4]
    import importlib.util

    script = root / "scripts/tapper_e2e_report.py"
    assert script.is_file(), "native E2E result validator is missing"
    spec = importlib.util.spec_from_file_location("e2e_report", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    incomplete = {"stats": {"expected": 1, "unexpected": 0, "flaky": 0, "skipped": 0}, "suites": []}
    with pytest.raises(ValueError):
        module.project_report(
            "journey",
            incomplete,
            b"native",
            json.loads((root / "scripts/tapper-e2e-specs.json").read_text()),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data,expected", [(b"", None), (b"\x00", "reject"), (b"\x00\x00\x00\x02{", "reject")]
)
async def test_parser_control_distinguishes_clean_half_close_from_truncation(data, expected):
    module = protocol()
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    if expected is None:
        assert await module.read_control(reader) is None
    else:
        with pytest.raises(module.ParserProtocolError):
            await module.read_control(reader)


def native_journey():
    return {
        "stats": {"expected": 2, "unexpected": 0, "flaky": 0, "skipped": 0},
        "suites": [
            {
                "specs": [
                    {
                        "file": name,
                        "title": "fixed " + name,
                        "tests": [
                            {
                                "projectName": "chromium",
                                "expectedStatus": "passed",
                                "status": "expected",
                                "results": [{"status": "passed", "retry": 0}],
                            }
                        ],
                    }
                ]
            }
            for name in ["tapper.spec.ts", "knowledge-upload-security.spec.ts"]
        ],
    }


@pytest.mark.parametrize(
    "drift", ["skip", "xfail", "flaky", "retry", "empty", "missing", "unexpected", "count", "error"]
)
def test_native_e2e_report_rejects_nonpassing_or_incomplete_evidence(drift):
    from scripts.tapper_e2e_report import project_report

    root = Path(__file__).resolve().parents[4]
    native = native_journey()
    test = native["suites"][0]["specs"][0]["tests"][0]
    if drift == "skip":
        test["results"][0]["status"] = "skipped"
    if drift == "xfail":
        test["expectedStatus"] = "failed"
    if drift == "flaky":
        test["status"] = "flaky"
    if drift == "retry":
        test["results"][0]["retry"] = 1
    if drift == "empty":
        native["suites"] = []
    if drift == "missing":
        native["suites"].pop()
    if drift == "unexpected":
        native["suites"][0]["specs"][0]["file"] = "other.spec.ts"
    if drift == "count":
        native["stats"]["expected"] = 3
    if drift == "error":
        native["errors"] = [{"message": "private error"}]
    with pytest.raises(ValueError):
        project_report(
            "journey",
            native,
            json.dumps(native).encode(),
            json.loads((root / "scripts/tapper-e2e-specs.json").read_text()),
        )


def test_native_e2e_projection_preserves_identity_and_discards_content():
    from scripts.tapper_e2e_report import project_report

    root = Path(__file__).resolve().parents[4]
    native = native_journey()
    native["config"] = {"metadata": "private canary"}
    result = project_report(
        "journey",
        native,
        json.dumps(native).encode(),
        json.loads((root / "scripts/tapper-e2e-specs.json").read_text()),
    )
    assert {row["file"] for row in result["specifications"]} == {
        "tests/e2e/tapper.spec.ts",
        "tests/e2e/knowledge-upload-security.spec.ts",
    }
    assert result["counts"]["passed"] == 2
    assert "private canary" not in json.dumps(result)


@pytest.mark.parametrize("suffix", ["<skipped/>", "<failure/>", "<error/>", "<rerunFailure/>"])
def test_native_pytest_projection_rejects_skip_failure_or_retry(suffix):
    from scripts.tapper_e2e_report import project_pytest

    raw = (
        '<testsuites><testsuite tests="1" errors="0" failures="0" skipped="0">'
        '<testcase classname="tests.integration.test_tapper_persistence_restart" '
        'name="test_exact_tapper_state_survives_application_and_compose_restarts">'
        + suffix
        + "</testcase></testsuite></testsuites>"
    ).encode()
    with pytest.raises(ValueError):
        project_pytest(raw)


@pytest.mark.asyncio
async def test_unreachable_daemon_records_unresolved_and_rejects_new_work(tmp_path):
    from tap.entrypoints.tapper_parser_worker import Supervisor

    class Unavailable(Supervisor):
        async def command(self, args, timeout=5):
            raise ValueError("closed daemon unavailable")

    supervisor = Unavailable(
        Path.cwd(), tmp_path, "tap-parser-unit", "sha256:" + "a" * 64, ["unused"]
    )
    assert not await supervisor.cleanup()
    assert supervisor.unresolved
    assert json.loads((tmp_path / "unresolved.json").read_text()) == {
        "owner": supervisor.owner,
        "unresolved": True,
    }


@pytest.mark.asyncio
async def test_unknown_compose_service_field_rejects_before_create(tmp_path):
    from tap.entrypoints.tapper_parser_worker import Supervisor
    from tap.modules.knowledge.adapters.parser_protocol import read_result

    calls = []

    class Changed(Supervisor):
        async def command(self, args, timeout=5):
            calls.append(args)
            if "config" in args:
                return json.dumps({"services": {"tap-parser": {"unknown-unsafe-field": True}}})
            if "ps" in args:
                return ""
            raise AssertionError("unexpected Docker mutation")

    supervisor = Changed(Path.cwd(), tmp_path, "tap-parser-unit", "sha256:" + "a" * 64, ["unused"])
    result = await supervisor.run_job("a" * 32, source())
    reader = asyncio.StreamReader()
    reader.feed_data(result)
    reader.feed_eof()
    assert (await read_result(reader, "a" * 32))[0] == "parser-unavailable"
    assert not any("create" in call for call in calls)


@pytest.mark.asyncio
async def test_parser_cli_output_limit_still_reaps_pipe_backpressure(tmp_path):
    import sys

    from tap.entrypoints.tapper_parser_worker import Supervisor

    supervisor = Supervisor(
        Path.cwd(), tmp_path, "tap-parser-unit", "sha256:" + "a" * 64, ["unused"]
    )
    with pytest.raises(ValueError, match="output exceeded"):
        await asyncio.wait_for(
            supervisor.command(
                [
                    sys.executable,
                    "-c",
                    'import sys,time; sys.stdout.write("x"*1048576); '
                    "sys.stdout.flush(); time.sleep(5)",
                ]
            ),
            timeout=2,
        )


def test_parser_state_preserves_deployment_association_and_exclusive_lock(tmp_path):
    from tap.entrypoints.tapper_parser_worker import Supervisor, state_lock

    state = tmp_path / "owned"
    state.mkdir(mode=0o700)
    with state_lock(state):
        first = Supervisor(Path.cwd(), state, "tap-parser-unit", "sha256:" + "a" * 64, ["unused"])
        with pytest.raises((BlockingIOError, ValueError)):
            with state_lock(state):
                pass
    second = Supervisor(Path.cwd(), state, "tap-parser-unit", "sha256:" + "a" * 64, ["unused"])
    assert second.owner == first.owner
    assert json.loads((state / "association.json").read_text()) == {
        "owner": first.owner,
        "project": "tap-parser-unit",
        "imageId": "sha256:" + "a" * 64,
    }
    with pytest.raises(ValueError):
        Supervisor(Path.cwd(), state, "tap-other-project", "sha256:" + "a" * 64, ["unused"])
    with pytest.raises(ValueError):
        Supervisor(Path.cwd(), state, "tap-parser-unit", "sha256:" + "b" * 64, ["unused"])


@pytest.mark.asyncio
async def test_parser_cleanup_requires_second_empty_owned_scan_and_invalidates_old_proof(tmp_path):
    from tap.entrypoints.tapper_parser_worker import Supervisor

    class Appearing(Supervisor):
        calls = 0

        async def owned(self):
            self.calls += 1
            return [] if self.calls == 1 else ["a" * 64]

    supervisor = Appearing(
        Path.cwd(), tmp_path, "tap-parser-unit", "sha256:" + "a" * 64, ["unused"]
    )
    (tmp_path / "cleanup.json").write_text('{"stale":true}')
    assert not await supervisor.cleanup()
    assert not (tmp_path / "cleanup.json").exists()
    assert (tmp_path / "unresolved.json").exists()


def test_parser_lock_rejects_parallel_start_without_erasing_readiness(tmp_path):
    import subprocess
    import sys

    from tap.entrypoints.tapper_parser_worker import state_lock

    (tmp_path / "ready-pid").write_text("stale-pid")
    with state_lock(tmp_path):
        child = subprocess.run(
            [
                sys.executable,
                "-m",
                "tap.entrypoints.tapper_parser_worker",
                "--state-dir",
                str(tmp_path),
                "--project",
                "tap-parser-unit",
                "--cleanup-only",
            ],
            capture_output=True,
            timeout=5,
        )
        assert child.returncode != 0
        assert (tmp_path / "ready-pid").read_text() == "stale-pid"


@pytest.mark.asyncio
async def test_parser_locked_start_invalidates_stale_socket_and_readiness(tmp_path, monkeypatch):
    import socket

    from tap.entrypoints.tapper_parser_worker import serve, socket_path, state_lock

    path = socket_path(tmp_path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(path))
    (tmp_path / "ready-pid").write_text("123")

    async def unavailable(*args, **kwargs):
        raise ValueError("closed unavailable")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unavailable)
    try:
        with state_lock(tmp_path), pytest.raises(ValueError, match="closed unavailable"):
            await serve(tmp_path, "tap-parser-unit", False)
        assert not path.exists()
        assert not (tmp_path / "ready-pid").exists()
    finally:
        server.close()
        path.unlink(missing_ok=True)


def test_parser_short_socket_binding_is_readonly_and_private(tmp_path):
    from tap.entrypoints.tapper_parser_worker import private_directory, socket_path

    state = tmp_path / ("long-state" * 15)
    path = socket_path(state)
    assert len(str(path).encode()) < 100
    assert path == socket_path(state)
    assert path != socket_path(state / "other")
    assert not state.exists()
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        private_directory(link)


def test_parser_identical_association_never_reopens_live_record(tmp_path, monkeypatch):
    from tap.entrypoints.tapper_parser_worker import Supervisor, state_lock

    with state_lock(tmp_path):
        first = Supervisor(
            Path.cwd(), tmp_path, "tap-parser-unit", "sha256:" + "a" * 64, ["unused"]
        )
        record = tmp_path / "association.json"
        previous = record.read_bytes()
        original_stat = record.stat()
        original_write = Path.write_text

        def interrupted(path, data, *args, **kwargs):
            if path == record:
                with path.open("w"):
                    pass
                raise OSError("interrupted live metadata write")
            return original_write(path, data, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", interrupted)
        second = Supervisor(Path.cwd(), tmp_path, "tap-parser-unit", first.image, ["unused"])
        assert second.owner == first.owner
        assert record.read_bytes() == previous
        assert (record.stat().st_ino, record.stat().st_mtime_ns) == (
            original_stat.st_ino,
            original_stat.st_mtime_ns,
        )


@pytest.mark.parametrize("failure", ["write", "replace"])
def test_parser_interrupted_association_replace_preserves_old_binding(
    tmp_path, monkeypatch, failure
):
    import os

    from tap.entrypoints.tapper_parser_worker import Supervisor, state_lock, write_association

    with state_lock(tmp_path):
        first = Supervisor(
            Path.cwd(), tmp_path, "tap-parser-unit", "sha256:" + "a" * 64, ["unused"]
        )
        record = tmp_path / "association.json"
        previous = record.read_bytes()
        replacement = {
            "owner": first.owner,
            "project": first.project,
            "imageId": "sha256:" + "b" * 64,
        }
        invoked = []

        def interrupted(*args):
            invoked.append(True)
            if failure == "replace":
                temporary, destination = map(Path, args)
                assert temporary.parent == record.parent and destination == record
                info = temporary.lstat()
                assert info.st_uid == os.getuid() and info.st_mode & 0o777 == 0o600
                assert json.loads(temporary.read_text()) == replacement
            raise OSError("interrupted before atomic replacement")

        with monkeypatch.context() as patch:
            patch.setattr(os, "fsync" if failure == "write" else "replace", interrupted)
            with pytest.raises(OSError, match="before atomic replacement"):
                write_association(tmp_path, replacement)
        assert invoked
        assert record.read_bytes() == previous
        restored = Supervisor(Path.cwd(), tmp_path, first.project, first.image, ["unused"])
        assert restored.owner == first.owner
        write_association(tmp_path, replacement)
        assert json.loads(record.read_text()) == replacement
        assert not list(tmp_path.glob(".association-*"))
