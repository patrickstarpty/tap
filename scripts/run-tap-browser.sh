#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" != api ] && [ "${1:-}" != worker ]; then
  echo 'usage: run-tap-browser.sh api|worker' >&2
  exit 2
fi
for name in TAP_BROWSER_DATABASE_URL TAP_BROWSER_OBJECT_ROOT TAP_BROWSER_PROJECT_ID TAP_BROWSER_ACCESS_TOKEN TAP_BROWSER_TOKEN_EXPIRES_AT; do
  if [ -z "${!name:-}" ]; then echo "$name is required" >&2; exit 2; fi
done
if [ "${TAP_BROWSER_EXECUTION_MODE:-docker}" = docker ] && [ -z "${TAP_BROWSER_IMAGE:-}" ]; then
  echo 'TAP_BROWSER_IMAGE is required for Docker execution' >&2; exit 2
fi
repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$repo_root"
selected=("PATH=$PATH" "HOME=$HOME" "TAP_BROWSER_DATABASE_URL=$TAP_BROWSER_DATABASE_URL" "TAP_BROWSER_OBJECT_ROOT=$TAP_BROWSER_OBJECT_ROOT" "TAP_BROWSER_PROJECT_ID=$TAP_BROWSER_PROJECT_ID" "TAP_BROWSER_ACCESS_TOKEN=$TAP_BROWSER_ACCESS_TOKEN" "TAP_BROWSER_TOKEN_EXPIRES_AT=$TAP_BROWSER_TOKEN_EXPIRES_AT" "TAP_BROWSER_IMAGE=${TAP_BROWSER_IMAGE:-}" "TAP_BROWSER_WORKER_POLL_SECONDS=${TAP_BROWSER_WORKER_POLL_SECONDS:-0.2}")
# Explicit supervisor-only allowlist. Never inherit provider/model keys.
for name in TAP_BROWSER_GATEWAY_TOKEN TAP_BROWSER_WORKER_TOKEN TAP_BROWSER_EXECUTION_MODE TAP_BROWSER_LOCAL_UPSTREAM_PROXY; do
  if [ -n "${!name:-}" ]; then selected+=("$name=${!name}"); fi
done
if [ "$1" = api ]; then
  api_module=tap_platform.browser_automation.http:app
  if [ "${TAP_LCA_ENABLED:-false}" = true ]; then
    if [ -z "${TAP_LCA_GATEWAY_TOKEN:-}" ]; then echo 'TAP_LCA_GATEWAY_TOKEN is required' >&2; exit 2; fi
    selected+=("TAP_LCA_GATEWAY_TOKEN=$TAP_LCA_GATEWAY_TOKEN")
    api_module=tap_platform.app:app
    # Do not silently create/alter user schema. Fail before serving if migrations are missing.
    env -i "${selected[@]}" "$repo_root/.venv/bin/python" -c 'import os; from sqlalchemy import create_engine,inspect; e=create_engine(os.environ["TAP_BROWSER_DATABASE_URL"]); t=set(inspect(e).get_table_names()); assert {"tap_lca_releases", "tap_lca_assets"} <= t, "Run TAP migrations through 0012_lca_assets before enabling LCA"' >/dev/null
  fi
  api_port="$("$repo_root/.venv/bin/python" -c 'import sys; from urllib.parse import urlsplit; p=urlsplit(sys.argv[1]); assert p.scheme=="http" and p.hostname=="127.0.0.1" and p.port and not (p.username or p.password or p.query or p.fragment) and p.path in ("","/"); print(p.port)' "${TAP_BROWSER_API_ORIGIN:-http://127.0.0.1:8002}")"
  exec env -i "${selected[@]}" "$repo_root/.venv/bin/uvicorn" "$api_module" --host 127.0.0.1 --port "$api_port"
fi
exec env -i "${selected[@]}" "$repo_root/.venv/bin/python" -m tap_platform.browser_automation.worker
