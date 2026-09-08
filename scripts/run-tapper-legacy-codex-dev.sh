#!/bin/bash
set -euo pipefail

export TAPPER_ANSWER_BACKEND=codex
exec uv run --project apps/backend python -m tap.entrypoints.legacy_loopback_answer_runtime
