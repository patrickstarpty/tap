"""Real owned image/process security evidence, explicitly enabled outside make test."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path

import pytest
from scripts.parser_test_support import isolated_parser

from tap.modules.knowledge.adapters.isolated_parser import IsolatedParser
from tap.modules.knowledge.domain.documents import (
    PARSER_VERSION,
    DocumentId,
    DocumentParseRejected,
    DocumentSource,
    MediaType,
    canonical_sha256,
    revision_id_for,
)

pytestmark = pytest.mark.skipif(
    os.getenv("TAP_RUN_PARSER_SECURITY") != "1", reason="requires owned parser image"
)


def source(name: str, media: str, data: bytes) -> DocumentSource:
    doc = DocumentId("doc_" + "b" * 32)
    return DocumentSource(
        name,
        MediaType(media),
        data,
        doc,
        revision_id_for(doc, canonical_sha256(data), PARSER_VERSION),
    )


def test_owned_parser_hostile_matrix_then_valid_recovery(tmp_path):
    root = Path(__file__).resolve().parents[4]
    spec = importlib.util.spec_from_file_location(
        "fixture_builder", root / "scripts/build-hostile-document-fixtures.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build(tmp_path)
    with isolated_parser() as owned:

        async def check():
            client = IsolatedParser(owned.socket_path)
            await client.check_ready()
            for row in json.loads((tmp_path / "manifest.json").read_text()):
                current = source(
                    row["name"], row["mediaType"], (tmp_path / row["name"]).read_bytes()
                )
                if row["expectedError"]:
                    with pytest.raises(DocumentParseRejected) as error:
                        await client.parse(current)
                    assert error.value.code == row["expectedError"], row["name"]
                else:
                    result = await client.parse(current)
                    assert "retained safe fact" in result.blocks[0].text
                receipt = json.loads((owned.state / "last-terminal.json").read_text())
                assert receipt["running"] is False
                assert receipt["oomKilled"] is False
            final = await client.parse(
                source("after.txt", "text/plain", b"A valid parse after rejection.")
            )
            assert final.blocks[0].text == "A valid parse after rejection."
            await client.check_ready()

        asyncio.run(check())


def test_owned_parser_probe_inherits_limits_and_network_isolation():
    from scripts.parser_test_support import isolated_probe

    from tap.modules.knowledge.adapters.parser_protocol import read_result

    with isolated_probe() as supervisor:

        async def check():
            result = await supervisor.run_job(
                "c" * 32, source("probe.txt", "text/plain", b"constraints")
            )
            reader = asyncio.StreamReader()
            reader.feed_data(result)
            reader.feed_eof()
            error, payload = await read_result(reader, "c" * 32)
            assert error is None, (
                (supervisor.state / "last-terminal.json").read_text()
                if (supervisor.state / "last-terminal.json").exists()
                else "no container reached"
            )
            assert json.loads(payload) == {"probe": "constraints", "passed": True}
            assert await supervisor.owned() == []

        asyncio.run(check())


@pytest.mark.parametrize(
    "case", ["memory", "cpu", "timeout", "oom", "crash", "output", "stderr", "cancel"]
)
def test_owned_parser_resource_faults_reap_whole_container(case):
    from scripts.parser_test_support import isolated_probe

    from tap.modules.knowledge.adapters.parser_protocol import read_result

    with isolated_probe() as supervisor:

        async def check():
            task = asyncio.create_task(
                supervisor.run_job("d" * 32, source("probe.txt", "text/plain", case.encode()))
            )
            if case == "cancel":
                for _ in range(150):
                    await asyncio.sleep(0.1)
                    ids = await supervisor.owned()
                    if ids and (await supervisor.inspect(ids[0]))["State"]["Running"]:
                        await asyncio.sleep(0.3)
                        task.cancel()
                        break
                else:
                    raise AssertionError("owned probe never started")
            result = await task
            reader = asyncio.StreamReader()
            reader.feed_data(result)
            reader.feed_eof()
            error, payload = await read_result(reader, "d" * 32)
            if case == "memory":
                assert error is None
                assert json.loads(payload)["probe"] == "memory"
            else:
                assert error in {"parser-unavailable", "document-too-complex", "cancelled"}
            receipt = json.loads((supervisor.state / "last-terminal.json").read_text())
            assert receipt["running"] is False
            assert receipt["oomKilled"] is (case == "oom")
            assert await supervisor.owned() == []
            assert not supervisor.unresolved

        asyncio.run(check())


@pytest.mark.parametrize("mode", ["normal-half-close", "truncated-control", "client-cancel"])
def test_owned_uds_completion_and_cancellation_wait_for_remote_cleanup(mode):
    from scripts.parser_test_support import isolated_probe

    from tap.modules.knowledge.adapters.parser_protocol import encode_request, read_result

    with isolated_probe() as supervisor:

        async def check():
            path = supervisor.state / "probe.sock"
            server = await asyncio.start_unix_server(supervisor.handle, path=path)
            writer = None
            try:
                async with server:
                    if mode == "client-cancel":
                        client = IsolatedParser(str(path))
                        task = asyncio.create_task(
                            client.parse(source("probe.txt", "text/plain", b"cancel"))
                        )
                        for _ in range(150):
                            await asyncio.sleep(0.1)
                            ids = await supervisor.owned()
                            if ids and (await supervisor.inspect(ids[0]))["State"]["Running"]:
                                task.cancel()
                                break
                        else:
                            raise AssertionError("owned client parse never started")
                        with pytest.raises(asyncio.CancelledError):
                            await task
                    else:
                        reader, writer = await asyncio.open_unix_connection(str(path))
                        case = b"constraints" if mode == "normal-half-close" else b"cancel"
                        writer.write(
                            encode_request(source("probe.txt", "text/plain", case), "c" * 32)
                        )
                        if mode == "truncated-control":
                            writer.write(b"\x00")
                        await writer.drain()
                        writer.write_eof()
                        error, payload = await read_result(reader, "c" * 32)
                        if mode == "normal-half-close":
                            assert error is None
                            assert json.loads(payload) == {"probe": "constraints", "passed": True}
                        else:
                            assert error == "cancelled"
                    assert await supervisor.owned() == []
                    assert not supervisor.unresolved
                    terminal = supervisor.state / "last-terminal.json"
                    if mode != "truncated-control":
                        assert json.loads(terminal.read_text())["running"] is False
            finally:
                if writer is not None:
                    writer.close()
                    await writer.wait_closed()
                server.close()
                await server.wait_closed()
                for job in supervisor.jobs:
                    job.cancel()
                await asyncio.gather(*supervisor.jobs, return_exceptions=True)

        asyncio.run(check())


def test_owned_supervisor_crash_still_ends_child_and_recovery_removes_container():
    import signal
    import subprocess
    import time

    from scripts.parser_test_support import isolated_probe

    from tap.modules.knowledge.adapters.parser_protocol import encode_request

    with isolated_probe() as supervisor:
        settings = supervisor.state / "probe-settings.json"
        settings.write_text(
            json.dumps(
                {
                    "root": str(supervisor.root),
                    "state": str(supervisor.state),
                    "project": supervisor.project,
                    "image": supervisor.image,
                    "docker": supervisor.docker,
                }
            )
        )
        bootstrap = """
import asyncio,json,sys
from pathlib import Path
from tap.entrypoints.tapper_parser_worker import Supervisor
async def main():
    config=json.loads(Path(sys.argv[1]).read_text())
    supervisor=Supervisor(Path(config['root']),Path(config['state']),config['project'],config['image'],config['docker'])
    server=await asyncio.start_unix_server(
        supervisor.handle,path=Path(config['state'])/'crash.sock')
    async with server: await asyncio.Event().wait()
asyncio.run(main())
"""
        process = subprocess.Popen(
            [str(supervisor.root / ".venv/bin/python"), "-c", bootstrap, str(settings)],
            cwd=supervisor.root,
            env=supervisor.environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cli_pids = []
        try:

            async def check():
                deadline = time.monotonic() + 10
                while not (supervisor.state / "crash.sock").exists():
                    assert process.poll() is None
                    assert time.monotonic() < deadline
                    await asyncio.sleep(0.05)
                reader, writer = await asyncio.open_unix_connection(
                    str(supervisor.state / "crash.sock")
                )
                try:
                    writer.write(
                        encode_request(
                            source("probe.txt", "text/plain", b"supervisor-crash"), "e" * 32
                        )
                    )
                    await writer.drain()
                    deadline = time.monotonic() + 15
                    while True:
                        ids = await supervisor.owned()
                        if ids and (await supervisor.inspect(ids[0]))["State"]["Running"]:
                            break
                        assert time.monotonic() < deadline
                        await asyncio.sleep(0.1)
                    for line in subprocess.check_output(
                        ["ps", "-axo", "pid=,ppid="], text=True
                    ).splitlines():
                        pid, parent = map(int, line.split())
                        if parent == process.pid:
                            cli_pids.append(pid)
                    assert cli_pids
                    process.kill()
                    process.wait(timeout=5)
                    deadline = time.monotonic() + 38
                    while (await supervisor.inspect(ids[0]))["State"]["Running"]:
                        assert time.monotonic() < deadline, (
                            "independent PID1 lifetime did not settle"
                        )
                        await asyncio.sleep(0.3)
                    assert await supervisor.cleanup()
                    assert await supervisor.owned() == []
                finally:
                    writer.close()
                    await writer.wait_closed()

            asyncio.run(check())
            for pid in cli_pids:
                deadline = time.monotonic() + 5
                while True:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    assert time.monotonic() < deadline, (
                        "orphaned local Docker CLI did not exit/reap"
                    )
                    time.sleep(0.1)
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGKILL)
            process.wait(timeout=5)


def test_owned_parser_abnormal_exit_retains_state_and_cold_recovery():
    failed = None
    with pytest.raises(RuntimeError, match="abnormally"):
        with isolated_parser() as owned:
            failed = owned
            owned.process.kill()
            owned.process.wait(timeout=5)
    assert failed is not None and failed.state.exists()
    association = json.loads((failed.state / "association.json").read_text())
    with isolated_parser(state=failed.state, project=failed.project) as recovered:
        assert recovered.process.pid != failed.process.pid
        assert json.loads((recovered.state / "association.json").read_text()) == association
        asyncio.run(IsolatedParser(recovered.socket_path).check_ready())
    assert not failed.state.exists()


def test_actual_dev_launcher_cold_restart_reconciles_persisted_owner(tmp_path):
    import shlex
    import signal
    import subprocess
    import sys
    import time
    from uuid import uuid4

    root = Path(__file__).resolve().parents[4]
    spec = importlib.util.spec_from_file_location(
        "command_fixtures", root / "apps/backend/tests/contract/test_demo_commands.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    launcher, environment, log = module._supervisor_fixture(tmp_path)
    project = "tap-parser-restart-" + uuid4().hex[:12]
    environment.update(TAP_TAPPER_COMPOSE_PROJECT=project, TAPPER_EXPECTED_PROJECT=project)
    # The production launcher and parser process are real; unrelated app processes
    # and their HTTP readiness are command doubles, with no middleware started.
    shim = launcher.parents[1] / ".venv/bin/python"
    shim.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
    state = launcher.parents[1] / ".tapper/parser-runtime" / project
    processes = []
    parser_pids = []

    def launch():
        output = (tmp_path / f"launcher-{len(processes)}.log").open("w")
        process = subprocess.Popen(
            ["bash", str(launcher)],
            cwd=launcher.parents[1],
            env=environment,
            stdout=output,
            stderr=output,
        )
        output.close()
        processes.append(process)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError("actual launcher startup failed")
            if (state / "ready-pid").exists():
                pid = int((state / "ready-pid").read_text())
                if pid not in parser_pids:
                    parser_pids.append(pid)
                    return process, pid
            time.sleep(0.05)
        raise AssertionError("actual launcher readiness timed out")

    try:
        first, parser_pid = launch()
        association = json.loads((state / "association.json").read_text())
        socket = subprocess.check_output(
            [
                sys.executable,
                "-m",
                "tap.entrypoints.tapper_parser_worker",
                "--state-dir",
                str(state),
                "--project",
                project,
                "--socket-path",
            ],
            text=True,
        ).strip()

        async def interrupt_owned_job():
            client = IsolatedParser(socket)
            job = asyncio.create_task(
                client.parse(source("bounded.txt", "text/plain", b"a" * (25 * 1024 * 1024)))
            )
            cid = ""
            try:
                for _ in range(100):
                    await asyncio.sleep(0.02)
                    result = await asyncio.create_subprocess_exec(
                        "docker",
                        "ps",
                        "-aq",
                        "--no-trunc",
                        "--filter",
                        "label=io.tap.parser.owner=" + association["owner"],
                        stdout=asyncio.subprocess.PIPE,
                    )
                    output, _ = await result.communicate()
                    cid = output.decode().strip()
                    if cid:
                        break
                assert len(cid) == 64, "owned active job not observed"
                children = subprocess.check_output(["ps", "-axo", "pid=,ppid="], text=True)
                old_cli_pids.extend(
                    int(row.split()[0])
                    for row in children.splitlines()
                    if len(row.split()) == 2 and int(row.split()[1]) == parser_pid
                )
                assert old_cli_pids, "actual attached Docker child was not observed"
                first.kill()
                first.wait(timeout=5)
                os.kill(parser_pid, signal.SIGKILL)
                assert not (state / "cleanup.json").exists(), "old cleanup proof survived create"
                return cid
            finally:
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)

        old_cli_pids = []
        old_cid = asyncio.run(interrupt_owned_job())
        assert (
            state.exists() and json.loads((state / "association.json").read_text()) == association
        )
        second, new_pid = launch()
        assert new_pid != parser_pid
        assert json.loads((state / "association.json").read_text()) == association
        assert (
            subprocess.run(
                ["docker", "inspect", old_cid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            ).returncode
            != 0
        )
        asyncio.run(IsolatedParser(socket).check_ready())
        for _ in range(50):
            existing = {
                int(pid)
                for pid in subprocess.check_output(["ps", "-axo", "pid="], text=True).split()
            }
            if not existing.intersection(old_cli_pids):
                break
            time.sleep(0.1)
        assert not existing.intersection(old_cli_pids), "old local Docker CLI did not exit"
        second.terminate()
        assert second.wait(timeout=35) == 143
        proof = json.loads((state / "cleanup.json").read_text())
        assert proof == {**association, "containers": [], "localChildrenReaped": True}
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=35)
        for pid in parser_pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        # The killed launcher's unrelated command doubles are still ours.
        for pid in module._started_child_pids(log):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if state.exists():
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tap.entrypoints.tapper_parser_worker",
                    "--state-dir",
                    str(state),
                    "--project",
                    project,
                    "--cleanup-only",
                ],
                timeout=35,
            )
            assert result.returncode == 0
        owned_pids = module._started_child_pids(log) + parser_pids
        for _ in range(50):
            existing = {
                int(pid)
                for pid in subprocess.check_output(["ps", "-axo", "pid="], text=True).split()
            }
            if not existing.intersection(owned_pids):
                break
            time.sleep(0.1)
        assert not existing.intersection(owned_pids), "owned launcher children remain"
