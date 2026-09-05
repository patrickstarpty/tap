from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

_RETIRED = bytes((65, 116, 104, 101, 110, 97))


@dataclass(frozen=True, slots=True)
class BrandViolation:
    kind: Literal["path", "content"]
    path: str


def scan_tracked_files(root: Path, paths: Sequence[str]) -> tuple[BrandViolation, ...]:
    violations: list[BrandViolation] = []
    needle = _RETIRED.lower()
    for relative in sorted(paths):
        if needle in relative.encode("utf-8").lower():
            violations.append(BrandViolation("path", relative))
        candidate = root / relative
        if candidate.is_file() and needle in candidate.read_bytes().lower():
            violations.append(BrandViolation("content", relative))
    return tuple(violations)


def tracked_files(root: Path) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return tuple(item.decode("utf-8") for item in result.stdout.split(b"\0") if item)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        violations = scan_tracked_files(root, tracked_files(root))
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"brand namespace check failed: {error}", file=sys.stderr)
        return 2
    for violation in violations:
        print(f"{violation.kind}: {violation.path}")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
