"""Deterministically export the standalone TAP OpenAPI contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_SOURCE = REPOSITORY_ROOT / "apps" / "backend" / "src"

if str(BACKEND_SOURCE) not in sys.path:
    sys.path.insert(0, str(BACKEND_SOURCE))

from tap_platform.app import create_app  # noqa: E402


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def generated_contract() -> bytes:
    return canonical_json(create_app().openapi())


def write_or_check(output_directory: Path, *, check: bool) -> int:
    relative_path = Path("openapi/tap-api.json")
    destination = output_directory / relative_path
    expected = generated_contract()
    if check:
        if not destination.is_file() or destination.read_bytes() != expected:
            print("TAP contract artifact is out of date:", file=sys.stderr)
            print(f"  {relative_path}", file=sys.stderr)
            return 1
        return 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(expected)
    return 0


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPOSITORY_ROOT / "contracts",
        help="directory containing the TAP openapi artifact",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when the destination differs from deterministic output",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    return write_or_check(arguments.output_dir, check=arguments.check)


if __name__ == "__main__":
    raise SystemExit(main())
