#!/bin/bash
set -euo pipefail
umask 077

unset CODEX_HOME CODEX_API_KEY CODEX_BASE_URL CODEX_API_BASE
unset OPENAI_API_KEY OPENAI_BASE_URL OPENAI_API_BASE
unset DASHSCOPE_API_KEY DASHSCOPE_BASE_URL DASHSCOPE_API_BASE
unset LITELLM_EMBEDDING_API_KEY LITELLM_EMBEDDING_API_BASE

athena_e2e_preflight_only=0
case "$#" in
  0) ;;
  1)
    if [ "$1" != "--preflight-only" ]; then
      echo "Athena E2E accepts only --preflight-only." >&2
      exit 2
    fi
    athena_e2e_preflight_only=1
    ;;
  *)
    echo "Athena E2E accepts only --preflight-only." >&2
    exit 2
    ;;
esac

readonly athena_e2e_preflight_only
athena_e2e_script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
athena_e2e_repo_root="$(CDPATH= cd -- "$athena_e2e_script_dir/.." && pwd)"
athena_e2e_compose_file="$athena_e2e_repo_root/compose.yaml"
athena_e2e_project="tap-athena-e2e"
readonly athena_e2e_script_dir athena_e2e_repo_root athena_e2e_compose_file
readonly athena_e2e_project

if [ -f "$athena_e2e_repo_root/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$athena_e2e_repo_root/.env"
  set +a
fi

if [ "${ATHENA_ANSWER_BACKEND:-litellm}" = codex ]; then
  echo "Athena E2E does not allow the Codex answer backend." >&2
  exit 2
fi

# The deterministic E2E profile must not pass caller or dotenv provider secrets
# to Docker, migration, application, or browser subprocesses.
unset CODEX_HOME CODEX_API_KEY CODEX_BASE_URL CODEX_API_BASE
unset OPENAI_API_KEY OPENAI_BASE_URL OPENAI_API_BASE
unset BAILIAN_API_KEY BAILIAN_API_BASE
unset DASHSCOPE_API_KEY DASHSCOPE_BASE_URL DASHSCOPE_API_BASE
unset LITELLM_EMBEDDING_API_KEY LITELLM_EMBEDDING_API_BASE
unset TAP_RUN_PAID_EMBEDDING_RESEARCH

athena_e2e_state_root="${TMPDIR:-/tmp}"
athena_e2e_state_dir=""
athena_e2e_lock_dir="$athena_e2e_state_root/tap-athena-e2e.lock"
athena_e2e_apps_pid=""
athena_e2e_cleanup_started=0
athena_e2e_compose_mutated=0
athena_e2e_lock_owned=0

export TAP_ATHENA_COMPOSE_PROJECT="$athena_e2e_project"
export MYSQL_PORT=13306
export REDIS_PORT=16379
export AZURITE_BLOB_PORT=11000
export LITELLM_PORT=14000
export MILVUS_PORT=29530
export MILVUS_HEALTH_PORT=19091
export ATHENA_API_HOST=127.0.0.1
export ATHENA_API_PORT=18000
export ATHENA_WEB_HOST=127.0.0.1
export ATHENA_WEB_PORT=15173
export TAP_DEMO_MODE=e2e
export ATHENA_MODEL_BACKEND=fake
export ATHENA_ANSWER_BACKEND=litellm

export MYSQL_ROOT_PASSWORD=tap-e2e-root
export MYSQL_DATABASE=tap
export MYSQL_USER=tap
export MYSQL_PASSWORD=tap-e2e
export TAP_DATABASE_URL='mysql+asyncmy://tap:tap-e2e@127.0.0.1:13306/tap?charset=utf8mb4'
export TAP_ALEMBIC_DATABASE_URL='mysql+pymysql://tap:tap-e2e@127.0.0.1:13306/tap?charset=utf8mb4'
export TAP_REDIS_URL='redis://127.0.0.1:16379/0'
export TAP_REDIS_COMMAND_STREAM='tap:commands'
export AZURE_STORAGE_CONNECTION_STRING='DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:11000/devstoreaccount1;'
export LITELLM_BASE_URL='http://127.0.0.1:14000'
export LITELLM_MASTER_KEY='tap-e2e-master-key'
export LITELLM_MODEL='dashscope/e2e-chat-unused'
export LITELLM_ATHENA_EMBEDDING_MODEL='dashscope/text-embedding-v4'
export LITELLM_EMBEDDING_MODEL='text-embedding-v4'
export DASHSCOPE_API_KEY='tap-e2e-unused'
export DASHSCOPE_API_BASE='http://127.0.0.1:14000'

export MILVUS_URI='http://127.0.0.1:29530'
export MILVUS_DATABASE=default
export MILVUS_MINIO_ROOT_USER=tap-e2e-minio
export MILVUS_MINIO_ROOT_PASSWORD=tap-e2e-minio-password
export MILVUS_INITIAL_ROOT_PASSWORD=Milvus
export MILVUS_ROOT_PASSWORD='tap-e2e-Root1!'
export MILVUS_READER_USERNAME=tap_reader
export MILVUS_READER_PASSWORD='tap-e2e-Reader1!'
export MILVUS_WRITER_USERNAME=tap_writer
export MILVUS_WRITER_PASSWORD='tap-e2e-Writer1!'
export MILVUS_PROVISIONER_USERNAME=tap_provisioner
export MILVUS_PROVISIONER_PASSWORD='tap-e2e-Provisioner1!'

export ATHENA_COLLECTION=kb_doc_v1_athena_demo
export ATHENA_ALIAS=kb_doc_athena_demo_active
export ATHENA_CORPUS_VERSION=athena-demo-v1
export ATHENA_CHAT_ALIAS=athena-chat
export ATHENA_EMBEDDING_ALIAS=athena-embedding
export ATHENA_RETRIEVAL_PROFILE=quick-hybrid-v1
export ATHENA_EMBEDDING_DIMENSION=1536
export ATHENA_INDEX_VERSION=athena-index-v1
export ATHENA_PIPELINE_VERSION=athena-ingestion-v1
export ATHENA_WORKER_ID=athena-e2e-worker
export ATHENA_POLL_SECONDS=0.1
export ATHENA_JOB_BATCH_SIZE=10
export ATHENA_READY_TIMEOUT_SECONDS=5
export ATHENA_MODEL_TIMEOUT_SECONDS=15
export ATHENA_BLOB_TIMEOUT_SECONDS=15
export ATHENA_MILVUS_TIMEOUT_SECONDS=30

readonly TAP_ATHENA_COMPOSE_PROJECT MYSQL_PORT REDIS_PORT AZURITE_BLOB_PORT
readonly LITELLM_PORT MILVUS_PORT MILVUS_HEALTH_PORT ATHENA_API_HOST ATHENA_API_PORT
readonly ATHENA_WEB_HOST ATHENA_WEB_PORT TAP_DEMO_MODE ATHENA_MODEL_BACKEND
readonly ATHENA_ANSWER_BACKEND
readonly MYSQL_ROOT_PASSWORD MYSQL_DATABASE MYSQL_USER MYSQL_PASSWORD
readonly TAP_DATABASE_URL TAP_ALEMBIC_DATABASE_URL TAP_REDIS_URL TAP_REDIS_COMMAND_STREAM
readonly AZURE_STORAGE_CONNECTION_STRING LITELLM_BASE_URL LITELLM_MASTER_KEY
readonly LITELLM_MODEL LITELLM_ATHENA_EMBEDDING_MODEL LITELLM_EMBEDDING_MODEL
readonly DASHSCOPE_API_KEY DASHSCOPE_API_BASE
readonly MILVUS_URI
readonly MILVUS_DATABASE MILVUS_MINIO_ROOT_USER MILVUS_MINIO_ROOT_PASSWORD
readonly MILVUS_INITIAL_ROOT_PASSWORD MILVUS_ROOT_PASSWORD MILVUS_READER_USERNAME
readonly MILVUS_READER_PASSWORD MILVUS_WRITER_USERNAME MILVUS_WRITER_PASSWORD
readonly MILVUS_PROVISIONER_USERNAME MILVUS_PROVISIONER_PASSWORD
readonly ATHENA_COLLECTION ATHENA_ALIAS ATHENA_CORPUS_VERSION ATHENA_CHAT_ALIAS
readonly ATHENA_EMBEDDING_ALIAS ATHENA_RETRIEVAL_PROFILE ATHENA_EMBEDDING_DIMENSION
readonly ATHENA_INDEX_VERSION ATHENA_PIPELINE_VERSION ATHENA_WORKER_ID
readonly ATHENA_POLL_SECONDS ATHENA_JOB_BATCH_SIZE ATHENA_READY_TIMEOUT_SECONDS
readonly ATHENA_MODEL_TIMEOUT_SECONDS ATHENA_BLOB_TIMEOUT_SECONDS ATHENA_MILVUS_TIMEOUT_SECONDS

cd "$athena_e2e_repo_root"

if ! uv run --project apps/backend python -c \
  'import os; from tap.entrypoints.athena_runtime import AthenaSettings; AthenaSettings.from_mapping(dict(os.environ))' \
  >/dev/null 2>&1; then
  echo "Athena E2E configuration is invalid." >&2
  exit 2
fi

if ! uv run --project apps/backend python - \
  13306 16379 11000 14000 29530 19091 18000 15173 <<'PY'
import socket
import sys

for raw_port in sys.argv[1:]:
    port = int(raw_port)
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
    except OSError:
        print(f"Athena E2E fixed port {port} is unavailable.", file=sys.stderr)
        raise SystemExit(1)
    finally:
        probe.close()
PY
then
  exit 2
fi

chromium_path="$(
  cd "$athena_e2e_repo_root" &&
    corepack pnpm --filter @tap/web exec node --input-type=module -e \
      'import { chromium } from "@playwright/test"; process.stdout.write(chromium.executablePath())'
)" || {
  echo "Chromium is missing; run: corepack pnpm --filter @tap/web exec playwright install chromium" >&2
  exit 2
}
if [ -z "$chromium_path" ] || [ ! -x "$chromium_path" ]; then
  echo "Chromium is missing; run: corepack pnpm --filter @tap/web exec playwright install chromium" >&2
  exit 2
fi

docker_context="$(docker context show 2>/dev/null)" || {
  echo "Athena E2E requires a local Docker context." >&2
  exit 2
}
case "$docker_context" in
  ''|[-.]*|*[!A-Za-z0-9_.-]*)
    echo "Athena E2E requires a local Docker context." >&2
    exit 2
    ;;
esac
docker_endpoint="$(
  docker context inspect "$docker_context" \
    --format '{{(index .Endpoints "docker").Host}}' 2>/dev/null
)" || {
  echo "Athena E2E requires a local Docker context." >&2
  exit 2
}
case "$docker_endpoint" in
  unix://*|npipe://*) ;;
  *)
    echo "Athena E2E requires a local Docker context." >&2
    exit 2
    ;;
esac

readonly docker_context

if [ "$athena_e2e_preflight_only" -eq 1 ]; then
  echo "Athena E2E preflight passed."
  exit 0
fi

compose() {
  docker --context "$docker_context" compose \
    -f "$athena_e2e_compose_file" -p "$athena_e2e_project" --profile milvus "$@"
}

stop_apps() {
  [ -n "$athena_e2e_apps_pid" ] || return 0
  current_pid="$athena_e2e_apps_pid"
  sent_term=0
  forced=0
  if kill -0 "$current_pid" 2>/dev/null; then
    kill -TERM "$current_pid" 2>/dev/null || return 1
    sent_term=1
  fi
  stop_deadline=$(( SECONDS + 120 ))
  while kill -0 "$current_pid" 2>/dev/null && [ "$SECONDS" -lt "$stop_deadline" ]; do
    sleep 0.1
  done
  if kill -0 "$current_pid" 2>/dev/null; then
    kill -KILL "$current_pid" 2>/dev/null || return 1
    forced=1
  fi
  if wait "$current_pid"; then
    app_status=0
  else
    app_status=$?
  fi
  athena_e2e_apps_pid=""
  if [ "$forced" -eq 1 ] || [ "$sent_term" -eq 0 ]; then
    [ "$app_status" -ne 0 ] && return "$app_status"
    return 1
  fi
  case "$app_status" in
    0|143) return 0 ;;
    *) return "$app_status" ;;
  esac
}

cleanup() {
  primary_status=$?
  trap - EXIT
  trap '' INT TERM
  if [ "$athena_e2e_cleanup_started" -eq 1 ]; then
    exit "$primary_status"
  fi
  athena_e2e_cleanup_started=1
  cleanup_failed=0
  stop_apps || cleanup_failed=1
  if [ "$athena_e2e_compose_mutated" -eq 1 ]; then
    compose down --volumes --remove-orphans >/dev/null 2>&1 || cleanup_failed=1
  fi
  if [ -n "$athena_e2e_state_dir" ]; then
    case "$athena_e2e_state_dir" in
      "$athena_e2e_state_root"/tap-athena-e2e.*) \
        rm -rf "$athena_e2e_state_dir" || cleanup_failed=1 ;;
      *) cleanup_failed=1 ;;
    esac
  fi
  if [ "$athena_e2e_lock_owned" -eq 1 ]; then
    rmdir "$athena_e2e_lock_dir" 2>/dev/null || cleanup_failed=1
    athena_e2e_lock_owned=0
  fi
  if [ "$cleanup_failed" -ne 0 ]; then
    echo "Athena E2E cleanup failed." >&2
    [ "$primary_status" -ne 0 ] || primary_status=1
  fi
  exit "$primary_status"
}

trap '' INT TERM
if ! mkdir "$athena_e2e_lock_dir" 2>/dev/null; then
  trap - INT TERM
  echo "Another Athena E2E run owns the local isolation lock." >&2
  exit 2
fi
athena_e2e_lock_owned=1
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

athena_e2e_state_dir="$(mktemp -d "$athena_e2e_state_root/tap-athena-e2e.XXXXXX")" || {
  echo "Athena E2E could not create private state." >&2
  exit 2
}
chmod 700 "$athena_e2e_state_dir"
export ATHENA_E2E_STATE_FILE="$athena_e2e_state_dir/state.json"

bootstrap_middleware() {
  compose up -d --wait --wait-timeout 180
  uv run --project apps/backend alembic -c apps/backend/alembic.ini upgrade head
  TAP_ALLOW_INITIAL_MILVUS_ROOT=1 \
    uv run --project apps/backend python scripts/milvus_bootstrap.py
  uv run --project apps/backend python scripts/athena_collection.py ensure
}

athena_e2e_api_http_ready() {
  curl --fail --silent --show-error --max-time 2 --max-filesize 65536 \
    "http://127.0.0.1:18000/health/ready" >"$1" 2>/dev/null
}

athena_e2e_api_body_ready() {
  uv run --project apps/backend python - "$1" >/dev/null 2>&1 <<'PY'
import json
import sys
from pathlib import Path

ready_path = Path(sys.argv[1])
if ready_path.stat().st_size > 65536:
    raise SystemExit(1)
with ready_path.open(encoding="utf-8") as handle:
    value = json.load(handle)
if not isinstance(value, dict) or value.get("status") != "ready":
    raise SystemExit(1)
PY
}

athena_e2e_web_http_ready() {
  curl --fail --silent --show-error --max-time 2 --max-filesize 65536 \
    "http://127.0.0.1:15173/" >/dev/null 2>&1
}

athena_e2e_apps_job_is_running() {
  local athena_e2e_running_pid
  while IFS= read -r athena_e2e_running_pid; do
    if [ "$athena_e2e_running_pid" = "$athena_e2e_apps_pid" ]; then return 0; fi
  done < <(jobs -pr)
  return 1
}

start_apps() {
  local athena_e2e_ready_stage="supervisor"
  ATHENA_SUPERVISOR_ENV=preloaded \
    /bin/bash scripts/run-athena-dev.sh >"$athena_e2e_state_dir/apps.log" 2>&1 &
  athena_e2e_apps_pid=$!
  ready_file="$athena_e2e_state_dir/ready.json"
  ready_deadline=$(( SECONDS + 120 ))
  while [ "$SECONDS" -lt "$ready_deadline" ]; do
    if ! athena_e2e_apps_job_is_running; then
      if wait "$athena_e2e_apps_pid"; then app_status=1; else app_status=$?; fi
      athena_e2e_apps_pid=""
      echo "Athena E2E applications did not become ready at supervisor." >&2
      return "$app_status"
    fi
    athena_e2e_ready_stage="api-http"
    if ! athena_e2e_api_http_ready "$ready_file"; then
      sleep 0.2
      continue
    fi
    athena_e2e_ready_stage="api-body"
    if ! athena_e2e_api_body_ready "$ready_file"; then
      sleep 0.2
      continue
    fi
    athena_e2e_ready_stage="web-http"
    if athena_e2e_web_http_ready; then
      if athena_e2e_apps_job_is_running; then
        rm -f "$ready_file"
        return 0
      fi
      if wait "$athena_e2e_apps_pid"; then app_status=1; else app_status=$?; fi
      athena_e2e_apps_pid=""
      echo "Athena E2E applications did not become ready at supervisor." >&2
      return "$app_status"
    fi
    sleep 0.2
  done
  case "$athena_e2e_ready_stage" in
    supervisor|api-http|api-body|web-http) ;;
    *) athena_e2e_ready_stage="supervisor" ;;
  esac
  echo "Athena E2E applications did not become ready at $athena_e2e_ready_stage." >&2
  return 1
}

run_playwright() {
  spec_file="$1"
  phase="$2"
  report_file="$athena_e2e_state_dir/playwright-$phase.json"
  error_file="$athena_e2e_state_dir/playwright-$phase.err"
  if ATHENA_E2E_PHASE="$phase" \
    corepack pnpm --filter @tap/web exec playwright test "$spec_file" \
      --config=playwright.config.ts --reporter=json >"$report_file" 2>"$error_file"; then
    :
  else
    phase_status=$?
    echo "Athena E2E phase $phase failed." >&2
    return "$phase_status"
  fi
  if uv run --project apps/backend python - "$report_file" >/dev/null 2>&1 <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    report = json.load(handle)
stats = report.get("stats") if isinstance(report, dict) else None
if not isinstance(stats, dict):
    raise SystemExit(1)
if (
    stats.get("expected") != 1
    or stats.get("unexpected") != 0
    or stats.get("flaky") != 0
    or stats.get("skipped") != 0
):
    raise SystemExit(1)
PY
  then
    return 0
  fi
  echo "Athena E2E phase $phase returned an invalid result." >&2
  return 1
}

run_journey() {
  athena_e2e_compose_mutated=1
  compose down --volumes --remove-orphans
  bootstrap_middleware
  start_apps
  run_playwright tests/e2e/athena.spec.ts journey

  stop_apps
  start_apps
  run_playwright tests/e2e/persistence.spec.ts app-restart

  stop_apps
  compose down --remove-orphans
  bootstrap_middleware
  start_apps
  run_playwright tests/e2e/persistence.spec.ts compose-restart

  ATHENA_E2E_PHASE=verify TAP_RUN_ATHENA_E2E=1 \
    uv run --project apps/backend pytest -q \
      apps/backend/tests/integration/test_athena_persistence_restart.py
}

run_journey
echo "Athena isolated E2E journey passed."
