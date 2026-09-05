#!/bin/bash
set -euo pipefail

unset tapper_dev_caller_codex_home_set tapper_dev_caller_codex_home_value
tapper_dev_caller_codex_home_set=0
tapper_dev_caller_codex_home_value=""
if [ "${CODEX_HOME+x}" = x ]; then
  tapper_dev_caller_codex_home_set=1
  tapper_dev_caller_codex_home_value="$CODEX_HOME"
fi
unset CODEX_HOME CODEX_API_KEY CODEX_BASE_URL CODEX_API_BASE
unset OPENAI_API_KEY OPENAI_BASE_URL OPENAI_API_BASE
unset DASHSCOPE_API_KEY DASHSCOPE_BASE_URL DASHSCOPE_API_BASE
unset LITELLM_EMBEDDING_API_KEY LITELLM_EMBEDDING_API_BASE
readonly tapper_dev_caller_codex_home_set tapper_dev_caller_codex_home_value

tapper_dev_script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
tapper_dev_repo_root="$(CDPATH= cd -- "$tapper_dev_script_dir/.." && pwd)"
tapper_dev_requested_project="${TAP_TAPPER_COMPOSE_PROJECT:-tap-tapper-demo}"
readonly tapper_dev_script_dir tapper_dev_repo_root tapper_dev_requested_project

validate_project() {
  case "$1" in
    ''|[-_]*|*[!a-z0-9_-]*) return 1 ;;
  esac
  [ "${#1}" -ge 3 ] && [ "${#1}" -le 63 ]
}

if ! validate_project "$tapper_dev_requested_project"; then
  echo "invalid Tapper Compose project" >&2
  exit 2
fi

if [ "${TAPPER_SUPERVISOR_ENV:-}" != "preloaded" ] && \
  [ -f "$tapper_dev_repo_root/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$tapper_dev_repo_root/.env"
  set +a
fi
unset DASHSCOPE_API_KEY DASHSCOPE_API_BASE OPENAI_API_KEY
unset BAILIAN_API_KEY BAILIAN_API_BASE
unset LITELLM_EMBEDDING_API_KEY LITELLM_EMBEDDING_API_BASE
export TAP_TAPPER_COMPOSE_PROJECT="$tapper_dev_requested_project"
export TAPPER_API_HOST="${TAPPER_API_HOST:-127.0.0.1}"
export TAPPER_API_PORT="${TAPPER_API_PORT:-8000}"
export TAPPER_WEB_HOST="${TAPPER_WEB_HOST:-127.0.0.1}"
export TAPPER_WEB_PORT="${TAPPER_WEB_PORT:-5173}"

unset tapper_dev_codex_home_set tapper_dev_codex_home_value
tapper_dev_codex_home_set=0
tapper_dev_codex_home_value=""
if [ "${CODEX_HOME+x}" = x ]; then
  tapper_dev_codex_home_set=1
  tapper_dev_codex_home_value="$CODEX_HOME"
elif [ "$tapper_dev_caller_codex_home_set" -eq 1 ]; then
  tapper_dev_codex_home_set=1
  tapper_dev_codex_home_value="$tapper_dev_caller_codex_home_value"
fi
unset CODEX_HOME CODEX_API_KEY CODEX_BASE_URL CODEX_API_BASE
unset OPENAI_API_KEY OPENAI_BASE_URL OPENAI_API_BASE
unset DASHSCOPE_API_KEY DASHSCOPE_BASE_URL DASHSCOPE_API_BASE
unset LITELLM_EMBEDDING_API_KEY LITELLM_EMBEDDING_API_BASE
readonly tapper_dev_codex_home_set tapper_dev_codex_home_value

cd "$tapper_dev_repo_root"

if ! uv run --project apps/backend python -c \
  'import os; from tap.entrypoints.tapper_runtime import TapperSettings; TapperSettings.from_mapping(dict(os.environ))' \
  >/dev/null 2>&1; then
  echo "Tapper configuration is invalid; check .env.example." >&2
  exit 2
fi

tapper_dev_web_root="$tapper_dev_repo_root/apps/web"
tapper_dev_vite_bin="$tapper_dev_web_root/node_modules/.bin/vite"
tapper_dev_vite_config="$tapper_dev_web_root/vite.config.ts"
readonly tapper_dev_web_root tapper_dev_vite_bin tapper_dev_vite_config
if [ ! -x "$tapper_dev_vite_bin" ] || \
  [ ! -f "$tapper_dev_web_root/index.html" ] || \
  [ ! -f "$tapper_dev_vite_config" ]; then
  echo "Web dependencies are missing; run make bootstrap." >&2
  exit 2
fi

tapper_dev_api_pid=""
tapper_dev_relay_pid=""
tapper_dev_worker_pid=""
tapper_dev_web_pid=""
tapper_dev_ready_file=""
tapper_dev_cleanup_started=0
tapper_dev_shutdown_grace_seconds=90

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
  if [ "$tapper_dev_cleanup_started" -eq 1 ]; then
    exit "$primary_status"
  fi
  tapper_dev_cleanup_started=1
  cleanup_failed=0

  if [ -n "$tapper_dev_ready_file" ]; then
    rm -f "$tapper_dev_ready_file" 2>/dev/null || cleanup_failed=1
    tapper_dev_ready_file=""
  fi

  for child_pid in "$tapper_dev_web_pid" "$tapper_dev_worker_pid" \
    "$tapper_dev_relay_pid" "$tapper_dev_api_pid"; do
    terminate_pid "$child_pid" || cleanup_failed=1
  done

  deadline=$(( SECONDS + tapper_dev_shutdown_grace_seconds ))
  while :; do
    live=0
    for child_pid in "$tapper_dev_web_pid" "$tapper_dev_worker_pid" \
      "$tapper_dev_relay_pid" "$tapper_dev_api_pid"; do
      if [ -n "$child_pid" ] && kill -0 "$child_pid" 2>/dev/null; then
        live=1
      fi
    done
    [ "$live" -eq 0 ] && break
    [ "$SECONDS" -ge "$deadline" ] && break
    sleep 0.1 || cleanup_failed=1
  done

  for child_pid in "$tapper_dev_web_pid" "$tapper_dev_worker_pid" \
    "$tapper_dev_relay_pid" "$tapper_dev_api_pid"; do
    if [ -n "$child_pid" ] && kill -0 "$child_pid" 2>/dev/null; then
      kill -KILL "$child_pid" 2>/dev/null || cleanup_failed=1
    fi
  done
  for child_pid in "$tapper_dev_web_pid" "$tapper_dev_worker_pid" \
    "$tapper_dev_relay_pid" "$tapper_dev_api_pid"; do
    if [ -n "$child_pid" ]; then
      wait "$child_pid" 2>/dev/null || true
    fi
  done

  if [ "$cleanup_failed" -ne 0 ]; then
    echo "Tapper application cleanup failed." >&2
    [ "$primary_status" -ne 0 ] || primary_status=1
  fi
  exit "$primary_status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

(
  if [ "$tapper_dev_codex_home_set" -eq 1 ]; then
    export CODEX_HOME="$tapper_dev_codex_home_value"
  fi
  exec uv run --project apps/backend python -m tap.entrypoints.tapper_api
) &
tapper_dev_api_pid=$!
(exec uv run --project apps/backend python -m tap.entrypoints.relay_reconciler) &
tapper_dev_relay_pid=$!
(exec uv run --project apps/backend python -m tap.entrypoints.tapper_ingestion_worker) &
tapper_dev_worker_pid=$!
(exec "$tapper_dev_vite_bin" "$tapper_dev_web_root" \
  --config "$tapper_dev_vite_config" \
  --host "$TAPPER_WEB_HOST" --port "$TAPPER_WEB_PORT" --strictPort) &
tapper_dev_web_pid=$!

child_exit_status() {
  if [ -n "$tapper_dev_api_pid" ] && ! kill -0 "$tapper_dev_api_pid" 2>/dev/null; then
    if wait "$tapper_dev_api_pid"; then status=1; else status=$?; fi
    tapper_dev_api_pid=""
    [ "$status" -gt 0 ] && [ "$status" -le 255 ] || status=1
    return "$status"
  fi
  if [ -n "$tapper_dev_relay_pid" ] && \
    ! kill -0 "$tapper_dev_relay_pid" 2>/dev/null; then
    if wait "$tapper_dev_relay_pid"; then status=1; else status=$?; fi
    tapper_dev_relay_pid=""
    [ "$status" -gt 0 ] && [ "$status" -le 255 ] || status=1
    return "$status"
  fi
  if [ -n "$tapper_dev_worker_pid" ] && \
    ! kill -0 "$tapper_dev_worker_pid" 2>/dev/null; then
    if wait "$tapper_dev_worker_pid"; then status=1; else status=$?; fi
    tapper_dev_worker_pid=""
    [ "$status" -gt 0 ] && [ "$status" -le 255 ] || status=1
    return "$status"
  fi
  if [ -n "$tapper_dev_web_pid" ] && ! kill -0 "$tapper_dev_web_pid" 2>/dev/null; then
    if wait "$tapper_dev_web_pid"; then status=1; else status=$?; fi
    tapper_dev_web_pid=""
    [ "$status" -gt 0 ] && [ "$status" -le 255 ] || status=1
    return "$status"
  fi
  return 0
}

tapper_dev_ready_file="$(mktemp "${TMPDIR:-/tmp}/tap-tapper-ready.XXXXXX")"
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
    "http://$TAPPER_API_HOST:$TAPPER_API_PORT/health/ready" \
      >"$tapper_dev_ready_file" 2>/dev/null && \
    uv run --project apps/backend python - "$tapper_dev_ready_file" >/dev/null 2>&1 <<'PY'
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
rm -f "$tapper_dev_ready_file"
tapper_dev_ready_file=""

if [ "$ready" -ne 1 ]; then
  echo "Tapper applications did not become ready." >&2
  exit 1
fi
echo "Tapper local applications ready."

while :; do
  if child_exit_status; then
    sleep 0.2
  else
    status=$?
    exit "$status"
  fi
done
