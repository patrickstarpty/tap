#!/bin/bash
set -euo pipefail
umask 077
[ "$#" -eq 0 ] || { echo 'V0 gate accepts no arguments.' >&2; exit 2; }
tapper_gate_root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$tapper_gate_root"
export UV_NO_SYNC=1
exec uv run --project apps/backend python scripts/tapper_v0_gate.py run
