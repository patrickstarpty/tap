#!/usr/bin/env bash

set -euo pipefail

: "${CLICKHOUSE_USER:?CLICKHOUSE_USER is required}"
: "${CLICKHOUSE_PASSWORD:?CLICKHOUSE_PASSWORD is required}"
: "${TAP_CLICKHOUSE_WRITER_USER:?TAP_CLICKHOUSE_WRITER_USER is required}"
: "${TAP_CLICKHOUSE_WRITER_PASSWORD:?TAP_CLICKHOUSE_WRITER_PASSWORD is required}"
: "${TAP_CLICKHOUSE_READER_USER:?TAP_CLICKHOUSE_READER_USER is required}"
: "${TAP_CLICKHOUSE_READER_PASSWORD:?TAP_CLICKHOUSE_READER_PASSWORD is required}"

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

validate_user "$CLICKHOUSE_USER"
validate_user "$TAP_CLICKHOUSE_WRITER_USER"
validate_user "$TAP_CLICKHOUSE_READER_USER"
if [[ "$CLICKHOUSE_USER" == "$TAP_CLICKHOUSE_WRITER_USER" \
  || "$CLICKHOUSE_USER" == "$TAP_CLICKHOUSE_READER_USER" \
  || "$TAP_CLICKHOUSE_WRITER_USER" == "$TAP_CLICKHOUSE_READER_USER" ]]; then
  echo "ClickHouse admin, writer, and reader users must be distinct" >&2
  exit 2
fi

clickhouse-client \
  --user "$CLICKHOUSE_USER" \
  --password "$CLICKHOUSE_PASSWORD" \
  --param_writer_password "$TAP_CLICKHOUSE_WRITER_PASSWORD" \
  --param_reader_password "$TAP_CLICKHOUSE_READER_PASSWORD" \
  --multiquery <<SQL
CREATE USER IF NOT EXISTS "$TAP_CLICKHOUSE_WRITER_USER"
IDENTIFIED WITH sha256_password BY {writer_password:String};
CREATE USER IF NOT EXISTS "$TAP_CLICKHOUSE_READER_USER"
IDENTIFIED WITH sha256_password BY {reader_password:String};
GRANT SELECT, INSERT ON tap_insights.* TO "$TAP_CLICKHOUSE_WRITER_USER";
GRANT SELECT ON tap_insights.* TO "$TAP_CLICKHOUSE_READER_USER";
SQL
