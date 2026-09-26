#!/usr/bin/env bash
set -euo pipefail

task14_script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
task14_repo_root="$(CDPATH= cd -- "$task14_script_dir/.." && pwd)"
task14_artifact_root="${TAP_TASK14_ARTIFACT_ROOT:-$task14_repo_root/.superpowers/artifacts/task-14}"
task14_matrix_started=0
task14_secrets=()
readonly task14_script_dir task14_repo_root

task14_list() {
  printf '%s\n' \
    tapper-fixture-journeys \
    insights-fixture-journey \
    tap-ai-fault-retention-matrix \
    tap-insights-fault-recovery-matrix \
    prototype-and-safe-handoff
}

if [ "${1:-}" = "--list" ]; then
  task14_list
  exit 0
fi

if [ "$#" -ne 0 ]; then
  echo "usage: $0 [--list]" >&2
  exit 2
fi

if [ "${TAP_RUN_TASK14_ACCEPTANCE:-}" != "1" ]; then
  echo "Task 14 joint acceptance requires TAP_RUN_TASK14_ACCEPTANCE=1" >&2
  exit 2
fi

case "$task14_artifact_root" in
  "$task14_repo_root"/.superpowers/artifacts/task-14 | \
  "$task14_repo_root"/.superpowers/artifacts/task-14/* | \
  "${TMPDIR:-/tmp}"/tap-task14.*) ;;
  *)
    echo "refusing unsafe Task 14 artifact path" >&2
    exit 2
    ;;
esac

mkdir -p "$task14_artifact_root"
task14_artifact_root="$(CDPATH= cd -- "$task14_artifact_root" && pwd -P)"
case "$task14_artifact_root" in
  "$task14_repo_root"/.superpowers/artifacts/task-14 | \
  "$task14_repo_root"/.superpowers/artifacts/task-14/* | \
  "${TMPDIR:-/tmp}"/tap-task14.*) ;;
  *)
    echo "refusing unsafe Task 14 artifact path" >&2
    exit 2
    ;;
esac

task14_random_hex() {
  od -An -N24 -tx1 /dev/urandom | tr -d ' \n'
}

task14_run_nonce="$(task14_random_hex)"
task14_project="tap-insights-task14-${task14_run_nonce:0:12}"
if [[ ! "$task14_project" =~ ^tap-insights-task14-[0-9a-f]{12}$ ]]; then
  echo "refusing unsafe Task 14 Compose project" >&2
  exit 2
fi

task14_mysql_root_password="$(task14_random_hex)"
task14_mysql_password="$(task14_random_hex)"
task14_clickhouse_admin_password="$(task14_random_hex)"
task14_clickhouse_writer_password="$(task14_random_hex)"
task14_clickhouse_reader_password="$(task14_random_hex)"
task14_report_access_token="$(task14_random_hex)"
task14_secrets+=(
  "$task14_mysql_root_password"
  "$task14_mysql_password"
  "$task14_clickhouse_admin_password"
  "$task14_clickhouse_writer_password"
  "$task14_clickhouse_reader_password"
  "$task14_report_access_token"
)
while IFS='=' read -r task14_env_name task14_env_value; do
  case "$task14_env_name" in
    *_PASSWORD | *_TOKEN | *_API_KEY | *_MASTER_KEY | *_CONNECTION_STRING)
      if [ "${#task14_env_value}" -ge 8 ]; then
        task14_secrets+=("$task14_env_value")
      fi
      ;;
  esac
done < <(env)

task14_receipt="$task14_artifact_root/acceptance-receipt.tsv"
task14_insights_journey_project="$task14_project-journey"
readonly task14_artifact_root task14_receipt task14_run_nonce task14_project \
  task14_insights_journey_project
export COMPOSE_PROJECT_NAME="$task14_project"
task14_sha="$(git -C "$task14_repo_root" rev-parse HEAD)"
printf 'schema\ttask14-acceptance-v2\ncode_sha\t%s\ncompose_project\t%s\n' \
  "$task14_sha" "$task14_project" >"$task14_receipt"

task14_redact() {
  local line secret
  while IFS= read -r line || [ -n "$line" ]; do
    for secret in "${task14_secrets[@]}"; do
      line="${line//"$secret"/[REDACTED]}"
    done
    printf '%s\n' "$line"
  done
}

task14_digest() {
  python3 -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$1"
}

task14_run_phase() {
  local phase="$1"
  shift
  local started finished elapsed digest
  started="$(date +%s)"
  printf 'Task 14 phase %s started.\n' "$phase"
  if "$@" 2>&1 | task14_redact | tee "$task14_artifact_root/$phase.log"; then
    finished="$(date +%s)"
    elapsed=$((finished - started))
    digest="$(task14_digest "$task14_artifact_root/$phase.log")"
    printf 'phase\t%s\tpass\t%s\t%s\n' "$phase" "$elapsed" "$digest" >>"$task14_receipt"
    printf 'Task 14 phase %s passed in %ss.\n' "$phase" "$elapsed"
    return 0
  fi
  finished="$(date +%s)"
  elapsed=$((finished - started))
  digest="$(task14_digest "$task14_artifact_root/$phase.log")"
  printf 'phase\t%s\tfail\t%s\t%s\n' "$phase" "$elapsed" "$digest" >>"$task14_receipt"
  printf 'Task 14 phase %s failed in %ss.\n' "$phase" "$elapsed" >&2
  return 1
}

task14_record_preserved_volumes() {
  local project="$1" volume
  case "$project" in
    "$task14_project" | "$task14_insights_journey_project") ;;
    *) echo "refusing to record unowned Task 14 volumes" >&2; return 2 ;;
  esac
  while IFS= read -r volume; do
    if [ -n "$volume" ]; then
      printf 'preserved_volume\t%s\t%s\n' "$project" "$volume" >>"$task14_receipt"
    fi
  done < <(
    docker volume ls -q \
      --filter "label=com.docker.compose.project=$project" 2>/dev/null || true
  )
}

task14_cleanup() {
  if [ "$task14_matrix_started" -eq 1 ]; then
    docker compose -f "$task14_repo_root/compose.yaml" -p "$task14_project" \
      --profile insights down --remove-orphans >/dev/null 2>&1 || true
    task14_record_preserved_volumes "$task14_project"
  fi
}
trap task14_cleanup EXIT INT TERM

task14_tap_ai_matrix() {
  TAP_RUN_MYSQL_INTEGRATION=1 UV_CACHE_DIR="${UV_CACHE_DIR:-/private/tmp/tap-task14-uv}" \
    uv run --project apps/tap-ai-backend pytest \
      apps/tap-ai-backend/tests/integration/test_graph_run_recovery.py \
      apps/tap-ai-backend/tests/integration/test_test_plan_generation.py \
      apps/tap-ai-backend/tests/integration/test_test_plan_publish.py \
      apps/tap-ai-backend/tests/integration/test_published_retrieval.py \
      apps/tap-ai-backend/tests/integration/test_knowledge_operations_recovery.py \
      apps/tap-ai-backend/tests/integration/test_knowledge_review_migration.py \
      apps/tap-ai-backend/tests/integration/test_upgrade_from_0005.py::test_0012a_legacy_conversation_is_readable_through_new_repository \
      apps/tap-ai-backend/tests/unit/operations/test_redis_stream_recovery.py \
      apps/tap-ai-backend/tests/unit/test_management/test_test_design_review_migration.py \
      -q
}

task14_insights_journey() {
  TAP_INSIGHTS_E2E_PROJECT="$task14_insights_journey_project" \
    TAP_INSIGHTS_E2E_PRESERVE_VOLUMES=1 \
    TAP_INSIGHTS_E2E_ARTIFACTS="$task14_artifact_root/insights-journey" \
    TAP_INSIGHTS_E2E_SCREENSHOTS="$task14_artifact_root/screenshots" \
    MYSQL_ROOT_PASSWORD="$task14_mysql_root_password" \
    MYSQL_PASSWORD="$task14_mysql_password" \
    CLICKHOUSE_ADMIN_PASSWORD="$task14_clickhouse_admin_password" \
    TAP_CLICKHOUSE_WRITER_PASSWORD="$task14_clickhouse_writer_password" \
    TAP_CLICKHOUSE_READER_PASSWORD="$task14_clickhouse_reader_password" \
    TAP_REPORT_ACCESS_TOKEN="$task14_report_access_token" \
    make --no-print-directory tap-insights-e2e
}

task14_insights_matrix() {
  local existing
  existing="$(docker ps -aq --filter "label=com.docker.compose.project=$task14_project")"
  existing="$existing$(docker volume ls -q --filter "label=com.docker.compose.project=$task14_project")"
  existing="$existing$(docker network ls -q --filter "label=com.docker.compose.project=$task14_project")"
  if [ -n "$existing" ]; then
    echo "refusing to reuse existing Task 14 Insights resources" >&2
    return 2
  fi

  export MYSQL_ROOT_PASSWORD="$task14_mysql_root_password"
  export MYSQL_DATABASE=tap_task10_task14
  export MYSQL_USER=tap
  export MYSQL_PASSWORD="$task14_mysql_password"
  export MYSQL_PORT=33331
  export CLICKHOUSE_HTTP_PORT=38131
  export CLICKHOUSE_ADMIN_USER=tap_insights_admin
  export CLICKHOUSE_ADMIN_PASSWORD="$task14_clickhouse_admin_password"
  export TAP_CLICKHOUSE_WRITER_USER=tap_insights_writer
  export TAP_CLICKHOUSE_WRITER_PASSWORD="$task14_clickhouse_writer_password"
  export TAP_CLICKHOUSE_READER_USER=tap_insights_reader
  export TAP_CLICKHOUSE_READER_PASSWORD="$task14_clickhouse_reader_password"
  export TAP_TASK9_MYSQL_URL="mysql+pymysql://tap:$MYSQL_PASSWORD@127.0.0.1:33331/tap_task10_task14?charset=utf8mb4"
  export TAP_TASK10_MYSQL_URL="$TAP_TASK9_MYSQL_URL"
  export TAP_TASK10_CLICKHOUSE_URL='http://127.0.0.1:38131/?database=tap_insights'
  export TAP_TASK10_CLICKHOUSE_USER="$TAP_CLICKHOUSE_WRITER_USER"
  export TAP_TASK10_CLICKHOUSE_PASSWORD="$TAP_CLICKHOUSE_WRITER_PASSWORD"
  export TAP_TASK10_CLICKHOUSE_ADMIN_USER="$CLICKHOUSE_ADMIN_USER"
  export TAP_TASK10_CLICKHOUSE_ADMIN_PASSWORD="$CLICKHOUSE_ADMIN_PASSWORD"
  export TAP_TASK10_COMPOSE_PROJECT="$task14_project"

  docker compose -f "$task14_repo_root/compose.yaml" -p "$task14_project" \
    --profile insights up -d --wait --wait-timeout 180 mysql clickhouse
  UV_CACHE_DIR="${UV_CACHE_DIR:-/private/tmp/tap-task14-uv}" \
    uv run --project apps/backend pytest \
      apps/backend/tests/integration/test_report_recovery.py \
      apps/backend/tests/integration/test_insights_projection.py \
      -q
}

task14_product_and_handoff() {
  UV_CACHE_DIR="${UV_CACHE_DIR:-/private/tmp/tap-task14-uv}" \
    uv run --project apps/tap-ai-backend pytest \
      apps/tap-ai-backend/tests/contract/test_insights_tool.py \
      apps/tap-ai-backend/tests/integration/test_insights_tap_http.py \
      -q
  corepack pnpm --dir apps/web exec vitest run \
    src/widgets/tap/TapProductPrototype.test.tsx \
    src/features/insights/components/RunDetails.test.tsx
}

cd "$task14_repo_root"
task14_run_phase tapper-fixture-journeys make --no-print-directory demo-e2e
task14_run_phase insights-fixture-journey task14_insights_journey
task14_record_preserved_volumes "$task14_insights_journey_project"
task14_run_phase tap-ai-fault-retention-matrix task14_tap_ai_matrix
task14_matrix_started=1
task14_run_phase tap-insights-fault-recovery-matrix task14_insights_matrix
task14_run_phase prototype-and-safe-handoff task14_product_and_handoff

printf 'Task 14 isolated joint acceptance passed; receipt: %s\n' "$task14_receipt"
