#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo_root"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

MYSQL_PORT=${MYSQL_PORT:-3306}
MYSQL_DATABASE=${MYSQL_DATABASE:-tap}
MYSQL_USER=${MYSQL_USER:-tap}
MYSQL_PASSWORD=${MYSQL_PASSWORD:-tap}
MYSQL_IMAGE=${MYSQL_IMAGE:-mysql:8.4.6}

REDIS_PORT=${REDIS_PORT:-6379}
REDIS_IMAGE=${REDIS_IMAGE:-redis:7.4.7}

AZURITE_BLOB_PORT=${AZURITE_BLOB_PORT:-10000}
LITELLM_PORT=${LITELLM_PORT:-4000}
DOCKER_CHECK_TIMEOUT_SECONDS=${DOCKER_CHECK_TIMEOUT_SECONDS:-30}

failed_services=()

run_with_timeout() {
  local timeout_seconds=$1
  shift

  python3 - "$timeout_seconds" "$@" <<'PY'
import subprocess
import sys

timeout_seconds = int(sys.argv[1])
command = sys.argv[2:]

try:
    completed = subprocess.run(command, check=False, timeout=timeout_seconds)
except subprocess.TimeoutExpired:
    print(
        f"Timed out after {timeout_seconds}s: {' '.join(command)}",
        file=sys.stderr,
    )
    sys.exit(124)

sys.exit(completed.returncode)
PY
}

run_or_capture() {
  local service_name=$1
  shift
  local output

  if output=$("$@" 2>&1); then
    printf '[ok] %s\n' "$service_name"
    if [[ -n "$output" ]]; then
      printf '%s\n' "$output"
    fi
    return 0
  fi

  failed_services+=("$service_name")
  printf '[fail] %s\n' "$service_name" >&2
  printf '%s\n' "$output" >&2
  return 1
}

check_mysql() {
  run_with_timeout "$DOCKER_CHECK_TIMEOUT_SECONDS" docker run --rm "$MYSQL_IMAGE" sh -lc "
    MYSQL_PWD='$MYSQL_PASSWORD' mysqladmin ping \
      --connect-timeout=5 \
      --host=host.docker.internal \
      --port='$MYSQL_PORT' \
      --protocol=tcp \
      --user='$MYSQL_USER'
    MYSQL_PWD='$MYSQL_PASSWORD' mysql \
      --batch \
      --connect-timeout=5 \
      --host=host.docker.internal \
      --port='$MYSQL_PORT' \
      --protocol=tcp \
      --user='$MYSQL_USER' \
      '$MYSQL_DATABASE' \
      --execute='SELECT 1' | tail -n +2 | grep -qx '1'
  "
}

check_redis() {
  run_with_timeout "$DOCKER_CHECK_TIMEOUT_SECONDS" docker run --rm "$REDIS_IMAGE" redis-cli \
    --raw \
    -h host.docker.internal \
    -p "$REDIS_PORT" \
    ping | grep -qx 'PONG'
}

check_azurite() {
  local headers
  headers=$(mktemp)
  trap 'rm -f "$headers"' RETURN

  curl \
    --silent \
    --show-error \
    --max-time 5 \
    --dump-header "$headers" \
    --output /dev/null \
    "http://127.0.0.1:${AZURITE_BLOB_PORT}/devstoreaccount1?comp=list&maxresults=1"

  grep -Eiq '^server: .*azurite-blob' "$headers" || {
    printf 'Expected Azurite Blob server header in response from port %s\n' "$AZURITE_BLOB_PORT" >&2
    return 1
  }
}

check_litellm() {
  curl \
    --fail \
    --silent \
    --show-error \
    --max-time 5 \
    "http://127.0.0.1:${LITELLM_PORT}/health/readiness" \
    > /dev/null
}

run_or_capture "mysql" check_mysql || true
run_or_capture "redis" check_redis || true
run_or_capture "azurite" check_azurite || true
run_or_capture "litellm" check_litellm || true

if ((${#failed_services[@]} > 0)); then
  printf 'Unavailable services: %s\n' "${failed_services[*]}" >&2
  exit 1
fi

printf 'All local middleware services are reachable.\n'
