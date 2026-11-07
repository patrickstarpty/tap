"""Launch-time identity for the supervisor-owned OpenCode serve process."""

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ServeIdentity:
    pid: int
    start: str
    is_live: Callable[[], bool] = field(repr=False, compare=False)


def capture_serve_identity(
    pid: int, is_live: Callable[[], bool]
) -> ServeIdentity | None:
    """Capture and bracket the launch-time start identity while our handle is live."""
    if pid < 1 or not is_live():
        return None
    deadline = time.monotonic() + 2

    def read() -> str | None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        result = subprocess.run(
            ("ps", "-ww", "-p", str(pid), "-o", "pid=,ppid=,pgid=,lstart="),
            capture_output=True,
            text=True,
            timeout=min(remaining, 1),
            check=False,
        )
        if result.returncode != 0 or len(result.stdout) > 4096 or result.stderr:
            return None
        lines = result.stdout.splitlines()
        return lines[0].strip() if len(lines) == 1 else None

    try:
        first = read()
        last = read()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if first is None or first != last or not is_live():
        return None
    fields = first.split()
    if len(fields) != 8 or not all(value.isdigit() for value in fields[:3]):
        return None
    if int(fields[0]) != pid:
        return None
    return ServeIdentity(pid, " ".join(fields[3:]), is_live)
