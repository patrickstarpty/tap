from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import logging
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pymilvus.decorators import _log_rpc_error

ROOT = Path(__file__).resolve().parents[4]
E2E_FIXED_PORTS = (13306, 16379, 11000, 14000, 29530, 19091, 18000, 15173)
_RECORDED_ENVIRONMENT_NAMES = (
    "ATHENA_API_HOST",
    "AZURE_STORAGE_CONNECTION_STRING",
    "BAILIAN_API_BASE",
    "BAILIAN_API_KEY",
    "CODEX_API_BASE",
    "CODEX_API_KEY",
    "CODEX_HOME",
    "DASHSCOPE_API_BASE",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_BASE_URL",
    "LITELLM_ATHENA_EMBEDDING_MODEL",
    "LITELLM_EMBEDDING_API_BASE",
    "LITELLM_EMBEDDING_API_KEY",
    "LITELLM_EMBEDDING_MODEL",
    "LITELLM_MASTER_KEY",
    "MILVUS_READER_PASSWORD",
    "MILVUS_WRITER_PASSWORD",
    "OPENAI_API_BASE",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "TAP_DATABASE_URL",
    "TAP_REDIS_URL",
)


def _write_environment_name_probe(directory: Path) -> None:
    probe = directory / "athena-env-names"
    pattern = "|".join(_RECORDED_ENVIRONMENT_NAMES)
    probe.write_text(
        f"""#!/bin/sh
set -eu
env | sed 's/=.*//' | grep -E '^({pattern})$' | LC_ALL=C sort | tr '\n' ','
""",
        encoding="utf-8",
    )
    probe.chmod(probe.stat().st_mode | stat.S_IXUSR)


def _write_behavioral_socket_stub(root: Path) -> Path:
    calls = root / "socket-calls.log"
    (root / "socket.py").write_text(
        """import os

AF_INET = 2
SOCK_STREAM = 1


class socket:
    def __init__(self, family, kind):
        if family != AF_INET or kind != SOCK_STREAM:
            raise AssertionError("unexpected socket construction")

    def bind(self, address):
        if (
            type(address) is not tuple
            or len(address) != 2
            or address[0] != "127.0.0.1"
            or type(address[1]) is not int
        ):
            raise AssertionError("unexpected socket address")
        with open(os.environ["ATHENA_E2E_SOCKET_LOG"], "a", encoding="utf-8") as log:
            log.write(f"{address[0]}:{address[1]}\\n")
        occupied = os.environ.get("ATHENA_E2E_SOCKET_OCCUPIED", "")
        if occupied != "" and address[1] == int(occupied):
            raise OSError("controlled occupied port")

    def close(self):
        return None
""",
        encoding="utf-8",
    )
    return calls


def _write_stub(directory: Path, name: str) -> None:
    path = directory / name
    path.write_text(
        """#!/bin/sh
set -eu
if [ "${ATHENA_ASSERT_PROVIDER_SCOPE:-}" = 1 ]; then
  case "${0##*/}" in
    docker)
      [ -n "${DASHSCOPE_API_KEY:-}" ] || exit 71
      [ -z "${OPENAI_API_KEY:-}${BAILIAN_API_KEY:-}${LITELLM_EMBEDDING_API_KEY:-}" ] || exit 72
      ;;
    uv)
      [ -z "${DASHSCOPE_API_KEY:-}${OPENAI_API_KEY:-}${BAILIAN_API_KEY:-}" ] || exit 73
      [ -z "${LITELLM_EMBEDDING_API_KEY:-}" ] || exit 73
      ;;
  esac
fi
if [ "${ATHENA_ASSERT_CHECK_PROVIDER_SCOPE:-}" = 1 ]; then
  case "${0##*/}" in
    uv)
      [ -n "${DASHSCOPE_API_KEY:-}" ] || exit 74
      [ -z "${OPENAI_API_KEY:-}${BAILIAN_API_KEY:-}${BAILIAN_API_BASE:-}" ] || exit 75
      [ -z "${LITELLM_EMBEDDING_API_KEY:-}${LITELLM_EMBEDDING_API_BASE:-}" ] || exit 76
      ;;
  esac
fi
{
  printf '%s\\0' "${0##*/}"
  for argument in "$@"; do printf '%s\\0' "$argument"; done
  case " $* " in
    *" scripts/milvus_bootstrap.py "*)
      printf 'TAP_ALLOW_INITIAL_MILVUS_ROOT=%s\\0' "${TAP_ALLOW_INITIAL_MILVUS_ROOT:-}"
      ;;
  esac
  printf '\\0'
} >> "$ATHENA_STUB_LOG"
exit 0
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run_make_with_stubs(
    tmp_path: Path,
    target: str,
    *assignments: str,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for command in ("bash", "docker", "uv"):
        _write_stub(stubs, command)
    log = tmp_path / "calls.bin"
    environment = dict(os.environ)
    for name in (
        "COMPOSE_FILE",
        "COMPOSE_PROJECT_NAME",
        "TAP_ALLOW_ATHENA_VOLUME_RESET",
        "TAP_ATHENA_COMPOSE_PROJECT",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "ATHENA_STUB_LOG": str(log),
            "DOCKER_HOST": "unix:///athena-contract-must-not-reach-real-docker.sock",
            "PATH": f"{stubs}:{os.environ['PATH']}",
        }
    )
    environment.update(extra_env or {})
    completed = subprocess.run(
        ["make", "--no-print-directory", target, *assignments],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    calls = (
        [
            [part.decode() for part in record.split(b"\0") if part]
            for record in log.read_bytes().split(b"\0\0")
            if record
        ]
        if log.exists()
        else []
    )
    return completed, calls


def _make_dry_run(target: str, *assignments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", "-n", target, *assignments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _load_yaml_as_json(path: Path) -> dict[str, object]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _supervisor_fixture(
    tmp_path: Path,
    *,
    api_exit: str = "",
    ready_status: str = "ready",
    term_delay: str = "0",
) -> tuple[Path, dict[str, str], Path]:
    root = tmp_path / "repo"
    scripts = root / "scripts"
    vite_dir = root / "apps/web/node_modules/.bin"
    fake_bin = root / "fake-bin"
    scripts.mkdir(parents=True)
    vite_dir.mkdir(parents=True)
    fake_bin.mkdir()
    _write_environment_name_probe(fake_bin)
    (root / "apps/web/index.html").write_text("<main>Athena</main>\n", encoding="utf-8")
    (root / "apps/web/vite.config.ts").write_text("export default {};\n", encoding="utf-8")
    supervisor = scripts / "run-athena-dev.sh"
    supervisor.write_text(
        (ROOT / "scripts/run-athena-dev.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    supervisor.chmod(supervisor.stat().st_mode | stat.S_IXUSR)
    log = root / "children.log"
    log.touch()
    temp_directory = root / "tmp"
    temp_directory.mkdir()
    child = """#!/bin/sh
set -eu
if [ "${ATHENA_ASSERT_PROVIDER_SCOPE:-}" = 1 ]; then
  [ -z "${DASHSCOPE_API_KEY:-}${OPENAI_API_KEY:-}${BAILIAN_API_KEY:-}" ] || exit 103
  [ -z "${LITELLM_EMBEDDING_API_KEY:-}${LITELLM_EMBEDDING_API_BASE:-}" ] || exit 104
fi
role="$1"
printf 'start %s %s\n' "$role" "$$" >> "$ATHENA_CHILD_LOG"
environment_names="$(athena-env-names)"
printf 'env %s %s\n' "$role" "$environment_names" >> "$ATHENA_CHILD_LOG"
trap '
  sleep "$ATHENA_STUB_TERM_DELAY"
  printf "term %s %s\\n" "$role" "$$" >> "$ATHENA_CHILD_LOG"
  exit 0
' TERM INT
if [ "$role" = api ] && [ -n "$ATHENA_STUB_API_EXIT" ]; then
  sleep 0.2
  exit "$ATHENA_STUB_API_EXIT"
fi
while :; do sleep 0.1; done
"""
    child_path = fake_bin / "athena-child"
    child_path.write_text(child, encoding="utf-8")
    child_path.chmod(child_path.stat().st_mode | stat.S_IXUSR)
    uv = fake_bin / "uv"
    uv.write_text(
        """#!/bin/sh
set -eu
if [ "${ATHENA_ASSERT_PROVIDER_SCOPE:-}" = 1 ]; then
  [ -z "${DASHSCOPE_API_KEY:-}${OPENAI_API_KEY:-}${BAILIAN_API_KEY:-}" ] || exit 103
  [ -z "${LITELLM_EMBEDDING_API_KEY:-}${LITELLM_EMBEDDING_API_BASE:-}" ] || exit 104
fi
case " $* " in
  *" python -c "*)
    environment_names="$(athena-env-names)"
    printf 'env validation %s\n' "$environment_names" >> "$ATHENA_CHILD_LOG"
    [ "$TAP_ATHENA_COMPOSE_PROJECT" = "$ATHENA_EXPECTED_PROJECT" ] || exit 89
    exit 0
    ;;
  *" python - "*)
    environment_names="$(athena-env-names)"
    printf 'env readiness %s\n' "$environment_names" >> "$ATHENA_CHILD_LOG"
    last=""
    for item in "$@"; do last="$item"; done
    grep -q '"status"[[:space:]]*:[[:space:]]*"ready"' "$last"
    exit $?
    ;;
  *" tap.entrypoints.athena_api "*) exec athena-child api ;;
  *" tap.entrypoints.relay_reconciler "*) exec athena-child relay ;;
  *" tap.entrypoints.athena_ingestion_worker "*) exec athena-child worker ;;
esac
exit 99
""",
        encoding="utf-8",
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    curl = fake_bin / "curl"
    curl.write_text(
        """#!/bin/sh
set -eu
environment_names="$(athena-env-names)"
printf 'env curl %s\n' "$environment_names" >> "$ATHENA_CHILD_LOG"
printf 'curl-argv %s\n' "$*" >> "$ATHENA_CHILD_LOG"
printf '{"status":"%s"}\n' "$ATHENA_STUB_READY_STATUS"
""",
        encoding="utf-8",
    )
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)
    vite = vite_dir / "vite"
    vite.write_text(
        """#!/bin/sh
set -eu
[ "$#" -eq 8 ] || exit 94
[ "$1" = "$ATHENA_EXPECTED_WEB_ROOT" ] || exit 95
[ "$2" = --config ] || exit 96
[ "$3" = "$ATHENA_EXPECTED_WEB_ROOT/vite.config.ts" ] || exit 97
[ "$4" = --host ] || exit 98
[ "$5" = "$ATHENA_WEB_HOST" ] || exit 99
[ "$6" = --port ] || exit 100
[ "$7" = "$ATHENA_WEB_PORT" ] || exit 101
[ "$8" = --strictPort ] || exit 102
printf 'vite-root %s config %s\n' "$1" "$3" >> "$ATHENA_CHILD_LOG"
environment_names="$(athena-env-names)"
printf 'env web %s\n' "$environment_names" >> "$ATHENA_CHILD_LOG"
exec athena-child web
""",
        encoding="utf-8",
    )
    vite.chmod(vite.stat().st_mode | stat.S_IXUSR)
    environment = dict(os.environ)
    environment.update(
        {
            "ATHENA_CHILD_LOG": str(log),
            "ATHENA_EXPECTED_PROJECT": "tap-athena-demo",
            "ATHENA_EXPECTED_WEB_ROOT": str(root / "apps/web"),
            "ATHENA_STUB_API_EXIT": api_exit,
            "ATHENA_STUB_READY_STATUS": ready_status,
            "ATHENA_STUB_TERM_DELAY": term_delay,
            "ATHENA_SUPERVISOR_ENV": "preloaded",
            "TMPDIR": str(temp_directory),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "TAP_DATABASE_URL": (
                "mysql+asyncmy://tap:provider-secret@127.0.0.1:3306/tap?charset=utf8mb4"
            ),
            "TAP_REDIS_URL": "redis://provider-secret@127.0.0.1/0",
            "AZURE_STORAGE_CONNECTION_STRING": (
                "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
                "AccountKey=provider-secret;"
                "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
            ),
            "LITELLM_MASTER_KEY": "provider-secret",
            "MILVUS_READER_PASSWORD": "provider-secret",
            "MILVUS_WRITER_PASSWORD": "provider-secret",
            "MILVUS_PROVISIONER_PASSWORD": "provider-secret",
        }
    )
    return supervisor, environment, log


def _started_child_pids(log: Path) -> list[int]:
    return [
        int(line.split()[2])
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.startswith("start ")
    ]


def _assert_processes_are_gone(pids: list[int]) -> None:
    for pid in pids:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        raise AssertionError(f"supervisor left child PID {pid} running")


def _e2e_runner_fixture(
    tmp_path: Path,
    *,
    cleanup_failure: bool = False,
    fail_phase: str = "",
    ensure_failure: bool = False,
    malformed_phase: str = "",
    readiness_failure: str = "",
    skipped_phase: str = "",
) -> tuple[Path, dict[str, str], Path]:
    root = tmp_path / "repo"
    scripts = root / "scripts"
    stubs = root / "bin"
    temporary = root / "tmp"
    scripts.mkdir(parents=True)
    stubs.mkdir()
    _write_environment_name_probe(stubs)
    temporary.mkdir()
    socket_log = _write_behavioral_socket_stub(root)
    runner = scripts / "run-athena-e2e.sh"
    runner_source = (ROOT / "scripts/run-athena-e2e.sh").read_text(encoding="utf-8")
    if readiness_failure:
        deadline = "ready_deadline=$(( SECONDS + 120 ))"
        assert runner_source.count(deadline) == 1
        runner_source = runner_source.replace(
            deadline,
            "ready_deadline=$(( SECONDS + 1 ))",
            1,
        )
    runner.write_text(runner_source, encoding="utf-8")
    runner.chmod(runner.stat().st_mode | stat.S_IXUSR)
    (root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (root / ".env").write_text(
        """TAP_ATHENA_COMPOSE_PROJECT=shared-project
MYSQL_PORT=3306
REDIS_PORT=6379
AZURITE_BLOB_PORT=10000
LITELLM_PORT=4000
MILVUS_PORT=19530
MILVUS_HEALTH_PORT=9091
ATHENA_API_PORT=8000
ATHENA_WEB_PORT=5173
TAP_DEMO_MODE=
ATHENA_MODEL_BACKEND=litellm
TAP_DATABASE_URL=mysql+asyncmy://shared@127.0.0.1:3306/tap
TAP_REDIS_URL=redis://127.0.0.1:6379/0
MILVUS_URI=http://127.0.0.1:19530
DOCKER_HOST=tcp://provider-secret.invalid:2375
AZURE_STORAGE_CONNECTION_STRING=provider-secret
OPENAI_API_KEY=provider-secret
BAILIAN_API_KEY=provider-secret
BAILIAN_API_BASE=https://provider-secret.invalid/bailian
DASHSCOPE_API_KEY=provider-secret
LITELLM_ATHENA_EMBEDDING_MODEL=provider-secret-route
LITELLM_EMBEDDING_MODEL=provider-secret-model
LITELLM_EMBEDDING_API_KEY=provider-secret
LITELLM_EMBEDDING_API_BASE=https://provider-secret.invalid/v1
CODEX_HOME=/provider-secret/codex-home
CODEX_API_KEY=provider-secret
OPENAI_BASE_URL=https://provider-secret.invalid/openai
OPENAI_API_BASE=https://provider-secret.invalid/openai-api
DASHSCOPE_BASE_URL=https://provider-secret.invalid/dashscope
DASHSCOPE_API_BASE=https://provider-secret.invalid/dashscope-api
CODEX_API_BASE=https://provider-secret.invalid/codex-api
LITELLM_MASTER_KEY=provider-secret
MILVUS_READER_PASSWORD=provider-secret
MILVUS_WRITER_PASSWORD=provider-secret
MILVUS_PROVISIONER_PASSWORD=provider-secret
preflight_only=0
e2e_project=tap-athena-demo
repo_root=/provider-secret/invalid
compose_file=/provider-secret/invalid.yaml
state_root=/provider-secret/invalid
state_dir=/provider-secret/invalid-state
lock_dir=/provider-secret/invalid-lock
apps_pid=99999999
cleanup_started=1
compose_mutated=1
lock_owned=1
""",
        encoding="utf-8",
    )
    log = root / "calls.log"
    chromium = root / "chromium"
    chromium.write_text("", encoding="utf-8")
    chromium.chmod(chromium.stat().st_mode | stat.S_IXUSR)
    corepack = stubs / "corepack"
    corepack.write_text(
        """#!/bin/sh
set -eu
case " $* " in
  *" exec node "*)
    printf 'chromium\n' >> "$ATHENA_E2E_STUB_LOG"
    printf '%s' "$ATHENA_STUB_CHROMIUM"
    ;;
  *" playwright test "*)
    printf 'playwright|%s|%s\n' "$ATHENA_E2E_PHASE" "$*" >> "$ATHENA_E2E_STUB_LOG"
    if [ "$ATHENA_STUB_FAIL_PHASE" = "$ATHENA_E2E_PHASE" ]; then exit 37; fi
    if [ "$ATHENA_STUB_MALFORMED_PHASE" = "$ATHENA_E2E_PHASE" ]; then
      printf '%s\n' '{not-json'
      exit 0
    fi
    if [ "$ATHENA_STUB_SKIPPED_PHASE" = "$ATHENA_E2E_PHASE" ]; then
      printf '%s\n' '{"stats":{"expected":1,"unexpected":0,"flaky":0,"skipped":1}}'
      exit 0
    fi
    printf '%s\n' '{"stats":{"expected":1,"unexpected":0,"flaky":0,"skipped":0}}'
    ;;
  *) exit 98 ;;
esac
""",
        encoding="utf-8",
    )
    corepack.chmod(corepack.stat().st_mode | stat.S_IXUSR)
    uv = stubs / "uv"
    uv.write_text(
        """#!/bin/sh
set -eu
case " $* " in
  *" python -c "*)
    printf 'uv|settings\n' >> "$ATHENA_E2E_STUB_LOG"
    environment_names="$(athena-env-names)"
    printf 'env|settings|%s\n' "$environment_names" >> "$ATHENA_E2E_STUB_LOG"
    [ "$TAP_DEMO_MODE:$ATHENA_MODEL_BACKEND:$ATHENA_ANSWER_BACKEND" = e2e:fake:litellm ] || exit 71
    [ "$LITELLM_MODEL" = dashscope/e2e-chat-unused ] || exit 72
    [ "$LITELLM_ATHENA_EMBEDDING_MODEL" = dashscope/text-embedding-v4 ] || exit 72
    [ "$LITELLM_EMBEDDING_MODEL" = text-embedding-v4 ] || exit 73
    [ "$DASHSCOPE_API_KEY" = tap-e2e-unused ] || exit 75
    [ "$DASHSCOPE_API_BASE" = http://127.0.0.1:14000 ] || exit 76
    [ -z "${OPENAI_API_KEY+x}${BAILIAN_API_KEY+x}${BAILIAN_API_BASE+x}" ] || exit 77
    [ -z "${LITELLM_EMBEDDING_API_KEY+x}${LITELLM_EMBEDDING_API_BASE+x}" ] || exit 78
    [ -z "${CODEX_HOME+x}${CODEX_API_KEY+x}" ] || exit 78
    ;;
  *" python - "*)
    printf 'uv|probe\n' >> "$ATHENA_E2E_STUB_LOG"
    shift 4
    exec "$ATHENA_TEST_PYTHON" "$@"
    ;;
  *" alembic "*) printf 'uv|alembic\n' >> "$ATHENA_E2E_STUB_LOG" ;;
  *" scripts/milvus_bootstrap.py "*)
    [ "${TAP_ALLOW_INITIAL_MILVUS_ROOT:-}" = 1 ] || exit 91
    printf 'uv|bootstrap|initial=1\n' >> "$ATHENA_E2E_STUB_LOG"
    ;;
  *" scripts/athena_collection.py ensure "*)
    printf 'uv|ensure\n' >> "$ATHENA_E2E_STUB_LOG"
    [ "$ATHENA_STUB_ENSURE_FAILURE" != 1 ] || exit 41
    ;;
  *"test_athena_persistence_restart.py "*)
    [ "${ATHENA_E2E_PHASE:-}:${TAP_RUN_ATHENA_E2E:-}" = verify:1 ] || exit 92
    printf 'uv|verify|%s\n' "$ATHENA_E2E_PHASE" >> "$ATHENA_E2E_STUB_LOG"
    ;;
  *) exit 97 ;;
esac
""",
        encoding="utf-8",
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    docker = stubs / "docker"
    docker.write_text(
        """#!/bin/sh
set -eu
case " $* " in
  " context show ") printf 'context|show\n' >> "$ATHENA_E2E_STUB_LOG"; printf 'default\n' ;;
  *" context inspect "*)
    printf 'context|inspect\n' >> "$ATHENA_E2E_STUB_LOG"
    printf 'unix:///tmp/docker.sock\n'
    ;;
  *" compose "*)
    [ "$TAP_ATHENA_COMPOSE_PROJECT" = tap-athena-e2e ] || exit 81
    middleware_ports="$MYSQL_PORT:$REDIS_PORT:$AZURITE_BLOB_PORT:$LITELLM_PORT"
    [ "$middleware_ports" = 13306:16379:11000:14000 ] || exit 82
    app_ports="$MILVUS_PORT:$MILVUS_HEALTH_PORT:$ATHENA_API_PORT:$ATHENA_WEB_PORT"
    [ "$app_ports" = 29530:19091:18000:15173 ] || exit 83
    [ "$TAP_DEMO_MODE:$ATHENA_MODEL_BACKEND:$ATHENA_ANSWER_BACKEND" = e2e:fake:litellm ] || exit 84
    expected_database='mysql+asyncmy://tap:tap-e2e@127.0.0.1:13306/tap?charset=utf8mb4'
    [ "$TAP_DATABASE_URL" = "$expected_database" ] || exit 85
    [ "$TAP_REDIS_URL" = 'redis://127.0.0.1:16379/0' ] || exit 86
    [ "$MILVUS_URI" = 'http://127.0.0.1:29530' ] || exit 87
    [ -z "${OPENAI_API_KEY+x}${BAILIAN_API_KEY+x}${BAILIAN_API_BASE+x}" ] || exit 89
    [ -z "${LITELLM_EMBEDDING_API_KEY+x}" ] || exit 90
    [ -z "${LITELLM_EMBEDDING_API_BASE+x}" ] || exit 94
    case "$AZURE_STORAGE_CONNECTION_STRING" in
      *'BlobEndpoint=http://127.0.0.1:11000/devstoreaccount1;'*) ;;
      *) exit 88 ;;
    esac
    printf 'docker|%s\n' "$*" >> "$ATHENA_E2E_STUB_LOG"
    case " $* " in
      *" down --volumes --remove-orphans "*)
        cleanup_marker="$ATHENA_E2E_STUB_LOG.down-volumes"
        if [ "$ATHENA_STUB_CLEANUP_FAILURE" = 1 ] && [ -f "$cleanup_marker" ]; then
          exit 93
        fi
        : > "$cleanup_marker"
        ;;
    esac
    ;;
  *) exit 96 ;;
esac
""",
        encoding="utf-8",
    )
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    curl = stubs / "curl"
    curl.write_text(
        """#!/bin/sh
set -eu
case " $* " in
  *" --max-filesize 65536 "*) ;;
  *) exit 95 ;;
esac
case " $* " in
  *"/health/ready"*)
    if [ "${ATHENA_STUB_READINESS_FAILURE:-}" = api-http ]; then
      printf '%s\n' '{"status":"ready"}'
      exit 22
    fi
    if [ "${ATHENA_STUB_READINESS_FAILURE:-}" = api-body ]; then
      printf '%s\n' '{"status":"unready"}'
    else
      printf '%s\n' '{"status":"ready"}'
    fi
    ;;
  *"15173/"*)
    [ "${ATHENA_STUB_READINESS_FAILURE:-}" != web-http ] || exit 22
    printf '%s\n' '<html></html>'
    ;;
  *) exit 94 ;;
esac
""",
        encoding="utf-8",
    )
    curl.chmod(curl.stat().st_mode | stat.S_IXUSR)
    (scripts / "run-athena-dev.sh").write_text(
        """#!/bin/bash
set -eu
printf 'apps|start|%s\n' "$$" >> "$ATHENA_E2E_STUB_LOG"
[ "${ATHENA_STUB_READINESS_FAILURE:-}" != supervisor ] || exit 17
trap 'printf "apps|term|%s\\n" "$$" >> "$ATHENA_E2E_STUB_LOG"; exit 143' TERM
while :; do sleep 0.1; done
""",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.update(
        {
            "ATHENA_E2E_STUB_LOG": str(log),
            "ATHENA_E2E_SOCKET_OCCUPIED": "",
            "ATHENA_STUB_CLEANUP_FAILURE": "1" if cleanup_failure else "0",
            "ATHENA_STUB_CHROMIUM": str(chromium),
            "ATHENA_STUB_FAIL_PHASE": fail_phase,
            "ATHENA_STUB_ENSURE_FAILURE": "1" if ensure_failure else "0",
            "ATHENA_STUB_MALFORMED_PHASE": malformed_phase,
            "ATHENA_STUB_READINESS_FAILURE": readiness_failure,
            "ATHENA_STUB_SKIPPED_PHASE": skipped_phase,
            "ATHENA_E2E_SOCKET_LOG": str(socket_log),
            "ATHENA_TEST_PYTHON": sys.executable,
            "PATH": f"{stubs}:{os.environ['PATH']}",
            "TMPDIR": str(temporary),
        }
    )
    return runner, environment, log


def test_five_public_demo_targets_and_separate_reset_resolve() -> None:
    """Removing a public command or folding destructive reset into normal flow breaks the CLI."""

    recipes = {
        target: _make_dry_run(target)
        for target in (
            "demo-up",
            "demo-check",
            "demo-dev",
            "demo-e2e",
            "demo-down",
            "demo-reset",
        )
    }

    assert all(result.returncode == 0 for result in recipes.values())
    assert "scripts/check-athena-demo.py" in recipes["demo-check"].stdout
    assert "scripts/run-athena-dev.sh" in recipes["demo-dev"].stdout
    assert "scripts/run-athena-e2e.sh" in recipes["demo-e2e"].stdout
    assert "upgrade head" in recipes["demo-up"].stdout
    assert "scripts/athena_collection.py ensure" in recipes["demo-up"].stdout
    assert "--remove-orphans" in recipes["demo-down"].stdout


def test_demo_down_never_removes_named_volumes() -> None:
    """Ordinary shutdown must retain durable MySQL, Blob, and Milvus data."""

    completed = _make_dry_run("demo-down")

    assert completed.returncode == 0, completed.stderr
    words = completed.stdout.replace("\\\n", " ").split()
    assert "down" in words
    assert "-v" not in words
    assert "--volumes" not in words


def test_demo_down_executes_only_exact_project_scoped_non_volume_argv(
    tmp_path: Path,
) -> None:
    completed, calls = _run_make_with_stubs(tmp_path, "demo-down")

    assert completed.returncode == 0, completed.stderr
    assert calls == [
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "compose.yaml"),
            "-p",
            "tap-athena-demo",
            "--profile",
            "milvus",
            "down",
            "--remove-orphans",
        ]
    ]


def test_invalid_project_fails_before_fake_docker_is_invoked(tmp_path: Path) -> None:
    """An unresolved or malformed project must never reach the Docker boundary."""

    marker = tmp_path / "docker-called"
    docker = tmp_path / "docker"
    docker.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 99\n", encoding="utf-8")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    completed = subprocess.run(
        ["make", "--no-print-directory", "demo-down", "TAP_ATHENA_COMPOSE_PROJECT=Bad Project"],
        cwd=ROOT,
        env=os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert not marker.exists()
    assert "invalid Athena Compose project" in completed.stderr


def test_reset_requires_both_exact_project_and_opt_in_before_fake_docker(tmp_path: Path) -> None:
    """Neither an opt-in alone nor a project alone may delete named volumes."""

    marker = tmp_path / "docker-called"
    docker = tmp_path / "docker"
    docker.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 99\n", encoding="utf-8")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    base_env = os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}"}

    without_opt_in = subprocess.run(
        ["make", "--no-print-directory", "demo-reset"],
        cwd=ROOT,
        env=base_env,
        text=True,
        capture_output=True,
        check=False,
    )
    wrong_project = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "demo-reset",
            "TAP_ATHENA_COMPOSE_PROJECT=tap-athena-e2e",
            "TAP_ALLOW_ATHENA_VOLUME_RESET=1",
        ],
        cwd=ROOT,
        env=base_env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert without_opt_in.returncode != 0
    assert wrong_project.returncode != 0
    assert not marker.exists()


def test_valid_reset_targets_only_exact_default_project_volumes(tmp_path: Path) -> None:
    completed, calls = _run_make_with_stubs(
        tmp_path,
        "demo-reset",
        extra_env={"TAP_ALLOW_ATHENA_VOLUME_RESET": "1"},
    )

    assert completed.returncode == 0, completed.stderr
    assert calls == [
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "compose.yaml"),
            "-p",
            "tap-athena-demo",
            "--profile",
            "milvus",
            "down",
            "--volumes",
            "--remove-orphans",
        ]
    ]


def test_env_file_cannot_authorize_reset_or_run_before_invalid_project_guard(
    tmp_path: Path,
) -> None:
    isolated = tmp_path / "repo"
    isolated.mkdir()
    (isolated / "Makefile").write_text(
        (ROOT / "Makefile").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (isolated / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    side_effect = isolated / "env-sourced"
    (isolated / ".env").write_text(
        f"TAP_ALLOW_ATHENA_VOLUME_RESET=1\ntouch '{side_effect}'\n",
        encoding="utf-8",
    )
    stubs = isolated / "bin"
    stubs.mkdir()
    _write_stub(stubs, "docker")
    log = isolated / "calls.bin"
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"TAP_ALLOW_ATHENA_VOLUME_RESET", "TAP_ATHENA_COMPOSE_PROJECT"}
    } | {
        "ATHENA_STUB_LOG": str(log),
        "DOCKER_HOST": "unix:///athena-contract-must-not-reach-real-docker.sock",
        "PATH": f"{stubs}:{os.environ['PATH']}",
    }

    reset = subprocess.run(
        ["make", "--no-print-directory", "demo-reset"],
        cwd=isolated,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    invalid = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "demo-up",
            "TAP_ATHENA_COMPOSE_PROJECT=Bad Project",
        ],
        cwd=isolated,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert reset.returncode != 0
    assert invalid.returncode != 0
    assert not log.exists()
    assert not side_effect.exists()


def test_demo_up_reasserts_captured_project_after_dotenv_shell_collisions(
    tmp_path: Path,
) -> None:
    isolated = tmp_path / "repo"
    isolated.mkdir()
    (isolated / "Makefile").write_text(
        (ROOT / "Makefile").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (isolated / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (isolated / ".env").write_text(
        """project=tap-hostile
repo_root=/provider-secret/invalid
TAP_REPO_ROOT=/provider-secret/invalid
TAP_ATHENA_COMPOSE_PROJECT=tap-hostile
""",
        encoding="utf-8",
    )
    stubs = isolated / "bin"
    stubs.mkdir()
    marker = isolated / "projects.log"
    for command in ("docker", "uv"):
        path = stubs / command
        path.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            '[ "$TAP_ATHENA_COMPOSE_PROJECT" = tap-athena-demo ] || exit 88\n'
            f"printf '%s\\n' \"$TAP_ATHENA_COMPOSE_PROJECT\" >> '{marker}'\n",
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    completed = subprocess.run(
        ["make", "--no-print-directory", "demo-up"],
        cwd=isolated,
        env=os.environ
        | {
            "DOCKER_HOST": "unix:///athena-contract-must-not-reach-real-docker.sock",
            "PATH": f"{stubs}:{os.environ['PATH']}",
            "TAP_ATHENA_COMPOSE_PROJECT": "tap-athena-demo",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert marker.read_text(encoding="utf-8").splitlines() == [
        "tap-athena-demo",
        "tap-athena-demo",
        "tap-athena-demo",
        "tap-athena-demo",
    ]
    assert "provider-secret" not in completed.stdout + completed.stderr


def test_demo_up_executes_compose_migration_bootstrap_and_exact_ensure_in_order(
    tmp_path: Path,
) -> None:
    completed, calls = _run_make_with_stubs(tmp_path, "demo-up")

    assert completed.returncode == 0, completed.stderr
    assert calls == [
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "compose.yaml"),
            "-p",
            "tap-athena-demo",
            "--profile",
            "milvus",
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            "180",
        ],
        [
            "uv",
            "run",
            "--project",
            "apps/backend",
            "alembic",
            "-c",
            "apps/backend/alembic.ini",
            "upgrade",
            "head",
        ],
        [
            "uv",
            "run",
            "--project",
            "apps/backend",
            "python",
            "scripts/milvus_bootstrap.py",
            "TAP_ALLOW_INITIAL_MILVUS_ROOT=1",
        ],
        [
            "uv",
            "run",
            "--project",
            "apps/backend",
            "python",
            "scripts/athena_collection.py",
            "ensure",
        ],
    ]


def test_demo_up_scopes_provider_secrets_to_the_gateway_process(
    tmp_path: Path,
) -> None:
    completed, _calls = _run_make_with_stubs(
        tmp_path,
        "demo-up",
        extra_env={
            "ATHENA_ASSERT_PROVIDER_SCOPE": "1",
            "DASHSCOPE_API_KEY": "current-provider-secret",
            "OPENAI_API_KEY": "legacy-openai-secret",
            "BAILIAN_API_KEY": "legacy-bailian-secret",
            "LITELLM_EMBEDDING_API_KEY": "direct-research-secret",
        },
    )

    assert completed.returncode == 0, completed.stderr


def test_demo_check_keeps_only_the_current_gateway_provider_secret(
    tmp_path: Path,
) -> None:
    completed, _calls = _run_make_with_stubs(
        tmp_path,
        "demo-check",
        extra_env={
            "ATHENA_ASSERT_CHECK_PROVIDER_SCOPE": "1",
            "DASHSCOPE_API_KEY": "current-provider-secret",
            "OPENAI_API_KEY": "legacy-openai-secret",
            "BAILIAN_API_KEY": "legacy-bailian-secret",
            "BAILIAN_API_BASE": "https://legacy-provider-secret.invalid/v1",
            "LITELLM_EMBEDDING_API_KEY": "direct-research-secret",
            "LITELLM_EMBEDDING_API_BASE": "https://research-secret.invalid/v1",
        },
    )

    assert completed.returncode == 0, completed.stderr


def test_compose_file_boundary_ignores_command_line_curdir_override(tmp_path: Path) -> None:
    completed, calls = _run_make_with_stubs(
        tmp_path,
        "demo-down",
        "CURDIR=/tmp/attacker-controlled",
    )

    assert completed.returncode == 0, completed.stderr
    assert calls[0][3] == str(ROOT / "compose.yaml")


def test_demo_dry_run_never_renders_connection_strings_or_credentials() -> None:
    completed = _make_dry_run("demo-up")
    rendered = completed.stdout + completed.stderr

    assert completed.returncode == 0
    assert "://" not in rendered
    assert "tap:tap" not in rendered
    assert "AccountKey=" not in rendered


def test_athena_ensure_creates_and_verifies_both_private_containers_before_index(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from tap.entrypoints.athena_runtime import AthenaSettings

    spec = importlib.util.spec_from_file_location(
        "athena_collection_contract",
        ROOT / "scripts/athena_collection.py",
    )
    assert spec is not None and spec.loader is not None
    athena_collection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(athena_collection)

    events: list[str] = []

    class Engine:
        async def dispose(self) -> None:
            events.append("close:engine")

    class Blob:
        async def ensure_containers(self) -> None:
            events.append("ensure:containers")

        async def container_properties(self, name: str) -> dict[str, object]:
            events.append(f"verify:{name}")
            return {"public_access": None}

        async def aclose(self) -> None:
            events.append("close:blob")

    class Receipt:
        physical_collection = "kb_doc_v1_athena_demo"
        alias = "kb_doc_athena_demo_active"

    class Index:
        async def ensure_target(self) -> Receipt:
            events.append("ensure:index")
            return Receipt()

        async def close(self) -> None:
            events.append("close:index")

    async def database(_settings):  # type: ignore[no-untyped-def]
        return Engine(), object()

    async def index(_settings, _engine):  # type: ignore[no-untyped-def]
        return Index()

    monkeypatch.setattr(athena_collection, "_create_database", database)
    monkeypatch.setattr(athena_collection, "_create_blob", lambda _settings: Blob())
    monkeypatch.setattr(athena_collection, "_create_document_index", index)
    settings = AthenaSettings.from_mapping(
        {
            "LITELLM_MODEL": "openai/test-chat",
            "LITELLM_ATHENA_EMBEDDING_MODEL": "dashscope/text-embedding-v4",
        }
    )

    asyncio.run(athena_collection.ensure(settings))

    assert events == [
        "ensure:containers",
        "verify:athena-originals",
        "verify:athena-artifacts",
        "ensure:index",
        "close:index",
        "close:blob",
        "close:engine",
    ]


@pytest.mark.parametrize(
    ("failure_point", "expected_stage", "expected_events"),
    [
        ("database", "database", ["database"]),
        ("blob-client", "blob-containers", ["database", "blob", "close:engine"]),
        (
            "blob-containers",
            "blob-containers",
            ["database", "blob", "blob:containers", "close:blob", "close:engine"],
        ),
        (
            "blob-properties",
            "blob-containers",
            [
                "database",
                "blob",
                "blob:containers",
                "blob:athena-originals",
                "close:blob",
                "close:engine",
            ],
        ),
        (
            "milvus-client",
            "milvus-client",
            [
                "database",
                "blob",
                "blob:containers",
                "blob:athena-originals",
                "blob:athena-artifacts",
                "milvus-client",
                "close:blob",
                "close:engine",
            ],
        ),
        (
            "milvus-target",
            "milvus-target",
            [
                "database",
                "blob",
                "blob:containers",
                "blob:athena-originals",
                "blob:athena-artifacts",
                "milvus-client",
                "milvus-target",
                "close:index",
                "close:blob",
                "close:engine",
            ],
        ),
        (
            "milvus-receipt",
            "milvus-target",
            [
                "database",
                "blob",
                "blob:containers",
                "blob:athena-originals",
                "blob:athena-artifacts",
                "milvus-client",
                "milvus-target",
                "close:index",
                "close:blob",
                "close:engine",
            ],
        ),
    ],
)
def test_athena_ensure_cli_reports_only_the_closed_failure_stage_and_settles_prior_owners(
    monkeypatch,
    capsys,
    failure_point: str,
    expected_stage: str,
    expected_events: list[str],
) -> None:  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        f"athena_collection_{failure_point}_contract",
        ROOT / "scripts/athena_collection.py",
    )
    assert spec is not None and spec.loader is not None
    athena_collection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(athena_collection)
    events: list[str] = []

    class Engine:
        async def dispose(self) -> None:
            events.append("close:engine")

    class Blob:
        async def ensure_containers(self) -> None:
            events.append("blob:containers")
            if failure_point == "blob-containers":
                raise RuntimeError("provider-secret-detail")

        async def container_properties(self, name: str) -> dict[str, object]:
            events.append(f"blob:{name}")
            if failure_point == "blob-properties":
                raise RuntimeError("provider-secret-detail")
            return {"public_access": None}

        async def aclose(self) -> None:
            events.append("close:blob")

    class Index:
        async def ensure_target(self) -> object:
            events.append("milvus-target")
            if failure_point == "milvus-target":
                raise RuntimeError("provider-secret-detail")
            return type(
                "Receipt",
                (),
                {
                    "physical_collection": (
                        "wrong_collection"
                        if failure_point == "milvus-receipt"
                        else "kb_doc_v1_athena_demo"
                    ),
                    "alias": "kb_doc_athena_demo_active",
                },
            )()

        async def close(self) -> None:
            events.append("close:index")

    async def database(_settings):  # type: ignore[no-untyped-def]
        events.append("database")
        if failure_point == "database":
            raise RuntimeError("provider-secret-detail")
        return Engine(), object()

    def blob(_settings):  # type: ignore[no-untyped-def]
        events.append("blob")
        if failure_point == "blob-client":
            raise RuntimeError("provider-secret-detail")
        return Blob()

    async def index(_settings, _engine):  # type: ignore[no-untyped-def]
        events.append("milvus-client")
        if failure_point == "milvus-client":
            raise RuntimeError("provider-secret-detail")
        return Index()

    monkeypatch.setattr(athena_collection, "_create_database", database)
    monkeypatch.setattr(athena_collection, "_create_blob", blob)
    monkeypatch.setattr(athena_collection, "_create_document_index", index)
    result = athena_collection.main(
        ["ensure"],
        {
            "LITELLM_MODEL": "openai/test-chat",
            "LITELLM_ATHENA_EMBEDDING_MODEL": "dashscope/text-embedding-v4",
        },
    )
    output = capsys.readouterr()

    assert result == 1
    assert events == expected_events
    assert output.out == ""
    assert output.err == (
        f"Athena resource ensure failed at {expected_stage}; "
        "check local middleware configuration.\n"
    )
    assert "provider-secret-detail" not in output.err


def test_athena_ensure_cli_reports_configuration_failure_before_any_resource_stage(
    monkeypatch,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "athena_collection_configuration_contract",
        ROOT / "scripts/athena_collection.py",
    )
    assert spec is not None and spec.loader is not None
    athena_collection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(athena_collection)
    calls: list[str] = []
    monkeypatch.setattr(
        athena_collection,
        "ensure",
        lambda *_args, **_kwargs: calls.append("resource"),
    )

    result = athena_collection.main(
        ["ensure"],
        {
            "ATHENA_API_HOST": "provider-secret-invalid-host",
            "LITELLM_MODEL": "openai/test-chat",
            "LITELLM_ATHENA_EMBEDDING_MODEL": "dashscope/text-embedding-v4",
        },
    )
    output = capsys.readouterr()

    assert result == 1
    assert calls == []
    assert output.out == ""
    assert output.err == (
        "Athena resource ensure failed at configuration; check local middleware configuration.\n"
    )
    assert "provider-secret-invalid-host" not in output.err


def test_athena_ensure_parses_codex_selection_without_discovery(
    monkeypatch,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    import shutil

    spec = importlib.util.spec_from_file_location(
        "athena_collection_codex_configuration_contract",
        ROOT / "scripts/athena_collection.py",
    )
    assert spec is not None and spec.loader is not None
    athena_collection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(athena_collection)
    seen: list[str] = []

    async def ensure(settings, **_kwargs):  # type: ignore[no-untyped-def]
        seen.append(settings.answer_backend)

    def forbidden_discovery(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("collection ensure performed Codex discovery")

    monkeypatch.setattr(shutil, "which", forbidden_discovery)
    monkeypatch.setattr(athena_collection, "ensure", ensure)

    result = athena_collection.main(
        ["ensure"],
        {
            "ATHENA_ANSWER_BACKEND": "codex",
            "LITELLM_MODEL": "openai/test-chat",
            "LITELLM_EMBEDDING_MODEL": "dashscope/text-embedding-v4",
        },
    )

    output = capsys.readouterr()
    assert result == 0
    assert seen == ["codex"]
    assert output.out == "Athena resources ready.\n"
    assert output.err == ""


def test_athena_ensure_cli_redacts_provider_failures(
    monkeypatch,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    from tap.modules.knowledge.ports.errors import IndexUnavailable

    spec = importlib.util.spec_from_file_location(
        "athena_collection_failure_contract",
        ROOT / "scripts/athena_collection.py",
    )
    assert spec is not None and spec.loader is not None
    athena_collection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(athena_collection)

    async def fail(_settings, *, stage):  # type: ignore[no-untyped-def]
        stage.set("milvus-target")
        raise IndexUnavailable("provider-secret-detail")

    monkeypatch.setattr(athena_collection, "ensure", fail)
    result = athena_collection.main(
        ["ensure"],
        {
            "LITELLM_MODEL": "openai/test-chat",
            "LITELLM_ATHENA_EMBEDDING_MODEL": "dashscope/text-embedding-v4",
        },
    )
    output = capsys.readouterr()

    assert result == 1
    assert output.out == ""
    assert output.err == (
        "Athena resource ensure failed at milvus-target; check local middleware configuration.\n"
    )
    assert "provider-secret-detail" not in output.err


def test_athena_ensure_cli_maps_keyboard_interrupt_to_130_without_output(
    monkeypatch,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "athena_collection_keyboard_interrupt_contract",
        ROOT / "scripts/athena_collection.py",
    )
    assert spec is not None and spec.loader is not None
    athena_collection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(athena_collection)

    async def interrupt(_settings, *, stage):  # type: ignore[no-untyped-def]
        stage.set("milvus-target")
        raise KeyboardInterrupt("provider-secret-interrupt")

    monkeypatch.setattr(athena_collection, "ensure", interrupt)

    result = athena_collection.main(
        ["ensure"],
        {
            "LITELLM_MODEL": "openai/test-chat",
            "LITELLM_ATHENA_EMBEDDING_MODEL": "dashscope/text-embedding-v4",
        },
    )
    output = capsys.readouterr()

    assert result == 130
    assert output.out == ""
    assert output.err == ""


def test_athena_ensure_cli_redacts_direct_cancelled_error(
    monkeypatch,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "athena_collection_cancelled_contract",
        ROOT / "scripts/athena_collection.py",
    )
    assert spec is not None and spec.loader is not None
    athena_collection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(athena_collection)

    async def cancel(_settings, *, stage):  # type: ignore[no-untyped-def]
        stage.set("milvus-client")
        raise asyncio.CancelledError("provider-secret-cancelled")

    monkeypatch.setattr(athena_collection, "ensure", cancel)

    result = athena_collection.main(
        ["ensure"],
        {
            "LITELLM_MODEL": "openai/test-chat",
            "LITELLM_ATHENA_EMBEDDING_MODEL": "dashscope/text-embedding-v4",
        },
    )
    output = capsys.readouterr()

    assert result == 1
    assert output.out == ""
    assert output.err == (
        "Athena resource ensure failed at milvus-client; check local middleware configuration.\n"
    )
    assert "provider-secret" not in output.err


def test_athena_ensure_cli_redacts_cancelled_error_and_cleanup_group(
    monkeypatch,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "athena_collection_cancelled_cleanup_contract",
        ROOT / "scripts/athena_collection.py",
    )
    assert spec is not None and spec.loader is not None
    athena_collection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(athena_collection)
    events: list[str] = []

    class Engine:
        async def dispose(self) -> None:
            events.append("close:engine")

    class Blob:
        async def ensure_containers(self) -> None:
            events.append("ensure:containers")

        async def container_properties(self, name: str) -> dict[str, object]:
            events.append(f"verify:{name}")
            return {"public_access": None}

        async def aclose(self) -> None:
            events.append("close:blob")
            raise RuntimeError("provider-secret-cleanup")

    class Index:
        async def ensure_target(self) -> object:
            events.append("ensure:index")
            raise asyncio.CancelledError("provider-secret-primary")

        async def close(self) -> None:
            events.append("close:index")

    async def database(_settings):  # type: ignore[no-untyped-def]
        events.append("database")
        return Engine(), object()

    async def index(_settings, _engine):  # type: ignore[no-untyped-def]
        events.append("index")
        return Index()

    monkeypatch.setattr(athena_collection, "_create_database", database)
    monkeypatch.setattr(athena_collection, "_create_blob", lambda _settings: Blob())
    monkeypatch.setattr(athena_collection, "_create_document_index", index)

    result = athena_collection.main(
        ["ensure"],
        {
            "LITELLM_MODEL": "openai/test-chat",
            "LITELLM_ATHENA_EMBEDDING_MODEL": "dashscope/text-embedding-v4",
        },
    )
    output = capsys.readouterr()

    assert result == 1
    assert events == [
        "database",
        "ensure:containers",
        "verify:athena-originals",
        "verify:athena-artifacts",
        "index",
        "ensure:index",
        "close:index",
        "close:blob",
        "close:engine",
    ]
    assert output.out == ""
    assert output.err == (
        "Athena resource ensure failed at milvus-target; check local middleware configuration.\n"
    )
    assert "provider-secret" not in output.err


@pytest.mark.parametrize(
    "target_stage",
    (
        "authority",
        "discovery",
        "collection-create",
        "collection-schema-observe",
        "collection-schema-envelope",
        "collection-schema-properties",
        "collection-schema-aliases",
        "collection-schema-identity",
        "collection-schema-metadata",
        "collection-schema-fields",
        "collection-schema-functions",
        "collection-schema-vector",
        "collection-schema-binding",
        "indexes",
        "load",
        "grants",
        "alias",
        "authority-sync",
        "cleanup",
    ),
)
def test_athena_ensure_cli_maps_only_closed_target_failure_stages(
    monkeypatch,
    capsys,
    target_stage: str,
) -> None:  # type: ignore[no-untyped-def]
    from tap.modules.knowledge.adapters.milvus_documents import (
        IndexTargetProvisioningFailed,
        _IndexTargetStage,
    )

    spec = importlib.util.spec_from_file_location(
        f"athena_collection_target_{target_stage}_contract",
        ROOT / "scripts/athena_collection.py",
    )
    assert spec is not None and spec.loader is not None
    athena_collection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(athena_collection)

    async def fail(_settings, *, stage):  # type: ignore[no-untyped-def]
        stage.set("milvus-target")
        raise IndexTargetProvisioningFailed(_IndexTargetStage(target_stage))

    monkeypatch.setattr(athena_collection, "ensure", fail)

    result = athena_collection.main(
        ["ensure"],
        {
            "LITELLM_MODEL": "openai/test-chat",
            "LITELLM_ATHENA_EMBEDDING_MODEL": "dashscope/text-embedding-v4",
        },
    )
    output = capsys.readouterr()

    assert result == 1
    assert output.out == ""
    assert output.err == (
        f"Athena resource ensure failed at milvus-target-{target_stage}; "
        "check local middleware configuration.\n"
    )


def _capture_provider_rpc_logs() -> tuple[logging.Logger, logging.Handler, io.StringIO]:
    logger = logging.getLogger("pymilvus.decorators")
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    logger.addHandler(handler)
    logger.setLevel(logging.ERROR)
    return logger, handler, output


def _emit_provider_rpc_error(details: str) -> None:
    try:
        raise RuntimeError(details)
    except RuntimeError:
        _log_rpc_error("synthetic_call", "RPC error", details, time.monotonic())


def test_athena_ensure_cli_suppresses_worker_thread_rpc_details(
    monkeypatch,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "athena_collection_rpc_log_contract",
        ROOT / "scripts/athena_collection.py",
    )
    assert spec is not None and spec.loader is not None
    athena_collection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(athena_collection)
    logger, handler, provider_output = _capture_provider_rpc_logs()

    async def fail(_settings, *, stage):  # type: ignore[no-untyped-def]
        stage.set("milvus-target")
        await asyncio.to_thread(_emit_provider_rpc_error, "provider-secret-rpc-detail")
        raise RuntimeError("provider failure")

    monkeypatch.setattr(athena_collection, "ensure", fail)
    try:
        result = athena_collection.main(
            ["ensure"],
            {
                "LITELLM_MODEL": "openai/test-chat",
                "LITELLM_ATHENA_EMBEDDING_MODEL": "dashscope/text-embedding-v4",
            },
        )
        _emit_provider_rpc_error("ensure-filter-restored-after-main")
    finally:
        logger.removeHandler(handler)
    output = capsys.readouterr()

    assert result == 1
    assert output.out == ""
    assert output.err == (
        "Athena resource ensure failed at milvus-target; check local middleware configuration.\n"
    )
    assert "provider-secret-rpc-detail" not in provider_output.getvalue()
    assert "ensure-filter-restored-after-main" in provider_output.getvalue()


def test_owned_milvus_fixture_settles_coordinator_and_role_clients_if_index_construction_fails(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TAP_RUN_MILVUS_INTEGRATION", "1")
    monkeypatch.setenv("TAP_MILVUS_OWNED_INSTANCE", "task5-athena-owned")
    spec = importlib.util.spec_from_file_location(
        "athena_projection_fixture_ownership_contract",
        ROOT / "apps/backend/tests/integration/test_athena_milvus_projection.py",
    )
    assert spec is not None and spec.loader is not None
    projection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(projection)
    events: list[str] = []
    primary = RuntimeError("index-construction-primary")

    class Admin:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def close(self) -> None:
            events.append("admin")

    class Engine:
        async def dispose(self) -> None:
            events.append("engine")

    class Resource:
        def __init__(self, name: str) -> None:
            self.name = name

        async def close(self) -> None:
            events.append(self.name)

    class Coordinator:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def close(self) -> None:
            events.append("coordinator")

    async def clients(**_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            provisioner=Resource("provisioner"),
            writer=Resource("writer"),
            reader=Resource("reader"),
        )

    def fail_index(**_kwargs: object) -> None:
        raise primary

    monkeypatch.setattr(projection, "MilvusClient", Admin)
    monkeypatch.setattr(
        projection,
        "create_engine_and_session_factory",
        lambda _url: (Engine(), object()),
    )
    monkeypatch.setattr(projection, "create_athena_document_clients", clients)
    monkeypatch.setattr(projection, "MysqlProjectionCoordinator", Coordinator)
    monkeypatch.setattr(projection, "MilvusDocumentIndex", fail_index)
    monkeypatch.setattr(projection, "sdk", lambda: object())

    async def scenario() -> BaseException:
        fixture = projection._real_index.__wrapped__()
        try:
            await fixture.__anext__()
        except BaseException as error:
            return error
        raise AssertionError("fixture unexpectedly yielded after index construction failure")

    captured = asyncio.run(scenario())

    assert captured is primary
    assert events == [
        "reader",
        "writer",
        "provisioner",
        "coordinator",
        "engine",
        "admin",
    ]


def _load_safe_check_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "athena_safe_check_contract",
        ROOT / "scripts/check-athena-demo.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_safe_check_runs_all_five_probes_independently(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from tap.entrypoints.athena_runtime import AthenaSettings

    safe_check = _load_safe_check_module()
    settings = AthenaSettings.from_mapping(
        {
            "LITELLM_MODEL": "openai/test-chat",
            "LITELLM_ATHENA_EMBEDDING_MODEL": "dashscope/text-embedding-v4",
        }
    )
    events: list[str] = []

    def probe(name: str, result: bool = True):  # type: ignore[no-untyped-def]
        async def run(_settings, _values):  # type: ignore[no-untyped-def]
            events.append(name)
            if name == "redis":
                raise RuntimeError("provider-secret-detail")
            return result

        return run

    for name in ("mysql", "redis", "blob", "milvus", "models"):
        monkeypatch.setattr(safe_check, f"_check_{name}", probe(name))

    states = asyncio.run(safe_check.checks(settings, {}))

    assert set(events) == {"mysql", "redis", "blob", "milvus", "models"}
    assert states == {
        "mysql": True,
        "redis": False,
        "blob": True,
        "milvus": True,
        "models": True,
    }


def test_safe_check_cli_suppresses_worker_thread_rpc_details(
    monkeypatch,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    safe_check = _load_safe_check_module()
    logger, handler, provider_output = _capture_provider_rpc_logs()

    async def noisy_checks(_settings, _values):  # type: ignore[no-untyped-def]
        await asyncio.to_thread(_emit_provider_rpc_error, "provider-secret-rpc-detail")
        return {name: False for name in safe_check._ORDER}

    monkeypatch.setattr(safe_check, "checks", noisy_checks)
    try:
        result = safe_check.main(
            {
                "LITELLM_MODEL": "openai/test-chat",
                "LITELLM_ATHENA_EMBEDDING_MODEL": "dashscope/text-embedding-v4",
            }
        )
    finally:
        logger.removeHandler(handler)
    output = capsys.readouterr()

    assert result == 1
    assert output.err == ""
    assert output.out.splitlines() == [
        "mysql failed start-mysql",
        "redis failed start-redis",
        "blob failed start-blob",
        "milvus failed start-milvus",
        "models failed configure-models",
    ]
    assert "provider-secret-rpc-detail" not in provider_output.getvalue()


def test_safe_blob_canary_delete_failure_is_failed_and_still_closes(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from tap.entrypoints.athena_runtime import AthenaSettings

    safe_check = _load_safe_check_module()
    settings = AthenaSettings.from_mapping(
        {
            "LITELLM_MODEL": "openai/test-chat",
            "LITELLM_ATHENA_EMBEDDING_MODEL": "dashscope/text-embedding-v4",
        }
    )
    events: list[str] = []

    class Download:
        async def readall(self) -> bytes:
            events.append("read")
            return b"canary"

    class BlobClient:
        async def upload_blob(self, payload: bytes, *, overwrite: bool) -> None:
            assert payload == b"canary"
            assert overwrite is False
            events.append("upload")

        async def download_blob(self) -> Download:
            events.append("download")
            return Download()

        async def delete_blob(self) -> None:
            events.append("delete")
            raise RuntimeError("provider-secret-detail")

    class Service:
        def get_blob_client(self, container: str, name: str) -> BlobClient:
            assert container == "athena-artifacts"
            assert name.startswith("readiness/canary-")
            return BlobClient()

    class Blob:
        _service = Service()

        async def _bounded(self, awaitable):  # type: ignore[no-untyped-def]
            return await awaitable

        async def container_properties(self, name: str) -> dict[str, object]:
            events.append(f"container:{name}")
            return {"public_access": None}

        async def aclose(self) -> None:
            events.append("close")

    monkeypatch.setattr(safe_check, "_create_blob", lambda _settings: Blob())
    monkeypatch.setattr(safe_check.secrets, "token_bytes", lambda _size: b"canary")
    monkeypatch.setattr(safe_check.secrets, "token_hex", lambda _size: "a" * 32)
    for name in ("mysql", "redis", "milvus", "models"):

        async def success(*_args):  # type: ignore[no-untyped-def]
            return True

        monkeypatch.setattr(safe_check, f"_check_{name}", success)

    states = asyncio.run(safe_check.checks(settings, {}))

    assert states["blob"] is False
    assert events == [
        "container:athena-originals",
        "container:athena-artifacts",
        "upload",
        "download",
        "read",
        "delete",
        "close",
    ]


def test_safe_models_probe_requires_provider_config_and_uses_get_models_only(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    import httpx

    from tap.entrypoints.athena_runtime import AthenaSettings

    safe_check = _load_safe_check_module()
    settings = AthenaSettings.from_mapping(
        {
            "LITELLM_MODEL": "openai/test-chat",
            "LITELLM_ATHENA_EMBEDDING_MODEL": "dashscope/text-embedding-v4",
        }
    )
    requests: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "athena-chat", "object": "model", "created": 0, "owned_by": "tap"},
                    {
                        "id": "athena-embedding",
                        "object": "model",
                        "created": 0,
                        "owned_by": "tap",
                    },
                ],
            },
        )

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:4000/",
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(safe_check, "_create_models_probe_client", lambda _settings: client)
    assert "_create_model" not in vars(safe_check)
    provider = {"DASHSCOPE_API_KEY": "configured"}

    assert asyncio.run(safe_check._check_models(settings, {})) is False
    assert requests == []
    assert asyncio.run(safe_check._check_models(settings, provider)) is True
    assert requests == [("GET", "/v1/models")]
    assert client.is_closed


@pytest.mark.parametrize(
    ("answer_backend", "provider"),
    [
        (
            "litellm",
            {"DASHSCOPE_API_KEY": " \t"},
        ),
        (
            "litellm",
            {"OPENAI_API_KEY": "configured"},
        ),
        (
            "codex",
            {"DASHSCOPE_API_KEY": " ", "OPENAI_API_KEY": "configured"},
        ),
    ],
)
def test_safe_models_provider_gate_fails_before_construction_or_network(
    monkeypatch,
    answer_backend: str,
    provider: dict[str, str],
) -> None:  # type: ignore[no-untyped-def]
    from tap.entrypoints.athena_runtime import AthenaSettings

    safe_check = _load_safe_check_module()
    settings = AthenaSettings.from_mapping(
        {
            "ATHENA_ANSWER_BACKEND": answer_backend,
            "LITELLM_MODEL": "openai/test-chat",
            "LITELLM_ATHENA_EMBEDDING_MODEL": "dashscope/text-embedding-v4",
            "LITELLM_EMBEDDING_MODEL": "direct-research-only",
        }
    )

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("provider construction occurred before credential gate")

    monkeypatch.setattr(safe_check, "_create_embeddings", forbidden)
    monkeypatch.setattr(safe_check, "_create_models_probe_client", forbidden)

    assert asyncio.run(safe_check._check_models(settings, provider)) is False


@pytest.mark.parametrize(
    ("labels", "expected", "readiness_calls"),
    [
        (("athena-embedding",), True, 1),
        (("athena-chat",), False, 0),
    ],
)
def test_safe_codex_models_probe_checks_embedding_first_and_closes_all_owners(
    monkeypatch,
    labels: tuple[str, ...],
    expected: bool,
    readiness_calls: int,
) -> None:  # type: ignore[no-untyped-def]
    from tap.entrypoints.athena_runtime import AthenaAnswerBackend, AthenaSettings

    safe_check = _load_safe_check_module()
    settings = AthenaSettings.from_mapping(
        {
            "ATHENA_ANSWER_BACKEND": "codex",
            "LITELLM_MODEL": "openai/test-chat",
            "LITELLM_EMBEDDING_MODEL": "dashscope/text-embedding-v4",
        }
    )
    events: list[str] = []

    class Response:
        status_code = 200
        content = json.dumps(
            {
                "object": "list",
                "data": [
                    {
                        "id": label,
                        "object": "model",
                        "created": 0,
                        "owned_by": "tap",
                    }
                    for label in labels
                ],
            }
        ).encode()

        async def aiter_bytes(self):  # type: ignore[no-untyped-def]
            events.append("models")
            yield self.content

    class ResponseContext:
        async def __aenter__(self) -> Response:
            return Response()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class ModelsClient:
        def stream(self, method: str, path: str) -> ResponseContext:
            assert (method, path) == ("GET", "v1/models")
            return ResponseContext()

        async def aclose(self) -> None:
            events.append("close:models")

    class Embeddings:
        async def aclose(self) -> None:
            events.append("close:embeddings")

    class Codex:
        async def check_ready(self) -> None:
            events.append("codex-ready")

        async def aclose(self) -> None:
            events.append("close:codex")

    embeddings = Embeddings()
    codex = Codex()
    monkeypatch.setattr(safe_check, "_create_embeddings", lambda _settings: embeddings)
    monkeypatch.setattr(
        safe_check,
        "_create_answer_backend",
        lambda _settings, *, embeddings: AthenaAnswerBackend(
            generator=codex,
            readiness=codex.check_ready,
            owner=codex,
        ),
    )
    monkeypatch.setattr(
        safe_check,
        "_create_models_probe_client",
        lambda _settings: ModelsClient(),
    )

    assert (
        asyncio.run(safe_check._check_models(settings, {"DASHSCOPE_API_KEY": "configured"}))
        is expected
    )
    assert events.count("codex-ready") == readiness_calls
    assert events[-3:] == ["close:models", "close:codex", "close:embeddings"]


def test_safe_codex_models_probe_closes_all_owners_when_readiness_fails(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from tap.entrypoints.athena_runtime import AthenaAnswerBackend, AthenaSettings

    safe_check = _load_safe_check_module()
    settings = AthenaSettings.from_mapping(
        {
            "ATHENA_ANSWER_BACKEND": "codex",
            "LITELLM_MODEL": "openai/test-chat",
            "LITELLM_EMBEDDING_MODEL": "dashscope/text-embedding-v4",
        }
    )
    events: list[str] = []

    class Response:
        status_code = 200
        content = json.dumps(
            {
                "object": "list",
                "data": [
                    {
                        "id": "athena-embedding",
                        "object": "model",
                        "created": 0,
                        "owned_by": "tap",
                    }
                ],
            }
        ).encode()

        async def aiter_bytes(self):  # type: ignore[no-untyped-def]
            yield self.content

    class ResponseContext:
        async def __aenter__(self) -> Response:
            return Response()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class Owner:
        def __init__(self, name: str) -> None:
            self.name = name

        async def aclose(self) -> None:
            events.append(f"close:{self.name}")

    class ModelsClient(Owner):
        def stream(self, _method: str, _path: str) -> ResponseContext:
            return ResponseContext()

    class Codex(Owner):
        async def check_ready(self) -> None:
            raise RuntimeError("login=/private/auth.json provider-secret")

    embeddings = Owner("embeddings")
    codex = Codex("codex")
    monkeypatch.setattr(safe_check, "_create_embeddings", lambda _settings: embeddings)
    monkeypatch.setattr(
        safe_check,
        "_create_answer_backend",
        lambda _settings, *, embeddings: AthenaAnswerBackend(
            generator=codex,
            readiness=codex.check_ready,
            owner=codex,
        ),
    )
    monkeypatch.setattr(
        safe_check,
        "_create_models_probe_client",
        lambda _settings: ModelsClient("models"),
    )

    with pytest.raises(RuntimeError, match="provider-secret"):
        asyncio.run(safe_check._check_models(settings, {"DASHSCOPE_API_KEY": "configured"}))

    assert events == ["close:models", "close:codex", "close:embeddings"]


def test_litellm_exposes_exactly_the_two_fixed_athena_aliases() -> None:
    """A legacy or provider-named route must not become part of the Demo model surface."""

    config = _load_yaml_as_json(ROOT / "deploy/local/litellm/config.yaml")

    assert [item["model_name"] for item in config["model_list"]] == [
        "athena-chat",
        "athena-embedding",
    ]
    assert config["model_list"][0]["litellm_params"] == {
        "model": "os.environ/LITELLM_MODEL",
        "api_key": "os.environ/DASHSCOPE_API_KEY",
        "api_base": "os.environ/DASHSCOPE_API_BASE",
    }
    assert config["model_list"][1]["litellm_params"] == {
        "model": "os.environ/LITELLM_ATHENA_EMBEDDING_MODEL",
        "api_key": "os.environ/DASHSCOPE_API_KEY",
        "api_base": "os.environ/DASHSCOPE_API_BASE",
    }


def test_compose_declares_loopback_ports_and_project_scoped_named_volumes() -> None:
    """Host middleware ports must remain local and every durable volume must be project-owned."""

    config = _load_yaml_as_json(ROOT / "compose.yaml")
    for service in config["services"].values():
        assert all(str(item).startswith("127.0.0.1:") for item in service.get("ports", []))
    assert set(config["volumes"]) == {
        "azurite-data",
        "milvus-data",
        "milvus-etcd-data",
        "milvus-minio-data",
        "mysql-data",
        "redis-data",
    }
    assert all(value is None for value in config["volumes"].values())

    services = config["services"]
    assert {name: services[name]["ports"] for name in ("mysql", "redis", "azurite", "litellm")} == {
        "mysql": ["127.0.0.1:${MYSQL_PORT:-3306}:3306"],
        "redis": ["127.0.0.1:${REDIS_PORT:-6379}:6379"],
        "azurite": ["127.0.0.1:${AZURITE_BLOB_PORT:-10000}:${AZURITE_BLOB_PORT:-10000}"],
        "litellm": ["127.0.0.1:${LITELLM_PORT:-4000}:4000"],
    }
    azurite_port = "${AZURITE_BLOB_PORT:-10000}"
    azurite = services["azurite"]
    assert azurite["command"].count("--silent") == 1
    assert "--debug" not in azurite["command"]
    assert not any("debug.log" in item for item in azurite["command"])
    assert azurite["command"][azurite["command"].index("--blobPort") + 1] == azurite_port
    health_program = azurite["healthcheck"]["test"][-1]
    assert f"http://127.0.0.1:{azurite_port}/devstoreaccount1" in health_program
    for rendered_port in (10000, 11000):
        replacement = str(rendered_port)
        assert azurite["ports"][0].replace(azurite_port, replacement) == (
            f"127.0.0.1:{rendered_port}:{rendered_port}"
        )
        assert (
            azurite["command"][azurite["command"].index("--blobPort") + 1].replace(
                azurite_port, replacement
            )
            == replacement
        )
        assert f"http://127.0.0.1:{rendered_port}/devstoreaccount1" in (
            health_program.replace(azurite_port, replacement)
        )
    assert services["milvus"]["ports"] == [
        "127.0.0.1:${MILVUS_PORT:-19530}:19530",
        "127.0.0.1:${MILVUS_HEALTH_PORT:-9091}:9091",
    ]
    for name in ("milvus", "milvus-etcd", "milvus-minio"):
        assert services[name]["profiles"] == ["milvus"]
    assert set(services["milvus"]["depends_on"]) == {"milvus-etcd", "milvus-minio"}
    for name in ("mysql", "redis", "azurite", "litellm", "milvus"):
        assert "healthcheck" in services[name]
    assert services["litellm"]["environment"]["DASHSCOPE_API_KEY"] == ("${DASHSCOPE_API_KEY:-}")
    assert "CODEX_API_KEY" not in services["litellm"]["environment"]


def test_vite_config_is_strict_and_exposes_only_same_origin_api_proxies() -> None:
    """A broad proxy or fallback port could leak the unauthenticated Demo boundary."""

    environment = dict(os.environ)
    for name in (
        "ATHENA_API_HOST",
        "ATHENA_API_PORT",
        "ATHENA_WEB_HOST",
        "ATHENA_WEB_PORT",
        "VITE_API_PORT",
    ):
        environment.pop(name, None)
    code = """
import config from './apps/web/vite.config.ts';
const value = typeof config === 'function' ? await config({command:'serve', mode:'test'}) : config;
console.log(JSON.stringify(value.server));
"""
    completed = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", code],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    server = json.loads(completed.stdout.splitlines()[-1])
    assert server["host"] == "127.0.0.1"
    assert server["port"] == 5173
    assert server["strictPort"] is True
    assert set(server["proxy"]) == {"/health", "/v1"}
    assert {value["target"] for value in server["proxy"].values()} == {"http://127.0.0.1:8000"}


def test_vite_uses_the_exact_offset_athena_ports_and_rejects_unsafe_values() -> None:
    code = """
import config from './apps/web/vite.config.ts';
const value = typeof config === 'function' ? await config({command:'serve', mode:'test'}) : config;
console.log(JSON.stringify(value.server));
"""
    offset = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", code],
        cwd=ROOT,
        env=os.environ
        | {
            "ATHENA_API_HOST": "127.0.0.1",
            "ATHENA_API_PORT": "18000",
            "ATHENA_WEB_HOST": "127.0.0.1",
            "ATHENA_WEB_PORT": "15173",
            "VITE_API_PORT": "3306",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert offset.returncode == 0, offset.stderr
    server = json.loads(offset.stdout.splitlines()[-1])
    assert server["host"] == "127.0.0.1"
    assert server["port"] == 15173
    assert {value["target"] for value in server["proxy"].values()} == {"http://127.0.0.1:18000"}

    base_environment = dict(os.environ)
    for name in (
        "ATHENA_API_HOST",
        "ATHENA_API_PORT",
        "ATHENA_WEB_HOST",
        "ATHENA_WEB_PORT",
    ):
        base_environment.pop(name, None)
    for environment in (
        {"ATHENA_API_HOST": "0.0.0.0"},
        {"ATHENA_WEB_HOST": "remote.example"},
        {"ATHENA_API_PORT": "8000junk"},
        {"ATHENA_WEB_PORT": "0"},
    ):
        rejected = subprocess.run(
            ["node", "--experimental-strip-types", "--input-type=module", "-e", code],
            cwd=ROOT,
            env=base_environment | environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert rejected.returncode != 0


def test_exact_playwright_and_fixture_dependency_pins() -> None:
    """A widened browser toolchain would make the deterministic E2E contract non-reproducible."""

    package = json.loads((ROOT / "apps/web/package.json").read_text())

    assert package["devDependencies"]["@playwright/test"] == "1.62.1"
    assert package["devDependencies"]["pdf-lib"] == "1.17.1"
    assert package["devDependencies"]["docx"] == "9.5.1"


def test_playwright_config_is_serial_failure_only_and_has_no_server_side_effect() -> None:
    code = """
import config from './apps/web/playwright.config.ts';
console.log(JSON.stringify(config));
"""
    completed = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", code],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    config = json.loads(completed.stdout.splitlines()[-1])
    assert config["fullyParallel"] is False
    assert config["forbidOnly"] is True
    assert config["retries"] == 0
    assert config["workers"] == 1
    assert config["preserveOutput"] == "failures-only"
    assert config["outputDir"] == "./test-results"
    assert "webServer" not in config
    assert config["use"]["baseURL"] == "http://127.0.0.1:15173"
    assert config["use"]["trace"] == "retain-on-failure"
    assert config["use"]["video"] == "retain-on-failure"
    assert config["use"]["screenshot"] == "only-on-failure"
    assert config["projects"] == [{"name": "chromium", "use": {"browserName": "chromium"}}]


def test_vitest_preserves_default_exclusions_and_never_collects_playwright_specs() -> None:
    """Unit tests and browser journeys must remain separate executable boundaries."""

    code = """
import config from './apps/web/vitest.config.ts';
const value = typeof config === 'function' ? await config({command:'serve', mode:'test'}) : config;
console.log(JSON.stringify(value.test?.exclude));
"""
    completed = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", code],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    exclusions = json.loads(completed.stdout.splitlines()[-1])
    assert "tests/e2e/**" in exclusions
    assert "**/node_modules/**" in exclusions


def test_env_example_covers_the_strict_runtime_without_enabling_destructive_or_fake_mode() -> None:
    values = {
        name: value
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
        for name, value in [line.split("=", 1)]
    }
    required = {
        "TAP_ATHENA_COMPOSE_PROJECT",
        "TAP_DATABASE_URL",
        "TAP_ALEMBIC_DATABASE_URL",
        "TAP_REDIS_URL",
        "TAP_REDIS_COMMAND_STREAM",
        "AZURE_STORAGE_CONNECTION_STRING",
        "LITELLM_BASE_URL",
        "LITELLM_MASTER_KEY",
        "LITELLM_MODEL",
        "LITELLM_ATHENA_EMBEDDING_MODEL",
        "LITELLM_EMBEDDING_MODEL",
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_API_HOST",
        "DASHSCOPE_API_BASE",
        "DASHSCOPE_NATIVE_API_BASE",
        "MILVUS_URI",
        "MILVUS_READER_PASSWORD",
        "MILVUS_WRITER_PASSWORD",
        "MILVUS_PROVISIONER_PASSWORD",
        "ATHENA_COLLECTION",
        "ATHENA_ALIAS",
        "ATHENA_CORPUS_VERSION",
        "ATHENA_RETRIEVAL_PROFILE",
        "ATHENA_EMBEDDING_DIMENSION",
        "ATHENA_PIPELINE_VERSION",
        "ATHENA_WORKER_ID",
        "ATHENA_API_PORT",
        "ATHENA_WEB_PORT",
        "ATHENA_ANSWER_BACKEND",
        "ATHENA_CODEX_MODEL",
        "ATHENA_CODEX_REASONING_EFFORT",
        "ATHENA_CODEX_TIMEOUT_SECONDS",
    }
    assert required <= values.keys()
    assert values["ATHENA_MODEL_BACKEND"] == "litellm"
    assert values["ATHENA_ANSWER_BACKEND"] == "litellm"
    assert values["ATHENA_CODEX_MODEL"] == "gpt-5.6-sol"
    assert values["ATHENA_CODEX_REASONING_EFFORT"] == "ultra"
    assert values["ATHENA_CODEX_TIMEOUT_SECONDS"] == "300"
    assert values["LITELLM_ATHENA_EMBEDDING_MODEL"] == "dashscope/text-embedding-v4"
    assert values["DASHSCOPE_API_KEY"] == ""
    assert values["TAP_DEMO_MODE"] == ""
    assert values["TAP_ALLOW_INITIAL_MILVUS_ROOT"] == "0"
    assert "OPENAI_API_KEY" not in values
    assert values["LITELLM_IMAGE"] == "ghcr.io/berriai/litellm:v1.87.0"
    assert "BAILIAN_API_KEY" not in values
    assert "BAILIAN_API_BASE" not in values
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "query and selected Evidence are sent to OpenAI" in example
    assert "Embedding content is sent to Alibaba Bailian" in example
    assert "Codex uses local ChatGPT login; it does not require OPENAI_API_KEY" in example
    assert values["DASHSCOPE_API_HOST"] == ("ws-your-workspace-id.cn-beijing.maas.aliyuncs.com")
    assert values["DASHSCOPE_API_BASE"] == (
        "https://ws-your-workspace-id.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    )
    assert values["DASHSCOPE_NATIVE_API_BASE"] == (
        "https://ws-your-workspace-id.cn-beijing.maas.aliyuncs.com/api/v1"
    )
    assert values["LITELLM_MODEL"] == "dashscope/qwen-plus"
    assert values["LITELLM_ATHENA_EMBEDDING_MODEL"] == "dashscope/text-embedding-v4"
    assert values["LITELLM_EMBEDDING_MODEL"] == "text-embedding-v4"
    assert {
        "MYSQL_PORT": values["MYSQL_PORT"],
        "REDIS_PORT": values["REDIS_PORT"],
        "AZURITE_BLOB_PORT": values["AZURITE_BLOB_PORT"],
        "LITELLM_PORT": values["LITELLM_PORT"],
        "MILVUS_PORT": values["MILVUS_PORT"],
        "MILVUS_HEALTH_PORT": values["MILVUS_HEALTH_PORT"],
    } == {
        "MYSQL_PORT": "23306",
        "REDIS_PORT": "26379",
        "AZURITE_BLOB_PORT": "21000",
        "LITELLM_PORT": "24000",
        "MILVUS_PORT": "39530",
        "MILVUS_HEALTH_PORT": "29091",
    }
    assert values["TAP_DATABASE_URL"].endswith("@127.0.0.1:23306/tap?charset=utf8mb4")
    assert values["TAP_ALEMBIC_DATABASE_URL"].endswith("@127.0.0.1:23306/tap?charset=utf8mb4")
    assert values["TAP_REDIS_URL"] == "redis://127.0.0.1:26379/0"
    assert (
        "BlobEndpoint=http://127.0.0.1:21000/devstoreaccount1;"
        in values["AZURE_STORAGE_CONNECTION_STRING"]
    )
    assert values["LITELLM_BASE_URL"] == "http://127.0.0.1:24000"
    assert values["MILVUS_URI"] == "http://127.0.0.1:39530"

    sourced = subprocess.run(
        [
            "/bin/bash",
            "-c",
            (
                "set -a; . ./.env.example; set +a; "
                "uv run --project apps/backend python -c 'import os; "
                "from tap.entrypoints.athena_runtime import AthenaSettings; "
                "settings=AthenaSettings.from_mapping(dict(os.environ)); "
                'assert settings.blob_connection_string.count(";") == 4\''
            ),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert sourced.returncode == 0, sourced.stderr


def test_runtime_scripts_exist_are_executable_and_parse_as_bash() -> None:
    """A generated but non-executable or Bash-incompatible supervisor is not a stable command."""

    scripts = (
        ROOT / "scripts/run-athena-dev.sh",
        ROOT / "scripts/run-athena-e2e.sh",
    )
    for script in scripts:
        assert script.is_file()
        assert script.stat().st_mode & stat.S_IXUSR
    completed = subprocess.run(
        ["bash", "-n", *(str(script) for script in scripts)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_dev_supervisor_preserves_first_child_failure_and_stops_exact_siblings(
    tmp_path: Path,
) -> None:
    supervisor, environment, log = _supervisor_fixture(
        tmp_path,
        api_exit="17",
        ready_status="unready",
    )
    environment.update(
        {
            "ATHENA_ASSERT_PROVIDER_SCOPE": "1",
            "DASHSCOPE_API_KEY": "current-provider-secret",
            "OPENAI_API_KEY": "legacy-openai-secret",
            "BAILIAN_API_KEY": "legacy-bailian-secret",
            "LITELLM_EMBEDDING_API_KEY": "direct-research-secret",
            "LITELLM_EMBEDDING_API_BASE": "https://provider-secret.invalid/v1",
        }
    )

    completed = subprocess.run(
        ["/bin/bash", str(supervisor)],
        cwd=supervisor.parents[1],
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 17, completed.stderr
    events = log.read_text(encoding="utf-8").splitlines()
    assert {line.split()[1] for line in events if line.startswith("start ")} == {
        "api",
        "relay",
        "worker",
        "web",
    }
    assert {line.split()[1] for line in events if line.startswith("term ")} == {
        "relay",
        "worker",
        "web",
    }
    _assert_processes_are_gone(_started_child_pids(log))
    assert "provider-secret" not in completed.stdout + completed.stderr + "\n".join(events)
    assert list((supervisor.parents[1] / "tmp").glob("tap-athena-ready.*")) == []


def test_dev_supervisor_ignores_dotenv_collisions_with_internal_shell_state(
    tmp_path: Path,
) -> None:
    supervisor, environment, log = _supervisor_fixture(
        tmp_path,
        api_exit="17",
        ready_status="unready",
    )
    (supervisor.parents[1] / ".env").write_text(
        """repo_root=/provider-secret/invalid
requested_project=tap-hostile
vite_bin=/provider-secret/invalid-vite
api_pid=99999999
relay_pid=99999998
worker_pid=99999997
web_pid=99999996
ready_file=/provider-secret/invalid-ready
cleanup_started=1
shutdown_grace_seconds=0
TAP_ATHENA_COMPOSE_PROJECT=tap-hostile
""",
        encoding="utf-8",
    )
    environment.pop("ATHENA_SUPERVISOR_ENV")
    environment["TAP_ATHENA_COMPOSE_PROJECT"] = "tap-athena-demo"

    completed = subprocess.run(
        ["/bin/bash", str(supervisor)],
        cwd=supervisor.parents[1],
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 17, completed.stderr
    assert len(_started_child_pids(log)) == 4
    _assert_processes_are_gone(_started_child_pids(log))
    assert "provider-secret" not in completed.stdout + completed.stderr


def test_dev_supervisor_sigterm_returns_143_and_allows_bounded_child_settlement(
    tmp_path: Path,
) -> None:
    supervisor, environment, log = _supervisor_fixture(tmp_path, term_delay="0.4")
    process = subprocess.Popen(
        ["/bin/bash", str(supervisor)],
        cwd=supervisor.parents[1],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if log.exists():
            current_events = log.read_text(encoding="utf-8").splitlines()
            if len([line for line in current_events if line.startswith("start ")]) == 4 and any(
                line.startswith("curl-argv ") for line in current_events
            ):
                break
        time.sleep(0.05)
    else:
        process.kill()
        raise AssertionError("supervisor did not start all four children")

    process.terminate()
    time.sleep(0.1)
    process.terminate()
    stdout, stderr = process.communicate(timeout=8)

    assert process.returncode == 143, stderr
    assert "provider-secret" not in stdout + stderr
    events = log.read_text(encoding="utf-8").splitlines()
    curl_argv = [line for line in events if line.startswith("curl-argv ")]
    assert len(curl_argv) >= 1
    assert all(" --max-filesize 65536 " in f" {line} " for line in curl_argv)
    assert (
        f"vite-root {supervisor.parents[1] / 'apps/web'} "
        f"config {supervisor.parents[1] / 'apps/web/vite.config.ts'}"
    ) in events
    assert {line.split()[1] for line in events if line.startswith("term ")} == {
        "api",
        "relay",
        "worker",
        "web",
    }
    _assert_processes_are_gone(_started_child_pids(log))
    assert "provider-secret" not in stdout + stderr + "\n".join(events)
    assert list((supervisor.parents[1] / "tmp").glob("tap-athena-ready.*")) == []


@pytest.mark.parametrize("source", ["caller", "dotenv"])
def test_dev_supervisor_scrubs_provider_environment_at_every_child_boundary(
    tmp_path: Path,
    source: str,
) -> None:
    """Passing a provider credential through any non-API boundary reintroduces ambient auth."""

    supervisor, environment, log = _supervisor_fixture(tmp_path)
    environment.update(
        {
            "CODEX_HOME": "/caller/codex-home",
            "OPENAI_API_KEY": "caller-openai-key",
            "DASHSCOPE_API_KEY": "caller-dashscope-key",
            "CODEX_API_KEY": "caller-codex-key",
            "LITELLM_EMBEDDING_API_KEY": "caller-embedding-key",
            "LITELLM_EMBEDDING_API_BASE": "https://caller.invalid/embedding",
            "OPENAI_BASE_URL": "https://caller.invalid/openai",
            "OPENAI_API_BASE": "https://caller.invalid/openai-api",
            "DASHSCOPE_BASE_URL": "https://caller.invalid/dashscope",
            "DASHSCOPE_API_BASE": "https://caller.invalid/dashscope-api",
            "CODEX_API_BASE": "https://caller.invalid/codex-api",
        }
    )
    if source == "dotenv":
        environment["ATHENA_SUPERVISOR_ENV"] = ""
        (supervisor.parents[1] / ".env").write_text(
            """OPENAI_API_KEY=provider-secret
DASHSCOPE_API_KEY=provider-secret
CODEX_HOME=/provider-secret/codex-home
CODEX_API_KEY=provider-secret
LITELLM_EMBEDDING_API_KEY=provider-secret
LITELLM_EMBEDDING_API_BASE=https://provider-secret.invalid/embedding
OPENAI_BASE_URL=https://provider-secret.invalid/openai
OPENAI_API_BASE=https://provider-secret.invalid/openai-api
DASHSCOPE_BASE_URL=https://provider-secret.invalid/dashscope
DASHSCOPE_API_BASE=https://provider-secret.invalid/dashscope-api
CODEX_API_BASE=https://provider-secret.invalid/codex-api
""",
            encoding="utf-8",
        )
    process = subprocess.Popen(
        ["/bin/bash", str(supervisor)],
        cwd=supervisor.parents[1],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    expected_roles = {"api", "relay", "worker", "web", "validation", "readiness", "curl"}
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if log.exists():
            recorded_roles = {
                line.split()[1]
                for line in log.read_text(encoding="utf-8").splitlines()
                if line.startswith("env ")
            }
            if expected_roles <= recorded_roles:
                break
        time.sleep(0.05)
    else:
        process.kill()
        raise AssertionError("supervisor did not reach every environment boundary")

    process.terminate()
    stdout, stderr = process.communicate(timeout=8)

    assert process.returncode == 143, stderr
    environment_names = {
        line.split(maxsplit=2)[1]: set(filter(None, line.split(maxsplit=2)[2].split(",")))
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.startswith("env ")
    }
    forbidden_provider_names = {
        "OPENAI_API_KEY",
        "DASHSCOPE_API_KEY",
        "CODEX_API_KEY",
        "LITELLM_EMBEDDING_API_KEY",
        "LITELLM_EMBEDDING_API_BASE",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "DASHSCOPE_BASE_URL",
        "DASHSCOPE_API_BASE",
        "CODEX_API_BASE",
    }
    assert "CODEX_HOME" in environment_names["api"]
    for role, names in environment_names.items():
        assert not forbidden_provider_names & names
        if role != "api":
            assert "CODEX_HOME" not in names
    assert {
        "TAP_DATABASE_URL",
        "TAP_REDIS_URL",
        "AZURE_STORAGE_CONNECTION_STRING",
        "LITELLM_MASTER_KEY",
        "MILVUS_READER_PASSWORD",
    } <= environment_names["api"]
    assert {"TAP_DATABASE_URL", "TAP_REDIS_URL"} <= environment_names["relay"]
    assert {
        "TAP_DATABASE_URL",
        "TAP_REDIS_URL",
        "AZURE_STORAGE_CONNECTION_STRING",
        "LITELLM_MASTER_KEY",
        "MILVUS_WRITER_PASSWORD",
    } <= environment_names["worker"]
    output = stdout + stderr + log.read_text(encoding="utf-8")
    assert "caller-" not in output
    assert "provider-secret" not in output
    _assert_processes_are_gone(_started_child_pids(log))


def test_dev_supervisor_does_not_accept_http_200_with_unready_body(
    tmp_path: Path,
) -> None:
    supervisor, environment, log = _supervisor_fixture(
        tmp_path,
        ready_status="unready",
    )
    code = supervisor.read_text(encoding="utf-8")
    supervisor.write_text(
        code.replace(
            "ready_deadline=$(( SECONDS + 90 ))",
            "ready_deadline=$(( SECONDS + 2 ))",
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["/bin/bash", str(supervisor)],
        cwd=supervisor.parents[1],
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 1
    assert "Athena local applications ready." not in completed.stdout
    events = log.read_text(encoding="utf-8").splitlines()
    assert {line.split()[1] for line in events if line.startswith("term ")} == {
        "api",
        "relay",
        "worker",
        "web",
    }
    _assert_processes_are_gone(_started_child_pids(log))
    assert "provider-secret" not in completed.stdout + completed.stderr + "\n".join(events)
    assert list((supervisor.parents[1] / "tmp").glob("tap-athena-ready.*")) == []


def test_e2e_runner_rejects_an_occupied_fixed_port_before_docker(tmp_path: Path) -> None:
    """A collision must stop before Compose instead of falling back to a shared service."""

    isolated = tmp_path / "repo"
    scripts = isolated / "scripts"
    stubs = isolated / "bin"
    scripts.mkdir(parents=True)
    stubs.mkdir()
    socket_log = _write_behavioral_socket_stub(isolated)
    runner = scripts / "run-athena-e2e.sh"
    runner.write_text(
        (ROOT / "scripts/run-athena-e2e.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    runner.chmod(runner.stat().st_mode | stat.S_IXUSR)
    chromium = isolated / "chromium"
    chromium.write_text("", encoding="utf-8")
    chromium.chmod(chromium.stat().st_mode | stat.S_IXUSR)
    corepack = stubs / "corepack"
    corepack.write_text(f"#!/bin/sh\nprintf '%s' '{chromium}'\n", encoding="utf-8")
    corepack.chmod(corepack.stat().st_mode | stat.S_IXUSR)
    uv = stubs / "uv"
    uv.write_text(
        """#!/bin/sh
case " $* " in
  *" python -c "*) exit 0 ;;
  *" python - "*) shift 4; exec "$ATHENA_TEST_PYTHON" "$@" ;;
esac
exit 99
""",
        encoding="utf-8",
    )
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    marker = isolated / "docker-called"
    docker = stubs / "docker"
    docker.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 99\n", encoding="utf-8")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    completed = subprocess.run(
        ["/bin/bash", str(runner)],
        cwd=isolated,
        env=os.environ
        | {
            "ATHENA_E2E_SOCKET_LOG": str(socket_log),
            "ATHENA_E2E_SOCKET_OCCUPIED": "13306",
            "ATHENA_TEST_PYTHON": sys.executable,
            "PATH": f"{stubs}:{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "13306" in completed.stderr
    assert not marker.exists()
    assert socket_log.read_text(encoding="utf-8").splitlines() == ["127.0.0.1:13306"]


def test_e2e_runner_overrides_env_and_runs_exact_restart_volume_phases(
    tmp_path: Path,
) -> None:
    runner, environment, log = _e2e_runner_fixture(tmp_path)
    assert environment["ATHENA_E2E_SOCKET_OCCUPIED"] == ""

    completed = subprocess.run(
        ["/bin/bash", str(runner)],
        cwd=runner.parents[1],
        env=environment,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    socket_log = Path(environment["ATHENA_E2E_SOCKET_LOG"])
    assert socket_log.read_text(encoding="utf-8").splitlines() == [
        f"127.0.0.1:{port}" for port in E2E_FIXED_PORTS
    ]
    calls = log.read_text(encoding="utf-8").splitlines()
    behavior_calls = [line for line in calls if not line.startswith("env|")]
    assert behavior_calls[:4] == ["uv|settings", "uv|probe", "chromium", "context|show"]
    assert behavior_calls[4] == "context|inspect"
    compose_calls = [line for line in calls if line.startswith("docker|")]
    assert len(compose_calls) == 5
    assert all(
        f"--context default compose -f {runner.parents[1] / 'compose.yaml'} "
        "-p tap-athena-e2e --profile milvus" in line
        for line in compose_calls
    )
    assert compose_calls[0].endswith("down --volumes --remove-orphans")
    assert compose_calls[1].endswith("up -d --wait --wait-timeout 180")
    assert compose_calls[2].endswith("down --remove-orphans")
    assert compose_calls[3].endswith("up -d --wait --wait-timeout 180")
    assert compose_calls[4].endswith("down --volumes --remove-orphans")
    assert [line.split("|")[1] for line in calls if line.startswith("playwright|")] == [
        "journey",
        "app-restart",
        "compose-restart",
    ]
    assert calls.count("uv|alembic") == 2
    assert calls.count("uv|bootstrap|initial=1") == 2
    assert calls.count("uv|ensure") == 2
    assert "uv|verify|verify" in calls
    assert len([line for line in calls if line.startswith("apps|start|")]) == 3
    assert len([line for line in calls if line.startswith("apps|term|")]) == 3
    _assert_processes_are_gone(
        [int(line.split("|")[2]) for line in calls if line.startswith("apps|start|")]
    )
    assert "provider-secret" not in completed.stdout + completed.stderr + "\n".join(calls)
    assert not (runner.parents[1] / "tmp/tap-athena-e2e.lock").exists()
    assert list((runner.parents[1] / "tmp").glob("tap-athena-e2e.*")) == []


@pytest.mark.parametrize("source", ["caller", "dotenv"])
def test_e2e_runner_rejects_codex_after_loading_the_final_environment(
    tmp_path: Path,
    source: str,
) -> None:
    runner, environment, log = _e2e_runner_fixture(tmp_path)
    if source == "caller":
        environment["ATHENA_ANSWER_BACKEND"] = "codex"
    else:
        with (runner.parents[1] / ".env").open("a", encoding="utf-8") as handle:
            handle.write("ATHENA_ANSWER_BACKEND=codex\n")

    completed = subprocess.run(
        ["/bin/bash", str(runner), "--preflight-only"],
        cwd=runner.parents[1],
        env=environment,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "Athena E2E does not allow the Codex answer backend.\n"
    assert not log.exists() or log.read_text(encoding="utf-8") == ""


def test_e2e_preflight_forces_fake_configuration_without_caller_provider_endpoints(
    tmp_path: Path,
) -> None:
    """A preflight must validate only the fixed E2E configuration, never ambient providers."""

    runner, environment, log = _e2e_runner_fixture(tmp_path)
    environment.update(
        {
            "OPENAI_API_KEY": "caller-openai-key",
            "BAILIAN_API_KEY": "caller-bailian-key",
            "BAILIAN_API_BASE": "https://caller.invalid/bailian",
            "DASHSCOPE_API_KEY": "caller-dashscope-key",
            "CODEX_HOME": "/caller/codex-home",
            "CODEX_API_KEY": "caller-codex-key",
            "LITELLM_ATHENA_EMBEDDING_MODEL": "caller-secret-route",
            "LITELLM_EMBEDDING_MODEL": "caller-secret-model",
            "LITELLM_EMBEDDING_API_KEY": "caller-embedding-key",
            "LITELLM_EMBEDDING_API_BASE": "https://caller.invalid/embedding",
            "OPENAI_BASE_URL": "https://caller.invalid/openai",
            "OPENAI_API_BASE": "https://caller.invalid/openai-api",
            "DASHSCOPE_BASE_URL": "https://caller.invalid/dashscope",
            "DASHSCOPE_API_BASE": "https://caller.invalid/dashscope-api",
            "CODEX_API_BASE": "https://caller.invalid/codex-api",
        }
    )

    completed = subprocess.run(
        ["/bin/bash", str(runner), "--preflight-only"],
        cwd=runner.parents[1],
        env=environment,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    recorded = [
        line
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.startswith("env|settings|")
    ]
    assert len(recorded) == 1
    environment_names = set(filter(None, recorded[0].split("|", maxsplit=2)[2].split(",")))
    assert {
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_API_BASE",
        "LITELLM_ATHENA_EMBEDDING_MODEL",
        "LITELLM_EMBEDDING_MODEL",
    } <= environment_names
    assert (
        not {
            "OPENAI_API_KEY",
            "BAILIAN_API_KEY",
            "BAILIAN_API_BASE",
            "CODEX_HOME",
            "CODEX_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_API_BASE",
            "DASHSCOPE_BASE_URL",
            "CODEX_API_BASE",
            "LITELLM_EMBEDDING_API_KEY",
            "LITELLM_EMBEDDING_API_BASE",
        }
        & environment_names
    )
    assert not any(
        line.startswith("docker|") for line in log.read_text(encoding="utf-8").splitlines()
    )
    assert "caller-" not in completed.stdout + completed.stderr + log.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("failure", "expected_status"),
    (
        ("supervisor", 17),
        ("api-http", 1),
        ("api-body", 1),
        ("web-http", 1),
    ),
)
def test_e2e_start_apps_reports_only_its_closed_failed_readiness_stage(
    tmp_path: Path,
    failure: str,
    expected_status: int,
) -> None:
    runner, environment, log = _e2e_runner_fixture(
        tmp_path,
        readiness_failure=failure,
    )

    completed = subprocess.run(
        ["/bin/bash", str(runner)],
        cwd=runner.parents[1],
        env=environment,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == expected_status
    assert completed.stdout == ""
    assert (
        completed.stderr.count(f"Athena E2E applications did not become ready at {failure}.\n") == 1
    )
    assert "provider-secret" not in completed.stderr
    compose_calls = [
        line for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("docker|")
    ]
    assert compose_calls[-1].endswith("down --volumes --remove-orphans")


def test_e2e_runner_preserves_playwright_failure_and_still_cleans_only_owned_volumes(
    tmp_path: Path,
) -> None:
    runner, environment, log = _e2e_runner_fixture(
        tmp_path,
        fail_phase="app-restart",
    )

    completed = subprocess.run(
        ["/bin/bash", str(runner)],
        cwd=runner.parents[1],
        env=environment,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 37, completed.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    compose_calls = [line for line in calls if line.startswith("docker|")]
    assert len(compose_calls) == 3
    assert compose_calls[0].endswith("down --volumes --remove-orphans")
    assert compose_calls[1].endswith("up -d --wait --wait-timeout 180")
    assert compose_calls[2].endswith("down --volumes --remove-orphans")
    assert all(" -p tap-athena-e2e " in line for line in compose_calls)
    assert "provider-secret" not in completed.stdout + completed.stderr + "\n".join(calls)


def test_e2e_cleanup_warning_never_masks_the_primary_failure(tmp_path: Path) -> None:
    runner, environment, log = _e2e_runner_fixture(
        tmp_path,
        cleanup_failure=True,
        fail_phase="journey",
    )

    completed = subprocess.run(
        ["/bin/bash", str(runner)],
        cwd=runner.parents[1],
        env=environment,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 37
    assert "Athena E2E cleanup failed." in completed.stderr
    compose_calls = [
        line for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("docker|")
    ]
    assert compose_calls[-1].endswith("down --volumes --remove-orphans")


def test_e2e_bootstrap_failure_never_signals_dotenv_seeded_unrelated_pid(
    tmp_path: Path,
) -> None:
    runner, environment, log = _e2e_runner_fixture(
        tmp_path,
        ensure_failure=True,
    )
    sentinel = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    try:
        with (runner.parents[1] / ".env").open("a", encoding="utf-8") as handle:
            handle.write(f"apps_pid={sentinel.pid}\ncleanup_started=0\n")
        completed = subprocess.run(
            ["/bin/bash", str(runner)],
            cwd=runner.parents[1],
            env=environment,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        assert sentinel.poll() is None
    finally:
        if sentinel.poll() is None:
            sentinel.terminate()
        sentinel.wait(timeout=5)

    assert completed.returncode == 41, completed.stderr
    compose_calls = [
        line for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("docker|")
    ]
    assert compose_calls[0].endswith("down --volumes --remove-orphans")
    assert compose_calls[-1].endswith("down --volumes --remove-orphans")
    assert all(" -p tap-athena-e2e " in line for line in compose_calls)


def test_e2e_preflight_is_read_only_and_skipped_results_fail_closed(
    tmp_path: Path,
) -> None:
    preflight_runner, preflight_environment, preflight_log = _e2e_runner_fixture(
        tmp_path / "preflight"
    )
    sentinel = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    try:
        with (preflight_runner.parents[1] / ".env").open("a", encoding="utf-8") as handle:
            handle.write(
                f"apps_pid={sentinel.pid}\n"
                "preflight_only=0\n"
                "e2e_project=tap-athena-demo\n"
                "cleanup_started=0\n"
                "compose_mutated=1\n"
            )
        preflight = subprocess.run(
            ["/bin/bash", str(preflight_runner), "--preflight-only"],
            cwd=preflight_runner.parents[1],
            env=preflight_environment,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert sentinel.poll() is None
    finally:
        if sentinel.poll() is None:
            sentinel.terminate()
        sentinel.wait(timeout=5)

    assert preflight.returncode == 0, preflight.stderr
    assert not any(
        line.startswith("docker|")
        for line in preflight_log.read_text(encoding="utf-8").splitlines()
    )
    assert not (preflight_runner.parents[1] / "tmp/tap-athena-e2e.lock").exists()

    runner, environment, log = _e2e_runner_fixture(
        tmp_path / "skipped",
        skipped_phase="journey",
    )
    rejected = subprocess.run(
        ["/bin/bash", str(runner)],
        cwd=runner.parents[1],
        env=environment,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert rejected.returncode == 1
    calls = log.read_text(encoding="utf-8").splitlines()
    compose_calls = [line for line in calls if line.startswith("docker|")]
    assert compose_calls[-1].endswith("down --volumes --remove-orphans")
    assert "returned an invalid result" in rejected.stderr


def test_e2e_runner_rejects_malformed_playwright_report_and_cleans_owned_state(
    tmp_path: Path,
) -> None:
    runner, environment, log = _e2e_runner_fixture(
        tmp_path,
        malformed_phase="journey",
    )

    rejected = subprocess.run(
        ["/bin/bash", str(runner)],
        cwd=runner.parents[1],
        env=environment,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert rejected.returncode == 1
    calls = log.read_text(encoding="utf-8").splitlines()
    compose_calls = [line for line in calls if line.startswith("docker|")]
    assert compose_calls[-1].endswith("down --volumes --remove-orphans")
    assert "returned an invalid result" in rejected.stderr
    assert list((runner.parents[1] / "tmp").glob("tap-athena-e2e.*")) == []
