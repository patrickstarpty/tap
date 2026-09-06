"""Owned local parser supervisor harness; never uses shared application services."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import tempfile
import time
from collections.abc import Iterator
from uuid import uuid4


@dataclass(frozen=True)
class OwnedParser:
    socket_path: str
    state: Path
    project: str
    process: subprocess.Popen


@contextmanager
def isolated_parser(
    *, state: Path | None = None, project: str | None = None
) -> Iterator[OwnedParser]:
    root = Path(__file__).resolve().parents[1]
    project = project or "tap-parser-test-" + uuid4().hex[:12]
    state = state or Path(tempfile.mkdtemp(prefix="tap-parser-", dir="/tmp"))
    directory = str(state)
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    command = [
        str(root / ".venv/bin/python"),
        "-m",
        "tap.entrypoints.tapper_parser_worker",
        "--state-dir",
        directory,
        "--project",
        project,
    ]
    socket = subprocess.check_output(
        [*command, "--socket-path"], cwd=root, env=environment, text=True
    ).strip()
    old_ready = (
        (state / "ready-pid").read_text() if (state / "ready-pid").exists() else ""
    )
    process = subprocess.Popen(
        command,
        cwd=root,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        if old_ready == str(process.pid):
            raise RuntimeError("owned parser readiness identity reused")
        while (
            not Path(socket).exists()
            or not (state / "ready-pid").exists()
            or (state / "ready-pid").read_text() != str(process.pid)
        ):
            if process.poll() is not None:
                raise RuntimeError("owned parser startup failed")
            if time.monotonic() > deadline:
                raise RuntimeError("owned parser readiness timed out")
            time.sleep(0.1)
        yield OwnedParser(socket, state, project, process)
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=25)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise RuntimeError("owned parser supervisor cleanup timed out") from None
        if process.returncode != 0:
            raise RuntimeError(
                "owned parser exited abnormally; state retained for explicit recovery"
            )
        cleanup = subprocess.run(
            [*command, "--cleanup-only"],
            cwd=root,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        if (
            cleanup.returncode
            or (state / "unresolved.json").exists()
            or not (state / "cleanup.json").exists()
        ):
            raise RuntimeError("owned parser cleanup is unresolved; state retained")
    import shutil

    shutil.rmtree(state)


@contextmanager
def isolated_probe():
    """Owned derivative test image: same PID1/protocol, only child is replaced."""
    import hashlib
    import json
    import shutil
    from tap.entrypoints.tapper_parser_worker import Supervisor

    root = Path(__file__).resolve().parents[1]

    def run(args):
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, **({"DOCKER_BUILDKIT": "0"} if "build" in args else {})},
        )
        if result.returncode:
            raise RuntimeError(result.stderr[-2000:])
        return result.stdout.strip()

    production = run(["bash", str(root / "scripts/build-tapper-parser.sh"), "verify"])
    context = run(["docker", "context", "show"])
    endpoint = run(
        [
            "docker",
            "context",
            "inspect",
            context,
            "--format",
            '{{(index .Endpoints "docker").Host}}',
        ]
    )
    assert endpoint.startswith("unix://") and not os.getenv("DOCKER_HOST")
    plugin = run(
        [
            "docker",
            "info",
            "--format",
            '{{range .ClientInfo.Plugins}}{{if eq .Name "compose"}}{{.Path}}{{end}}{{end}}',
        ]
    )
    assert Path(plugin).is_file()
    directory = tempfile.mkdtemp(prefix="tap-parser-probe-", dir="/tmp")
    state = Path(directory)
    project = "tap-parser-probe-" + uuid4().hex[:12]
    image = None
    supervisor = None
    try:
        config = state / "docker"
        config.mkdir()
        (config / "config.json").write_text(
            json.dumps({"auths": {}, "cliPluginsExtraDirs": [str(Path(plugin).parent)]})
        )
        docker = ["docker", "--config", str(config), "--host", endpoint]
        build = state / "context"
        build.mkdir()
        probe = (root / "apps/backend/tests/fixtures/parser_probe.py").read_bytes()
        digest = hashlib.sha256(probe).hexdigest()
        (build / "child.py").write_bytes(probe)
        recipe = f"FROM {production}\nCOPY child.py /opt/parser/child.py\n"
        recipe_digest = hashlib.sha256(recipe.encode()).hexdigest()
        labels = {
            "io.tap.parser.test": "true",
            "io.tap.parser.probe-sha": digest,
            "io.tap.parser.recipe-sha": recipe_digest,
            "io.tap.parser.test-owner": project,
        }
        (build / "Dockerfile").write_text(
            recipe
            + "LABEL "
            + " ".join(key + "=" + json.dumps(value) for key, value in labels.items())
            + "\n"
        )
        run(
            [
                *docker,
                "build",
                "--network=none",
                "--iidfile",
                str(state / "image-id"),
                str(build),
            ]
        )
        image = (state / "image-id").read_text().strip()
        inspected = json.loads(run([*docker, "image", "inspect", image]))[0]
        assert all(
            inspected["Config"]["Labels"].get(key) == value
            for key, value in labels.items()
        )
        supervisor = Supervisor(root, state, project, image, docker)
        yield supervisor
    finally:
        import asyncio

        cleaned = supervisor is None or asyncio.run(supervisor.cleanup())
        if cleaned and image is not None:
            run([*docker, "image", "rm", "--no-prune", image])
            run([*docker, "image", "inspect", production])
        if cleaned:
            shutil.rmtree(state)
        else:
            raise RuntimeError(
                "owned parser probe cleanup unresolved; state retained at " + str(state)
            )
