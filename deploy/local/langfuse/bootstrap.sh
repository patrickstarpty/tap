#!/usr/bin/env bash
# Idempotent local bootstrap for the langfuse profile:
#   1. create a langfuse-only ClickHouse database and user on the insights
#      ClickHouse, which lives in its own Compose project (never grants
#      access to tap_insights.*);
#   2. create the langfuse MinIO bucket on tap-minio, which lives in the
#      Tapper demo Compose project, via a one-off mc container.
# Run via `make langfuse-up` after the insights ClickHouse is healthy and the
# Tapper demo's redis/tap-minio are running.

set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
repo_root="$(CDPATH= cd -- "$script_dir/../../.." && pwd)"

if [[ -f "$repo_root/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$repo_root/.env"
  set +a
fi

TAP_TAPPER_COMPOSE_PROJECT="${TAP_TAPPER_COMPOSE_PROJECT:-tap-tapper-demo}"
TAP_INSIGHTS_COMPOSE_PROJECT="${TAP_INSIGHTS_COMPOSE_PROJECT:-tap-insights-local}"
CLICKHOUSE_ADMIN_USER="${CLICKHOUSE_ADMIN_USER:-tap_insights_admin}"
CLICKHOUSE_ADMIN_PASSWORD="${CLICKHOUSE_ADMIN_PASSWORD:-tap-local-insights-admin}"
LANGFUSE_CLICKHOUSE_USER="${LANGFUSE_CLICKHOUSE_USER:-tap_langfuse}"
LANGFUSE_CLICKHOUSE_PASSWORD="${LANGFUSE_CLICKHOUSE_PASSWORD:-tap-local-langfuse}"
TAPPER_S3_ACCESS_KEY="${TAPPER_S3_ACCESS_KEY:-tap-object-user}"
TAPPER_S3_SECRET_KEY="${TAPPER_S3_SECRET_KEY:-tap-local-object-password}"
LANGFUSE_MC_IMAGE="${LANGFUSE_MC_IMAGE:-quay.io/minio/mc:RELEASE.2025-02-04T04-57-50Z}"
LANGFUSE_S3_BUCKET="langfuse"

compose_file="$repo_root/compose.yaml"

validate_user() {
  local user="$1"
  case "$user" in
    ''|*[!a-z0-9_]*) echo "invalid ClickHouse application user" >&2; exit 2 ;;
  esac
  if (( ${#user} > 64 )); then
    echo "ClickHouse application user is too long" >&2
    exit 2
  fi
}

validate_user "$CLICKHOUSE_ADMIN_USER"
validate_user "$LANGFUSE_CLICKHOUSE_USER"
if [[ "$CLICKHOUSE_ADMIN_USER" == "$LANGFUSE_CLICKHOUSE_USER" ]]; then
  echo "ClickHouse admin and langfuse users must be distinct" >&2
  exit 2
fi

echo "bootstrap: creating ClickHouse database/user for langfuse in Compose project $TAP_INSIGHTS_COMPOSE_PROJECT (idempotent)"
docker compose -f "$compose_file" -p "$TAP_INSIGHTS_COMPOSE_PROJECT" exec -T clickhouse clickhouse-client \
  --user "$CLICKHOUSE_ADMIN_USER" \
  --password "$CLICKHOUSE_ADMIN_PASSWORD" \
  --param_langfuse_password "$LANGFUSE_CLICKHOUSE_PASSWORD" \
  --multiquery <<SQL
CREATE DATABASE IF NOT EXISTS langfuse;
CREATE USER IF NOT EXISTS "$LANGFUSE_CLICKHOUSE_USER"
IDENTIFIED WITH sha256_password BY {langfuse_password:String};
GRANT ALL ON langfuse.* TO "$LANGFUSE_CLICKHOUSE_USER";
SQL

echo "bootstrap: creating langfuse MinIO bucket in Compose project $TAP_TAPPER_COMPOSE_PROJECT (idempotent)"
if [[ -z "$(docker compose -f "$compose_file" -p "$TAP_TAPPER_COMPOSE_PROJECT" ps -q tap-minio)" ]]; then
  echo "tap-minio is not running in Compose project $TAP_TAPPER_COMPOSE_PROJECT; run make demo-up first" >&2
  exit 1
fi
# tap-minio's image (deploy/minio/Dockerfile) ships only /minio and
# /minio-health, no mc client, so the bucket is created from a one-off,
# pinned minio/mc container on the Tapper demo project's own default
# network (the network docker compose always creates for that project).
docker run --rm \
  --network "${TAP_TAPPER_COMPOSE_PROJECT}_default" \
  -e "MC_HOST_langfuselocal=http://${TAPPER_S3_ACCESS_KEY}:${TAPPER_S3_SECRET_KEY}@tap-minio:9000" \
  "$LANGFUSE_MC_IMAGE" \
  mb --ignore-existing "langfuselocal/${LANGFUSE_S3_BUCKET}"

echo "bootstrap: done"
