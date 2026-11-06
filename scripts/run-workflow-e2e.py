#!/usr/bin/env python3
"""Isolated real-stack workflow browser journey with a deterministic model.

No caller dotenv or provider credentials enter the owned Compose project or apps.
"""

from __future__ import annotations

import os
import json
import hashlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "compose.yaml"
OWNED_DATA_MOUNTS = {
    "mysql": "/var/lib/mysql",
    "redis": "/data",
    "tap-minio": "/data",
    "milvus-etcd": "/etcd",
    "milvus-minio": "/minio_data",
    "milvus": "/var/lib/milvus",
}


def write_owned_data_override(state: Path) -> Path:
    """Put only this run's durable middleware data on the host-backed private path."""
    services: dict[str, dict[str, list[dict[str, str]]]] = {}
    for service, target in OWNED_DATA_MOUNTS.items():
        directory = state / service
        directory.mkdir(mode=0o700)
        # Docker Desktop maps the bind root directly; container service UIDs
        # (including 65532 for the pinned object store) need write access.
        directory.chmod(0o777)
        services[service] = {
            "volumes": [{"type": "bind", "source": str(directory), "target": target}]
        }
    override = state / "owned-data.compose.json"
    override.write_text(json.dumps({"services": services}), encoding="utf-8")
    return override


def verify_owned_data_mounts(effective_json: str, state: Path) -> None:
    """Refuse to start if Compose did not replace each named data volume."""
    try:
        services = json.loads(effective_json)["services"]
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("workflow Compose configuration is invalid") from error
    if not isinstance(services, dict):
        raise RuntimeError("workflow Compose services are invalid")
    for service, target in OWNED_DATA_MOUNTS.items():
        config = services.get(service)
        volumes = config.get("volumes") if isinstance(config, dict) else None
        matches = (
            [
                mount
                for mount in volumes
                if isinstance(mount, dict) and mount.get("target") == target
            ]
            if isinstance(volumes, list)
            else []
        )
        expected = (state / service).resolve()
        if (
            len(matches) != 1
            or matches[0].get("type") != "bind"
            or not isinstance(matches[0].get("source"), str)
            or Path(matches[0]["source"]).resolve() != expected
        ):
            raise RuntimeError(f"workflow {service} data mount is not owned")


def write_owned_database_marker(
    env: dict[str, str], state: Path, mysql_container: str, context: str
) -> Path:
    """Let the existing publication fixture verify this exact owned MySQL."""
    if len(mysql_container) != 64 or any(
        char not in "0123456789abcdef" for char in mysql_container
    ):
        raise RuntimeError("owned workflow MySQL container ID is invalid")
    marker = state / "database-owner.json"
    receipt = {
        "project": env["TAP_TAPPER_COMPOSE_PROJECT"],
        "databaseUrlSha256": hashlib.sha256(
            env["TAP_DATABASE_URL"].encode()
        ).hexdigest(),
        "hostPort": int(env["MYSQL_PORT"]),
        "runnerPid": os.getpid(),
        "mysqlContainerId": mysql_container,
        "dockerContext": context,
    }
    descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(receipt, output)
    env["TAPPER_E2E_OWNERSHIP_FILE"] = str(marker)
    return marker


def run(command: list[str], env: dict[str, str], *, timeout: int = 300) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"{' '.join(command[:4])} exited {result.returncode}: "
            f"{result.stderr[-4000:]}"
        )
    return result.stdout.strip()


def run_browser(command: list[str], env: dict[str, str], *, timeout: int) -> str:
    """Own Playwright and its browser children even when the test times out."""
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=timeout)
    except BaseException:
        stop_owned_group(process, grace=10)
        raise
    stop_owned_group(process, grace=10)
    if process.returncode:
        raise RuntimeError(f"workflow browser failed: {output[-6000:]}")
    return output.strip()


def stop_owned_group(process: subprocess.Popen[str], *, grace: int) -> bool:
    """Settle only the process group created with start_new_session=True."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    forced = False
    if process.poll() is None:
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            forced = True
    else:
        # The direct parent may have exited while owned browser/app children live.
        time.sleep(0.1)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        process.wait(timeout=10)
    return forced


def local_docker(env: dict[str, str]) -> str:
    context = run(["docker", "context", "show"], env)
    if not context or not all(char.isalnum() or char in "._-" for char in context):
        raise RuntimeError("workflow E2E requires a named local Docker context")
    endpoint = run(
        [
            "docker",
            "context",
            "inspect",
            context,
            "--format",
            '{{(index .Endpoints "docker").Host}}',
        ],
        env,
    )
    if not endpoint.startswith(("unix://", "npipe://")):
        raise RuntimeError("workflow E2E requires a local Docker socket")
    return context


def free_ports(count: int) -> list[int]:
    probes = []
    try:
        for _ in range(count):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            probes.append(probe)
        return [probe.getsockname()[1] for probe in probes]
    finally:
        for probe in probes:
            probe.close()


def isolated_env(project: str, state: Path, ports: list[int]) -> dict[str, str]:
    names = (
        "MYSQL_PORT",
        "REDIS_PORT",
        "LITELLM_PORT",
        "MILVUS_PORT",
        "MILVUS_HEALTH_PORT",
        "TAPPER_S3_PORT",
        "TAPPER_API_PORT",
        "TAPPER_WEB_PORT",
    )
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "TMPDIR": str(state),
        "TAP_TAPPER_COMPOSE_PROJECT": project,
        "TAPPER_SUPERVISOR_ENV": "preloaded",
        "TAPPER_COMPOSE_OBJECT_STORE_VERIFY": "1",
        "COMPOSE_DISABLE_ENV_FILE": "1",
        "TAP_DEMO_MODE": "e2e",
        "TAPPER_MODEL_BACKEND": "fake",
        "TAPPER_GRAPH_EXTRACTION_MODE": "fake",
        "TAP_WORKFLOW_ENABLED": "true",
        "TAPPER_API_HOST": "127.0.0.1",
        "TAPPER_WEB_HOST": "127.0.0.1",
        "TAP_AGENT_TEAM_RUNTIME_METRICS": "turn_table",
        "TAP_LANGFUSE_ENVIRONMENT": "e2e",
        "TAPPER_VISION_MODEL": "",
        "MYSQL_ROOT_PASSWORD": "workflow-e2e-root",
        "MYSQL_DATABASE": "tap",
        "MYSQL_USER": "tap",
        "MYSQL_PASSWORD": "workflow-e2e",
        "LITELLM_MASTER_KEY": "workflow-e2e-master",
        "LITELLM_EMBEDDING_MODEL": "text-embedding-v4",
        "DASHSCOPE_API_KEY": "workflow-e2e-unused",
        "MILVUS_DATABASE": "default",
        "MILVUS_MINIO_ROOT_USER": "workflow-e2e-minio",
        "MILVUS_MINIO_ROOT_PASSWORD": "workflow-e2e-minio-password",
        "MILVUS_INITIAL_ROOT_PASSWORD": "Milvus",
        "MILVUS_ROOT_PASSWORD": "workflow-e2e-Root1!",
        "MILVUS_READER_USERNAME": "tap_reader",
        "MILVUS_READER_PASSWORD": "workflow-e2e-Reader1!",
        "MILVUS_WRITER_USERNAME": "tap_writer",
        "MILVUS_WRITER_PASSWORD": "workflow-e2e-Writer1!",
        "MILVUS_PROVISIONER_USERNAME": "tap_provisioner",
        "MILVUS_PROVISIONER_PASSWORD": "workflow-e2e-Provisioner1!",
        "TAPPER_S3_BUCKET": f"{project}-objects",
        "TAPPER_S3_REGION": "us-east-1",
        "TAPPER_S3_ACCESS_KEY": "workflow-e2e-object-key",
        "TAPPER_S3_SECRET_KEY": "workflow-e2e-object-password",
        "TAPPER_S3_STORE_ID": project,
        "TAPPER_COLLECTION": "kb_doc_v1_tapper_demo",
        "TAPPER_ALIAS": "kb_doc_tapper_demo_active",
        "TAPPER_CORPUS_VERSION": "tapper-demo-v1",
        "TAPPER_SCHEMA_VERSION": "doc-schema-v1",
        "TAPPER_DEFAULT_CHAT_MODEL": "qwen-plus",
        "TAPPER_EMBEDDING_MODEL": "text-embedding-v4",
        "TAPPER_RETRIEVAL_PROFILE": "quick-hybrid-v1",
        "TAPPER_EMBEDDING_DIMENSION": "1536",
        "TAPPER_INDEX_VERSION": "tapper-index-v1",
        "TAPPER_PIPELINE_VERSION": "tapper-ingestion-v1",
        "TAPPER_WORKER_ID": project,
        "TAPPER_POLL_SECONDS": "0.1",
        "TAPPER_JOB_BATCH_SIZE": "10",
        "TAPPER_READY_TIMEOUT_SECONDS": "5",
        "TAPPER_MODEL_TIMEOUT_SECONDS": "15",
        "TAPPER_VISION_TIMEOUT_SECONDS": "60",
        "TAPPER_BLOB_TIMEOUT_SECONDS": "15",
        "TAPPER_MILVUS_TIMEOUT_SECONDS": "30",
    }
    env.update({name: str(port) for name, port in zip(names, ports, strict=True)})
    mysql, redis, litellm, milvus, _health, object_store, api, web = ports
    env.update(
        {
            "TAP_DATABASE_URL": f"mysql+asyncmy://tap:workflow-e2e@127.0.0.1:{mysql}/tap?charset=utf8mb4",
            "TAP_ALEMBIC_DATABASE_URL": f"mysql+pymysql://tap:workflow-e2e@127.0.0.1:{mysql}/tap?charset=utf8mb4",
            "TAP_REDIS_URL": f"redis://127.0.0.1:{redis}/0",
            "TAP_REDIS_COMMAND_STREAM": "tap:commands",
            "LITELLM_BASE_URL": f"http://127.0.0.1:{litellm}",
            "DASHSCOPE_API_BASE": f"http://127.0.0.1:{litellm}",
            "MILVUS_URI": f"http://127.0.0.1:{milvus}",
            "TAPPER_S3_ENDPOINT": f"http://127.0.0.1:{object_store}",
            "TAPPER_WEB_BASE_URL": f"http://127.0.0.1:{web}",
            "TAPPER_API_BASE_URL": f"http://127.0.0.1:{api}",
            "TAPPER_WORKFLOW_E2E": "1",
        }
    )
    return env


def api_readiness(body: bytes) -> tuple[bool, str]:
    """Accept the fixed API readiness state and report only public component states."""
    if len(body) > 65536:
        return False, "oversized readiness response"
    try:
        value = json.loads(body)
    except (TypeError, ValueError):
        return False, "invalid readiness response"
    if not isinstance(value, dict):
        return False, "invalid readiness response"
    status = value.get("status")
    if status not in {"ready", "unready"}:
        return False, "invalid readiness status"
    components = value.get("components")
    safe_components: list[str] = []
    if isinstance(components, list):
        for component in components:
            if not isinstance(component, dict):
                continue
            name, state = component.get("name"), component.get("state")
            if name in {"mysql", "redis", "blob", "milvus", "models"} and state in {
                "ok",
                "failed",
            }:
                safe_components.append(f"{name}:{state}")
    return status == "ready", f"{status} ({', '.join(safe_components)})"


def wait_ready(
    url: str, process: subprocess.Popen[str], log: Path, *, api: bool = False
) -> None:
    deadline = time.monotonic() + 150
    last_readiness = "no readiness response"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"workflow app supervisor exited; log: {log}\n{log.read_text()[-6000:]}"
            )
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    if not api:
                        return
                    ready, last_readiness = api_readiness(response.read(65537))
                    if ready:
                        return
        except (OSError, URLError):
            pass
        time.sleep(0.25)
    raise RuntimeError(
        f"workflow app did not become ready: {url}; {last_readiness}; "
        f"log: {log}\n{log.read_text()[-6000:]}"
    )


def stop_supervisor(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    if stop_owned_group(process, grace=125):
        raise RuntimeError("workflow app supervisor required a forced stop")
    if process.returncode not in (0, 143, -signal.SIGTERM):
        raise RuntimeError(f"workflow app supervisor exited {process.returncode}")


def cleanup_owned_state(
    state: Path,
    process: subprocess.Popen[str] | None,
    owned: bool,
    compose: list[str] | None,
    env: dict[str, str],
    context: str | None,
    project: str,
) -> list[str]:
    """Retain data and its receipt unless owned services are proven detached."""
    errors: list[str] = []
    try:
        stop_supervisor(process)
    except BaseException as error:
        errors.append(f"app supervisor: {error}")
    if owned:
        if compose is None or context is None:
            errors.append("owned Compose identity is unavailable")
        else:
            try:
                run(
                    compose + ["down", "--volumes", "--remove-orphans"],
                    env,
                    timeout=180,
                )
            except BaseException as error:
                errors.append(f"Compose down: {error}")
            else:
                try:
                    remaining = run(
                        [
                            "docker",
                            "--context",
                            context,
                            "ps",
                            "-aq",
                            "--filter",
                            f"label=com.docker.compose.project={project}",
                        ],
                        env,
                    )
                except BaseException as error:
                    errors.append(f"owned container verification: {error}")
                else:
                    if remaining:
                        errors.append(
                            "owned project containers remain after Compose down"
                        )
    if not errors:
        shutil.rmtree(state)
    return errors


def main() -> int:
    project = f"tap-workflow-e2e-{uuid.uuid4().hex[:12]}"
    state = Path(tempfile.mkdtemp(prefix="tap-workflow-e2e-")).resolve()
    state.chmod(0o700)
    env: dict[str, str] = {}
    compose: list[str] | None = None
    context: str | None = None
    process: subprocess.Popen[str] | None = None
    owned = False
    primary_error: BaseException | None = None
    try:
        env = isolated_env(project, state, free_ports(8))
        context = local_docker(env)
        env["DOCKER_CONTEXT"] = context
        override = write_owned_data_override(state)
        compose = [
            "docker",
            "--context",
            context,
            "compose",
            "-f",
            str(COMPOSE),
            "-f",
            str(override),
            "-p",
            project,
            "--profile",
            "milvus",
        ]
        verify_owned_data_mounts(
            run(compose + ["config", "--format", "json"], env), state
        )
        if run(
            [
                "docker",
                "--context",
                context,
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
            env,
        ):
            raise RuntimeError("new workflow E2E project label is already owned")
        env["TAPPER_OBJECT_STORE_IMAGE"] = run(
            ["bash", "scripts/build-tapper-object-store.sh", "verify"], env
        )
        owned = True
        run(compose + ["up", "-d", "--wait", "--wait-timeout", "180"], env, timeout=300)
        mysql_container = run(
            [
                "docker",
                "--context",
                context,
                "ps",
                "--no-trunc",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--filter",
                "label=com.docker.compose.service=mysql",
                "--format",
                "{{.ID}}",
            ],
            env,
        )
        if len(mysql_container) != 64:
            raise RuntimeError("owned workflow E2E MySQL container was not identified")
        write_owned_database_marker(env, state, mysql_container, context)
        run(
            [
                "uv",
                "run",
                "--project",
                "apps/tap-ai-backend",
                "alembic",
                "-c",
                "apps/tap-ai-backend/alembic.ini",
                "upgrade",
                "head",
            ],
            env,
        )
        run(
            [
                "uv",
                "run",
                "--project",
                "apps/tap-ai-backend",
                "python",
                "scripts/milvus_bootstrap.py",
            ],
            {**env, "TAP_ALLOW_INITIAL_MILVUS_ROOT": "1"},
        )
        run(
            [
                "uv",
                "run",
                "--project",
                "apps/tap-ai-backend",
                "python",
                "scripts/tapper_collection.py",
                "ensure",
            ],
            env,
        )
        log = state / "apps.log"
        with log.open("w", encoding="utf-8") as output:
            process = subprocess.Popen(
                ["bash", "scripts/run-tapper-dev.sh"],
                cwd=ROOT,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            wait_ready(
                f"{env['TAPPER_API_BASE_URL']}/health/ready", process, log, api=True
            )
            wait_ready(f"{env['TAPPER_WEB_BASE_URL']}/", process, log)
            run_browser(
                [
                    "corepack",
                    "pnpm",
                    "--filter",
                    "@tap/ai-frontend",
                    "exec",
                    "playwright",
                    "test",
                    "tests/e2e/workflow-live.spec.ts",
                    "--config=playwright.config.ts",
                    "--reporter=line",
                    "--workers=1",
                ],
                env,
                timeout=360,
            )
        print("Workflow real-stack deterministic browser journey passed.")
        return 0
    except BaseException as error:
        primary_error = error
        print(f"Workflow E2E failed: {error}", file=sys.stderr)
        app_log = state / "apps.log"
        if app_log.is_file():
            print(
                f"Owned app log tail:\n{app_log.read_text()[-8000:]}", file=sys.stderr
            )
        return 1
    finally:
        cleanup_errors = cleanup_owned_state(
            state, process, owned, compose, env, context, project
        )
        if cleanup_errors:
            print(
                "Workflow E2E cleanup failed; owned state and receipt retained at "
                f"{state}: " + "; ".join(cleanup_errors),
                file=sys.stderr,
            )
            if primary_error is None:
                raise RuntimeError("workflow E2E cleanup failed")


if __name__ == "__main__":
    raise SystemExit(main())
