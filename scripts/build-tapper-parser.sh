#!/bin/bash
set -euo pipefail
parser_build_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec python3 "$parser_build_dir/parser_build.py" "$parser_build_dir/.." "$@"
