#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" != api ] && [ "${1:-}" != worker ]; then
  echo 'usage: run-tap-browser.sh api|worker' >&2
  exit 2
fi
for name in TAP_BROWSER_DATABASE_URL TAP_BROWSER_OBJECT_ROOT TAP_BROWSER_PROJECT_ID TAP_BROWSER_ACCESS_TOKEN TAP_BROWSER_TOKEN_EXPIRES_AT TAP_BROWSER_IMAGE; do
  if [ -z "${!name:-}" ]; then echo "$name is required" >&2; exit 2; fi
done
repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$repo_root"
selected=("PATH=$PATH" "HOME=$HOME" "TAP_BROWSER_DATABASE_URL=$TAP_BROWSER_DATABASE_URL" "TAP_BROWSER_OBJECT_ROOT=$TAP_BROWSER_OBJECT_ROOT" "TAP_BROWSER_PROJECT_ID=$TAP_BROWSER_PROJECT_ID" "TAP_BROWSER_ACCESS_TOKEN=$TAP_BROWSER_ACCESS_TOKEN" "TAP_BROWSER_TOKEN_EXPIRES_AT=$TAP_BROWSER_TOKEN_EXPIRES_AT" "TAP_BROWSER_IMAGE=$TAP_BROWSER_IMAGE" "TAP_BROWSER_WORKER_POLL_SECONDS=${TAP_BROWSER_WORKER_POLL_SECONDS:-0.2}")
if [ "$1" = api ]; then
  exec env -i "${selected[@]}" "$repo_root/.venv/bin/uvicorn" tap_platform.browser_automation.http:app --host 127.0.0.1 --port 8002
fi
exec env -i "${selected[@]}" "$repo_root/.venv/bin/python" -m tap_platform.browser_automation.worker
