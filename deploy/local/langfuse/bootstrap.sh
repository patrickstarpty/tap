#!/usr/bin/env bash
# Idempotent local bootstrap for the langfuse profile:
#   1. create a langfuse-only ClickHouse database and user on the insights
#      ClickHouse (never grants access to tap_insights.*);
#   2. create the langfuse MinIO bucket on tap-minio via a one-off mc container.
# Run via `make langfuse-up` after the insights ClickHouse is healthy.

set -euo pipefail

CLICKHOUSE_ADMIN_USER="${CLICKHOUSE_ADMIN_USER:-tap_insights_admin}"
CLICKHOUSE_ADMIN_PASSWORD="${CLICKHOUSE_ADMIN_PASSWORD:-tap-local-insights-admin}"
LANGFUSE_CLICKHOUSE_USER="${LANGFUSE_CLICKHOUSE_USER:-tap_langfuse}"
LANGFUSE_CLICKHOUSE_PASSWORD="${LANGFUSE_CLICKHOUSE_PASSWORD:-tap-local-langfuse}"
TAPPER_S3_ACCESS_KEY="${TAPPER_S3_ACCESS_KEY:-tap-local-object-key}"
TAPPER_S3_SECRET_KEY="${TAPPER_S3_SECRET_KEY:-tap-local-object-password}"
LANGFUSE_MC_IMAGE="${LANGFUSE_MC_IMAGE:-quay.io/minio/mc:RELEASE.2025-02-04T04-57-50Z}"
LANGFUSE_S3_BUCKET="langfuse"

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

echo "bootstrap: creating ClickHouse database/user for langfuse (idempotent)"
docker compose exec -T clickhouse clickhouse-client \
  --user "$CLICKHOUSE_ADMIN_USER" \
  --password "$CLICKHOUSE_ADMIN_PASSWORD" \
  --param_langfuse_password "$LANGFUSE_CLICKHOUSE_PASSWORD" \
  --multiquery <<SQL
CREATE DATABASE IF NOT EXISTS langfuse;
CREATE USER IF NOT EXISTS "$LANGFUSE_CLICKHOUSE_USER"
IDENTIFIED WITH sha256_password BY {langfuse_password:String};
GRANT ALL ON langfuse.* TO "$LANGFUSE_CLICKHOUSE_USER";
SQL

echo "bootstrap: resolving compose network for tap-minio"
minio_container="$(docker compose ps -q tap-minio)"
if [[ -z "$minio_container" ]]; then
  echo "tap-minio is not running; start it before bootstrapping langfuse" >&2
  exit 1
fi
minio_network="$(docker inspect "$minio_container" --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}' | awk '{print $1}')"
if [[ -z "$minio_network" ]]; then
  echo "could not resolve the compose network for tap-minio" >&2
  exit 1
fi

echo "bootstrap: creating langfuse MinIO bucket (idempotent)"
docker run --rm \
  --network "$minio_network" \
  -e "MC_HOST_langfuselocal=http://${TAPPER_S3_ACCESS_KEY}:${TAPPER_S3_SECRET_KEY}@tap-minio:9000" \
  "$LANGFUSE_MC_IMAGE" \
  mb --ignore-existing "langfuselocal/${LANGFUSE_S3_BUCKET}"

echo "bootstrap: done"
