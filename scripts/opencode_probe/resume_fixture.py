"""Supervisor-owned synthetic waiter for the interruption probe."""

import hashlib
import os
from pathlib import Path


_WAITER = b'''"""Bounded synthetic interruption fixture."""

import os
import time
from pathlib import Path


marker = os.open("first-action.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
try:
    os.write(marker, b"start\\n")
    os.fsync(marker)
finally:
    os.close(marker)
Path("first.txt").write_text("first")
Path("owned.pid").write_text(str(os.getpid()))
time.sleep(60)
Path("second.txt").write_text("second")
'''


def prepare_resume_fixture(workspace: Path) -> str:
    """Write an immutable task-local waiter once and return its source digest."""
    path = workspace / "waiter.py"
    digest = hashlib.sha256(_WAITER).hexdigest()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.is_symlink() or not path.is_file() or path.read_bytes() != _WAITER:
            raise ValueError("resume fixture differs from supervisor version") from None
        return digest
    with os.fdopen(fd, "wb") as stream:
        stream.write(_WAITER)
        stream.flush()
        os.fsync(stream.fileno())
    return digest
