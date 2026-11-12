import asyncio
import hashlib
import importlib.util
import os
import shutil
import signal
import sys
import time
from pathlib import Path

import pytest

MODULE = "tap.modules.workflows.adapters.local_opencode"
ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.asyncio
@pytest.mark.parametrize("stopped_serve", [False, True])
async def test_local_coding_uses_private_environment_and_owned_processes(
    tmp_path, monkeypatch, stopped_serve
):
    assert importlib.util.find_spec(MODULE) is not None, "native coding adapter is missing"
    from tap.modules.workflows.adapters.local_opencode import LocalCodingContainer
    from tap.modules.workflows.adapters.opencode_runtime import CodingConfig

    if sys.platform != "darwin" or not shutil.which("sandbox-exec"):
        pytest.skip("native Mac sandbox test")
    node = Path(shutil.which("node"))
    binary = tmp_path / "opencode"
    binary.write_text("""#!/usr/bin/env node
const http = require('node:http');
if (process.argv.includes('--version')) { console.log('1.19.5'); process.exit(0); }
const paths = Object.fromEntries(['/session','/session/{sessionID}/message',
 '/session/{sessionID}/prompt_async','/session/{sessionID}/children',
 '/session/{sessionID}/abort','/event'].map(p => [p,{}]));
http.createServer((req,res) => {
 res.setHeader('Content-Type','application/json');
 res.end(JSON.stringify(req.url === '/doc' ? {openapi:'3.1.0',paths} : [{id:'tap_browser'}]));
}).listen(Number(process.argv.at(-1)),'127.0.0.1');
""")
    binary.chmod(0o700)
    mailbox = tmp_path / "mailbox"
    mailbox.mkdir(mode=0o700)
    for folder in ("requests", "responses", "acks"):
        (mailbox / folder).mkdir(mode=0o700)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-inherit")
    monkeypatch.setenv("OPENCODE_CONFIG", "/unrelated/config")
    adapter = LocalCodingContainer(
        CodingConfig.for_test(),
        mailbox,
        opencode_binary=binary,
        node_binary=node,
        client_path=ROOT / "deploy/browser/opencode-client.mjs",
    )
    config = {
        "model": "litellm/test",
        "plugin": ["file:///opt/tapper/browser-tools.mjs"],
        "provider": {"litellm": {"options": {"baseURL": "http://127.0.0.1:39001/v1"}}},
    }
    try:
        await adapter.start(config, "synthetic-shim-token", 30)
        workspace = adapter.workspace
        assert workspace.exists() and workspace.stat().st_mode & 0o077 == 0
        data = b'{"value":"frozen"}'
        await adapter.set_frozen_input(data, hashlib.sha256(data).hexdigest())
        await adapter.set_goal("Read /workspace/frozen-inputs.json; save /workspace/flow.spec.ts")
        assert (workspace / "frozen-inputs.json").read_bytes() == data
        assert str(workspace / "flow.spec.ts") in (workspace / "goal.txt").read_text()
        (workspace / "flow.spec.ts").write_text("const result = 42;")
        assert (await adapter.collect())["script_sha256"] == hashlib.sha256(
            b"const result = 42;"
        ).hexdigest()
        (workspace / "flow.spec.ts").write_text("")
        with pytest.raises(RuntimeError, match="invalid generated script") as diagnostic:
            await adapter.collect()
        assert "synthetic-shim-token" not in str(diagnostic.value)
        (workspace / "tool-diagnostics.json").write_text(
            '[{"tool":"tap_browser","status":"error","category":"validation_error","input":"private"}]'
        )
        adapter._deadline = time.monotonic() - 1
        assert await adapter.collect_diagnostics() == {
            "diagnostics": [
                {
                    "sequence": 1,
                    "tool": "tap_browser",
                    "status": "error",
                    "category": "validation_error",
                }
            ]
        }
        env = adapter.environment
        assert "OPENAI_API_KEY" not in env and "OPENCODE_CONFIG" not in env
        assert Path(env["HOME"]).is_relative_to(workspace.parent)
        assert env["TAP_CODING_PORT"] != env["TAP_MODEL_SHIM_PORT"]
        assert config["provider"]["litellm"]["options"]["baseURL"] == "http://127.0.0.1:39001/v1"
        private = tmp_path / "unrelated-secret"
        private.write_text("unrelated")
        # A spawned shell is confined by the same profile as the actual daemon.
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/sandbox-exec",
            "-f",
            str(adapter.sandbox_profile),
            "/bin/sh",
            "-c",
            'echo forbidden > "$1"',
            "sh",
            str(private),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await process.communicate()
        assert process.returncode != 0 and not stdout
        assert private.read_text() == "unrelated"
        if stopped_serve:
            os.kill(adapter._serve_pid, signal.SIGSTOP)
    finally:
        started = time.monotonic()
        stopped = await adapter.stop()
        if not stopped and adapter._serve_pid:
            os.kill(adapter._serve_pid, signal.SIGKILL)
            await adapter.stop()
        assert stopped and time.monotonic() - started < 10
    assert not adapter.workspace.exists()
    assert await adapter.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["tapper-unrelated-task-", "tap-browser-unrelated-task-"])
@pytest.mark.parametrize("temporary_root", [None, "/private/tmp"])
async def test_local_sandbox_rejects_other_task_files_and_other_loopback_ports(
    tmp_path, prefix, temporary_root
):
    import asyncio
    import socket
    import tempfile

    from tap.modules.workflows.adapters.local_opencode import LocalCodingContainer
    from tap.modules.workflows.adapters.opencode_runtime import CodingConfig

    if sys.platform != "darwin" or not shutil.which("sandbox-exec"):
        pytest.skip("native Mac sandbox test")
    node = Path(shutil.which("node"))
    adapter = LocalCodingContainer(
        CodingConfig.for_test(),
        tmp_path,
        opencode_binary=node,
        node_binary=node,
        client_path=ROOT / "deploy/browser/opencode-client.mjs",
    )
    config = {"provider": {"litellm": {"options": {}}}}
    try:
        adapter._prepare(config, "synthetic", 10)
        with tempfile.TemporaryDirectory(prefix=prefix, dir=temporary_root) as other:
            secret = Path(other) / "private-value"
            secret.write_text("private")
            process = await asyncio.create_subprocess_exec(
                "/usr/bin/sandbox-exec",
                "-f",
                str(adapter.sandbox_profile),
                "/bin/cat",
                str(secret),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await process.communicate()
            assert process.returncode != 0 and not stdout
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            process = await asyncio.create_subprocess_exec(
                "/usr/bin/sandbox-exec",
                "-f",
                str(adapter.sandbox_profile),
                str(node),
                "-e",
                "require('node:net').connect(Number(process.argv[1]),'127.0.0.1')"
                ".on('error', e => {console.log(e.code);process.exit(0)})"
                ".on('connect', () => process.exit(1));",
                str(port),
                env=adapter.environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), 5)
            assert process.returncode == 0 and stdout.strip() in (b"EPERM", b"EACCES")
    finally:
        assert await adapter.stop()


@pytest.mark.asyncio
async def test_native_shell_writes_heredoc_using_private_temporary_directory(tmp_path):
    from tap.modules.workflows.adapters.local_opencode import LocalCodingContainer
    from tap.modules.workflows.adapters.opencode_runtime import CodingConfig

    if sys.platform != "darwin":
        pytest.skip("native Mac shell test")
    node = Path(shutil.which("node"))
    adapter = LocalCodingContainer(
        CodingConfig.for_test(),
        tmp_path,
        opencode_binary=node,
        node_binary=node,
        client_path=ROOT / "deploy/browser/opencode-client.mjs",
    )
    try:
        adapter._prepare({"provider": {"litellm": {"options": {}}}}, "synthetic", 10)
        child = await asyncio.create_subprocess_exec(
            "/usr/bin/sandbox-exec",
            "-f",
            str(adapter.sandbox_profile),
            adapter.environment.get("SHELL", "/bin/zsh"),
            "-c",
            "cat > flow.spec.ts <<'EOF'\nconst value = 42;\nEOF",
            cwd=adapter.workspace,
            env=adapter.environment,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await child.wait()
        assert child.returncode == 0
        assert (adapter.workspace / "flow.spec.ts").read_text() == "const value = 42;\n"
    finally:
        assert await adapter.stop()


@pytest.mark.asyncio
async def test_local_stop_never_signals_a_reused_serve_pid(tmp_path):
    from tap.modules.workflows.adapters.local_opencode import LocalCodingContainer
    from tap.modules.workflows.adapters.opencode_runtime import CodingConfig

    if sys.platform != "darwin":
        pytest.skip("native Mac identity test")
    node = Path(shutil.which("node"))
    adapter = LocalCodingContainer(
        CodingConfig.for_test(),
        tmp_path,
        opencode_binary=node,
        node_binary=node,
        client_path=ROOT / "deploy/browser/opencode-client.mjs",
    )
    unrelated = await asyncio.create_subprocess_exec(
        str(node), "-e", "setInterval(() => {}, 1000)", start_new_session=True
    )
    try:
        adapter._serve_pid = unrelated.pid
        adapter._serve_identity = "previous process birth and command"
        assert await adapter.stop() is False
        assert unrelated.returncode is None
        os.kill(unrelated.pid, 0)
    finally:
        unrelated.kill()
        await unrelated.wait()
        adapter._serve_pid = None
        assert await adapter.stop()


@pytest.mark.parametrize(
    "stderr",
    [
        b"invalid generated script SECRET_TOKEN",
        b"EPERM /Users/user/SECRET_TOKEN",
        b"provider SECRET_TOKEN deadline exceeded",
    ],
)
def test_local_client_diagnostic_does_not_reflect_arbitrary_stderr(stderr):
    from tap.modules.workflows.adapters.local_opencode import LocalCodingContainer

    assert "SECRET_TOKEN" not in LocalCodingContainer._diagnostic(stderr)


@pytest.mark.asyncio
async def test_cancel_before_readiness_stops_already_spawned_detached_serve(tmp_path):
    from tap.modules.workflows.adapters.local_opencode import LocalCodingContainer
    from tap.modules.workflows.adapters.opencode_runtime import CodingConfig

    if sys.platform != "darwin":
        pytest.skip("native Mac process test")
    node = Path(shutil.which("node"))
    binary = tmp_path / "opencode"
    binary.write_text("""#!/usr/bin/env node
if (process.argv.includes('--version')) { console.log('1.19.5'); process.exit(0); }
require('node:fs').writeFileSync('spawned-pid', String(process.pid));
process.kill(process.pid, 'SIGSTOP');
setInterval(() => {}, 1000);
""")
    binary.chmod(0o700)
    adapter = LocalCodingContainer(
        CodingConfig.for_test(),
        tmp_path,
        opencode_binary=binary,
        node_binary=node,
        client_path=ROOT / "deploy/browser/opencode-client.mjs",
    )
    starting = asyncio.create_task(
        adapter.start({"provider": {"litellm": {"options": {}}}}, "synthetic", 60)
    )
    serve_pid = None
    try:
        async with asyncio.timeout(10):
            while not (adapter.workspace / "spawned-pid").exists():
                await asyncio.sleep(0.02)
        serve_pid = int((adapter.workspace / "spawned-pid").read_text())
        assert adapter._serve_pid is None
        starting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await starting
        started = time.monotonic()
        assert await adapter.stop()
        assert time.monotonic() - started < 10
        with pytest.raises(ProcessLookupError):
            os.kill(serve_pid, 0)
    finally:
        if not starting.done():
            starting.cancel()
            await asyncio.gather(starting, return_exceptions=True)
        if serve_pid is not None:
            try:
                os.kill(serve_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await adapter.stop()
