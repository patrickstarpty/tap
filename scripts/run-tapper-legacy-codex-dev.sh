#!/bin/bash
set -euo pipefail

export TAPPER_ANSWER_BACKEND=codex
exec uv run --project apps/tap-ai-backend python -m tap.entrypoints.legacy_loopback_answer_runtime
