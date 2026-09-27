#!/usr/bin/env bash
set -euo pipefail
umask 077

insights_script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
insights_repo_root="$(CDPATH= cd -- "$insights_script_dir/.." && pwd)"
if [ "${1:-}" != "--redacted-child" ]; then
  exec python3 "$insights_script_dir/insights_runtime.py" run-redacted
fi
insights_nonce="$(openssl rand -hex 6)"
insights_project="${TAP_INSIGHTS_E2E_PROJECT:-tap-insights-task12-$insights_nonce}"
insights_state_dir="$(mktemp -d "${TMPDIR:-/tmp}/tap-insights-task12.XXXXXX")"
insights_artifacts="${TAP_INSIGHTS_E2E_ARTIFACTS:-$insights_repo_root/.superpowers/artifacts/task-12}"
insights_preserve_volumes="${TAP_INSIGHTS_E2E_PRESERVE_VOLUMES:-0}"
insights_runtime_dir="$insights_repo_root/.superpowers/runtime/insights/$insights_project"
insights_app_pids=""
insights_compose_started=0
readonly insights_script_dir insights_repo_root insights_project insights_state_dir insights_preserve_volumes insights_runtime_dir

case "$insights_project" in
  tap-insights-task12-[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f] | tap-insights-task14-[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]-journey) ;;
  *) echo "refusing unsafe TAP Insights Compose project" >&2; exit 2 ;;
esac

case "$insights_artifacts" in
  "$insights_repo_root"/.superpowers/artifacts/* | "${TMPDIR:-/tmp}"/tap-task14.*) ;;
  *) echo "refusing unsafe TAP Insights artifact path" >&2; exit 2 ;;
esac

case "$insights_preserve_volumes" in
  0 | 1) ;;
  *) echo "TAP_INSIGHTS_E2E_PRESERVE_VOLUMES must be 0 or 1" >&2; exit 2 ;;
esac

case "$insights_state_dir" in
  "${TMPDIR:-/tmp}"/tap-insights-task12.*) ;;
  *) echo "refusing unsafe TAP Insights state path" >&2; exit 2 ;;
esac

export MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-$(openssl rand -hex 24)}"
export MYSQL_DATABASE="${MYSQL_DATABASE:-tap_task12_$insights_nonce}"
export MYSQL_USER="${MYSQL_USER:-tap}"
export MYSQL_PASSWORD="${MYSQL_PASSWORD:-$(openssl rand -hex 24)}"
export MYSQL_PORT="${MYSQL_PORT:-33329}"
export CLICKHOUSE_HTTP_PORT="${CLICKHOUSE_HTTP_PORT:-38129}"
export CLICKHOUSE_ADMIN_USER="${CLICKHOUSE_ADMIN_USER:-tap_insights_admin}"
export CLICKHOUSE_ADMIN_PASSWORD="${CLICKHOUSE_ADMIN_PASSWORD:-$(openssl rand -hex 24)}"
export TAP_CLICKHOUSE_WRITER_USER="${TAP_CLICKHOUSE_WRITER_USER:-tap_insights_writer}"
export TAP_CLICKHOUSE_WRITER_PASSWORD="${TAP_CLICKHOUSE_WRITER_PASSWORD:-$(openssl rand -hex 24)}"
export TAP_CLICKHOUSE_READER_USER="${TAP_CLICKHOUSE_READER_USER:-tap_insights_reader}"
export TAP_CLICKHOUSE_READER_PASSWORD="${TAP_CLICKHOUSE_READER_PASSWORD:-$(openssl rand -hex 24)}"
export TAP_DATABASE_URL="mysql+pymysql://$MYSQL_USER:$MYSQL_PASSWORD@127.0.0.1:$MYSQL_PORT/$MYSQL_DATABASE?charset=utf8mb4"
export TAP_REPORT_OBJECT_ROOT="$insights_runtime_dir/objects"
export TAP_CLICKHOUSE_URL="http://127.0.0.1:$CLICKHOUSE_HTTP_PORT/?database=tap_insights"
export TAP_REPORT_ACCESS_TOKEN="${TAP_REPORT_ACCESS_TOKEN:-$(openssl rand -hex 24)}"
export TAP_REPORT_PROJECT_ID=project-a
export TAP_REPORT_TOKEN_EXPIRES_AT=2099-12-31T23:59:59+00:00
export TAP_REPORT_WORKER_POLL_SECONDS=0.1
export TAP_WEB_API_TARGET=http://127.0.0.1:18012
export TAP_INSIGHTS_E2E_BASE_URL=http://127.0.0.1:15182
export TAP_INSIGHTS_E2E_AUTH_STATE="$insights_runtime_dir/auth-state.json"
export TAP_INSIGHTS_E2E_SCREENSHOTS="${TAP_INSIGHTS_E2E_SCREENSHOTS:-$insights_artifacts}"
export TAP_REPO_ROOT="$insights_repo_root"

mkdir -p "$insights_artifacts" "$TAP_INSIGHTS_E2E_SCREENSHOTS"
insights_artifacts="$(CDPATH= cd -- "$insights_artifacts" && pwd -P)"
case "$insights_artifacts" in
  "$insights_repo_root"/.superpowers/artifacts/* | "${TMPDIR:-/tmp}"/tap-task14.*) ;;
  *) echo "refusing unsafe resolved Insights artifacts" >&2; exit 2 ;;
esac

insights_redact() {
  python3 "$insights_script_dir/insights_runtime.py" redact
}

insights_compose() {
  docker compose -f "$insights_repo_root/compose.yaml" -p "$insights_project" --profile insights "$@"
}

insights_stop_apps() {
  if [ -n "$insights_app_pids" ]; then
    kill $insights_app_pids 2>/dev/null || true
    wait $insights_app_pids 2>/dev/null || true
    insights_app_pids=""
  fi
}

insights_cleanup() {
  insights_stop_apps
  for log in "$insights_state_dir"/*.log; do
    [ -f "$log" ] && insights_redact <"$log" >"$insights_artifacts/$(basename "$log")"
  done
  if [ "$insights_compose_started" -eq 1 ]; then
    if ! python3 "$insights_script_dir/insights_runtime.py" verify-cleanup --compose-project "$insights_project"; then
      echo "refusing cleanup after Insights resource ownership changed" >&2
    elif [ "$insights_preserve_volumes" = "1" ]; then
      insights_compose down --remove-orphans >/dev/null 2>&1 || true
    else
      insights_compose down -v --remove-orphans >/dev/null 2>&1 || true
    fi
  fi
  rm -rf -- "$insights_state_dir"
}
trap insights_cleanup EXIT INT TERM

insights_wait_url() {
  local url="$1"
  local attempts=0
  until curl --fail --silent --show-error "$url" >/dev/null; do
    attempts=$((attempts + 1))
    if [ "$attempts" -ge 120 ]; then
      echo "timed out waiting for $url" >&2
      return 1
    fi
    sleep 0.25
  done
}

insights_require_free_port() {
  local port="$1"
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | rg -q .; then
    echo "owned TAP Insights port $port is already in use" >&2
    return 1
  fi
}

insights_start_apps() {
  insights_require_free_port 18012
  insights_require_free_port 15182
  (
    cd "$insights_repo_root"
    exec uv run --project apps/backend uvicorn tap_platform.app:app --host 127.0.0.1 --port 18012 \
      >"$insights_state_dir/backend.log" 2>&1
  ) &
  local backend_pid=$!
  (
    cd "$insights_repo_root"
    exec uv run --project apps/backend python -m tap_platform.insights.worker \
      >"$insights_state_dir/worker.log" 2>&1
  ) &
  local worker_pid=$!
  (
    cd "$insights_repo_root"
    exec corepack pnpm --dir apps/web exec vite --host 127.0.0.1 --port 15182 \
      >"$insights_state_dir/web.log" 2>&1
  ) &
  local web_pid=$!
  insights_app_pids="$backend_pid $worker_pid $web_pid"
  insights_wait_url http://127.0.0.1:18012/health/live
  insights_wait_url http://127.0.0.1:15182/
  for pid in $insights_app_pids; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "a TAP Insights process exited before taking ownership" >&2
      return 1
    fi
  done
}

insights_run_phase() {
  export TAP_INSIGHTS_E2E_PHASE="$1"
  corepack pnpm --dir "$insights_repo_root/apps/web" exec playwright test \
    --config playwright.insights.config.ts insights-report.spec.ts
}

cd "$insights_repo_root"
existing="$(docker ps -aq --filter "label=com.docker.compose.project=$insights_project")"
existing="$existing$(docker volume ls -q --filter "label=com.docker.compose.project=$insights_project")"
existing="$existing$(docker network ls -q --filter "label=com.docker.compose.project=$insights_project")"
if [ -n "$existing" ]; then
  echo "refusing to reuse existing TAP Insights E2E resources" >&2
  exit 2
fi
python3 "$insights_script_dir/insights_runtime.py" initialize --compose-project "$insights_project"
insights_require_free_port "$MYSQL_PORT"
insights_require_free_port "$CLICKHOUSE_HTTP_PORT"
insights_compose_started=1
insights_compose up -d --wait --wait-timeout 180 mysql clickhouse
uv run --project apps/backend alembic -c apps/backend/alembic.ini upgrade head

insights_start_apps
insights_run_phase upload

insights_stop_apps
insights_start_apps
insights_run_phase app-restart

insights_stop_apps
insights_compose restart mysql clickhouse
insights_compose up -d --wait --wait-timeout 180 mysql clickhouse
insights_start_apps
insights_run_phase compose-restart

echo "TAP Insights owned JUnit upload, query, detail, and restart journey passed."
