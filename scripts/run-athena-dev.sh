#!/bin/bash
set -euo pipefail

unset athena_dev_caller_codex_home_set athena_dev_caller_codex_home_value
athena_dev_caller_codex_home_set=0
athena_dev_caller_codex_home_value=""
if [ "${CODEX_HOME+x}" = x ]; then
  athena_dev_caller_codex_home_set=1
  athena_dev_caller_codex_home_value="$CODEX_HOME"
fi
unset CODEX_HOME CODEX_API_KEY CODEX_BASE_URL CODEX_API_BASE
unset OPENAI_API_KEY OPENAI_BASE_URL OPENAI_API_BASE
unset DASHSCOPE_API_KEY DASHSCOPE_BASE_URL DASHSCOPE_API_BASE
unset LITELLM_EMBEDDING_API_KEY LITELLM_EMBEDDING_API_BASE
readonly athena_dev_caller_codex_home_set athena_dev_caller_codex_home_value

athena_dev_script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
athena_dev_repo_root="$(CDPATH= cd -- "$athena_dev_script_dir/.." && pwd)"
athena_dev_requested_project="${TAP_ATHENA_COMPOSE_PROJECT:-tap-athena-demo}"
readonly athena_dev_script_dir athena_dev_repo_root athena_dev_requested_project

validate_project() {
  case "$1" in
    ''|[-_]*|*[!a-z0-9_-]*) return 1 ;;
  esac
  [ "${#1}" -ge 3 ] && [ "${#1}" -le 63 ]
}

if ! validate_project "$athena_dev_requested_project"; then
  echo "invalid Athena Compose project" >&2
  exit 2
fi

if [ "${ATHENA_SUPERVISOR_ENV:-}" != "preloaded" ] && \
  [ -f "$athena_dev_repo_root/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$athena_dev_repo_root/.env"
  set +a
fi
export TAP_ATHENA_COMPOSE_PROJECT="$athena_dev_requested_project"
export ATHENA_API_HOST="${ATHENA_API_HOST:-127.0.0.1}"
export ATHENA_API_PORT="${ATHENA_API_PORT:-8000}"
export ATHENA_WEB_HOST="${ATHENA_WEB_HOST:-127.0.0.1}"
export ATHENA_WEB_PORT="${ATHENA_WEB_PORT:-5173}"

unset athena_dev_codex_home_set athena_dev_codex_home_value
athena_dev_codex_home_set=0
athena_dev_codex_home_value=""
if [ "${CODEX_HOME+x}" = x ]; then
  athena_dev_codex_home_set=1
  athena_dev_codex_home_value="$CODEX_HOME"
elif [ "$athena_dev_caller_codex_home_set" -eq 1 ]; then
  athena_dev_codex_home_set=1
  athena_dev_codex_home_value="$athena_dev_caller_codex_home_value"
fi
unset CODEX_HOME CODEX_API_KEY CODEX_BASE_URL CODEX_API_BASE
unset OPENAI_API_KEY OPENAI_BASE_URL OPENAI_API_BASE
unset DASHSCOPE_API_KEY DASHSCOPE_BASE_URL DASHSCOPE_API_BASE
unset LITELLM_EMBEDDING_API_KEY LITELLM_EMBEDDING_API_BASE
readonly athena_dev_codex_home_set athena_dev_codex_home_value

cd "$athena_dev_repo_root"

if ! uv run --project apps/backend python -c \
  'import os; from tap.entrypoints.athena_runtime import AthenaSettings; AthenaSettings.from_mapping(dict(os.environ))' \
  >/dev/null 2>&1; then
  echo "Athena configuration is invalid; check .env.example." >&2
  exit 2
fi

athena_dev_web_root="$athena_dev_repo_root/apps/web"
athena_dev_vite_bin="$athena_dev_web_root/node_modules/.bin/vite"
athena_dev_vite_config="$athena_dev_web_root/vite.config.ts"
readonly athena_dev_web_root athena_dev_vite_bin athena_dev_vite_config
if [ ! -x "$athena_dev_vite_bin" ] || \
  [ ! -f "$athena_dev_web_root/index.html" ] || \
  [ ! -f "$athena_dev_vite_config" ]; then
  echo "Web dependencies are missing; run make bootstrap." >&2
  exit 2
fi

athena_dev_api_pid=""
athena_dev_relay_pid=""
athena_dev_worker_pid=""
athena_dev_web_pid=""
athena_dev_ready_file=""
athena_dev_cleanup_started=0
athena_dev_shutdown_grace_seconds=90

terminate_pid() {
  target_pid="$1"
  [ -n "$target_pid" ] || return 0
  if kill -0 "$target_pid" 2>/dev/null; then
    kill -TERM "$target_pid" 2>/dev/null || return 1
  fi
}

cleanup() {
  primary_status=$?
  trap - EXIT
  trap '' INT TERM
  if [ "$athena_dev_cleanup_started" -eq 1 ]; then
    exit "$primary_status"
  fi
  athena_dev_cleanup_started=1
  cleanup_failed=0

  if [ -n "$athena_dev_ready_file" ]; then
    rm -f "$athena_dev_ready_file" 2>/dev/null || cleanup_failed=1
    athena_dev_ready_file=""
  fi

  for child_pid in "$athena_dev_web_pid" "$athena_dev_worker_pid" \
    "$athena_dev_relay_pid" "$athena_dev_api_pid"; do
    terminate_pid "$child_pid" || cleanup_failed=1
  done

  deadline=$(( SECONDS + athena_dev_shutdown_grace_seconds ))
  while :; do
    live=0
    for child_pid in "$athena_dev_web_pid" "$athena_dev_worker_pid" \
      "$athena_dev_relay_pid" "$athena_dev_api_pid"; do
      if [ -n "$child_pid" ] && kill -0 "$child_pid" 2>/dev/null; then
        live=1
      fi
    done
    [ "$live" -eq 0 ] && break
    [ "$SECONDS" -ge "$deadline" ] && break
    sleep 0.1 || cleanup_failed=1
  done

  for child_pid in "$athena_dev_web_pid" "$athena_dev_worker_pid" \
    "$athena_dev_relay_pid" "$athena_dev_api_pid"; do
    if [ -n "$child_pid" ] && kill -0 "$child_pid" 2>/dev/null; then
      kill -KILL "$child_pid" 2>/dev/null || cleanup_failed=1
    fi
  done
  for child_pid in "$athena_dev_web_pid" "$athena_dev_worker_pid" \
    "$athena_dev_relay_pid" "$athena_dev_api_pid"; do
    if [ -n "$child_pid" ]; then
      wait "$child_pid" 2>/dev/null || true
    fi
  done

  if [ "$cleanup_failed" -ne 0 ]; then
    echo "Athena application cleanup failed." >&2
    [ "$primary_status" -ne 0 ] || primary_status=1
  fi
  exit "$primary_status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

(
  if [ "$athena_dev_codex_home_set" -eq 1 ]; then
    export CODEX_HOME="$athena_dev_codex_home_value"
  fi
  exec uv run --project apps/backend python -m tap.entrypoints.athena_api
) &
athena_dev_api_pid=$!
(exec uv run --project apps/backend python -m tap.entrypoints.relay_reconciler) &
athena_dev_relay_pid=$!
(exec uv run --project apps/backend python -m tap.entrypoints.athena_ingestion_worker) &
athena_dev_worker_pid=$!
(exec "$athena_dev_vite_bin" "$athena_dev_web_root" \
  --config "$athena_dev_vite_config" \
  --host "$ATHENA_WEB_HOST" --port "$ATHENA_WEB_PORT" --strictPort) &
athena_dev_web_pid=$!

child_exit_status() {
  if [ -n "$athena_dev_api_pid" ] && ! kill -0 "$athena_dev_api_pid" 2>/dev/null; then
    if wait "$athena_dev_api_pid"; then status=1; else status=$?; fi
    athena_dev_api_pid=""
    [ "$status" -gt 0 ] && [ "$status" -le 255 ] || status=1
    return "$status"
  fi
  if [ -n "$athena_dev_relay_pid" ] && \
    ! kill -0 "$athena_dev_relay_pid" 2>/dev/null; then
    if wait "$athena_dev_relay_pid"; then status=1; else status=$?; fi
    athena_dev_relay_pid=""
    [ "$status" -gt 0 ] && [ "$status" -le 255 ] || status=1
    return "$status"
  fi
  if [ -n "$athena_dev_worker_pid" ] && \
    ! kill -0 "$athena_dev_worker_pid" 2>/dev/null; then
    if wait "$athena_dev_worker_pid"; then status=1; else status=$?; fi
    athena_dev_worker_pid=""
    [ "$status" -gt 0 ] && [ "$status" -le 255 ] || status=1
    return "$status"
  fi
  if [ -n "$athena_dev_web_pid" ] && ! kill -0 "$athena_dev_web_pid" 2>/dev/null; then
    if wait "$athena_dev_web_pid"; then status=1; else status=$?; fi
    athena_dev_web_pid=""
    [ "$status" -gt 0 ] && [ "$status" -le 255 ] || status=1
    return "$status"
  fi
  return 0
}

athena_dev_ready_file="$(mktemp "${TMPDIR:-/tmp}/tap-athena-ready.XXXXXX")"
ready_deadline=$(( SECONDS + 90 ))
ready=0
while [ "$SECONDS" -lt "$ready_deadline" ]; do
  if child_exit_status; then
    :
  else
    status=$?
    exit "$status"
  fi
  if curl --fail --silent --show-error --max-time 2 --max-filesize 65536 \
    "http://$ATHENA_API_HOST:$ATHENA_API_PORT/health/ready" \
      >"$athena_dev_ready_file" 2>/dev/null && \
    uv run --project apps/backend python - "$athena_dev_ready_file" >/dev/null 2>&1 <<'PY'
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
  then
    ready=1
    break
  fi
  sleep 0.2
done
rm -f "$athena_dev_ready_file"
athena_dev_ready_file=""

if [ "$ready" -ne 1 ]; then
  echo "Athena applications did not become ready." >&2
  exit 1
fi
echo "Athena local applications ready."

while :; do
  if child_exit_status; then
    sleep 0.2
  else
    status=$?
    exit "$status"
  fi
done
