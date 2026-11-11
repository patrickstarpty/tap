"""Private immutable, bounded browser file objects."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import cast, Literal

from .contracts import Evidence
from .store import BrowserStore

_SHA = re.compile(r"^[0-9a-f]{64}$")
_SIGNATURES = {
    "screenshot": b"\x89PNG\r\n\x1a\n",
    "trace": b"PK\x03\x04",
    "result": b"{",
    "video": b"\x1a\x45\xdf\xa3",
}


class BrowserObjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def persist(
        self, data: bytes, *, kind: str, max_bytes: int
    ) -> tuple[str, str, int]:
        if kind not in {"script", "input", *_SIGNATURES}:
            raise ValueError("unsupported browser object")
        if not isinstance(data, bytes) or not data or len(data) > max_bytes:
            raise ValueError("invalid browser object size")
        if kind in _SIGNATURES and not data.startswith(_SIGNATURES[kind]):
            raise ValueError("invalid browser evidence signature")
        if kind == "script":
            data.decode("utf-8")
        if kind == "input":
            import json

            if not isinstance(json.loads(data), dict):
                raise ValueError("invalid browser input")
        sha = hashlib.sha256(data).hexdigest()
        ref = f"{sha[:2]}/{sha}"
        path = self.root / ref
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                descriptor, name = tempfile.mkstemp(prefix=".browser-", dir=path.parent)
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(name, path)
                    os.chmod(path, 0o600)
                    directory = os.open(path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                finally:
                    Path(name).unlink(missing_ok=True)
        return ref, sha, len(data)

    def read(self, ref: str, expected_sha: str, *, max_bytes: int) -> bytes:
        if (
            not _SHA.fullmatch(expected_sha)
            or ref != f"{expected_sha[:2]}/{expected_sha}"
        ):
            raise ValueError("invalid browser object reference")
        path = self.root / ref
        if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
            raise FileNotFoundError("browser object unavailable")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected_sha:
            raise ValueError("browser object hash mismatch")
        return data


class BrowserEvidenceSink:
    """Thread-safe runtime sink; terminal status belongs to the worker."""

    def __init__(self, store: BrowserStore, objects: BrowserObjectStore) -> None:
        self.store = store
        self.objects = objects

    def persist(
        self,
        project_id: str,
        task_id: str,
        execution_id: str,
        kind: str,
        data: bytes,
        max_bytes: int,
        *,
        session_id: str | None = None,
    ) -> Evidence:
        if kind == "screenshot" and execution_id == task_id and session_id is None:
            raise ValueError("inspector session authority unavailable")
        if session_id is not None:
            session = self.store.get("session", project_id, session_id)
            if (
                kind not in {"screenshot", "trace", "result", "video"}
                or execution_id != task_id
                or session is None
                or session["task_id"] != task_id
            ):
                raise ValueError("inspector session authority unavailable")
        ref, sha, size = self.objects.persist(data, kind=kind, max_bytes=max_bytes)
        row = self.store.append_evidence(
            dict(
                project_id=project_id,
                task_id=task_id,
                execution_id=None if session_id else execution_id,
                session_id=session_id,
                kind=kind,
                sha256=sha,
                object_ref=ref,
                size_bytes=size,
            )
        )
        return Evidence(
            kind=cast(Literal["screenshot", "trace", "result", "video"], kind),
            object_ref=row["id"],
            sha256=sha,
            size_bytes=size,
        )
