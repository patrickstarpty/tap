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
        self._lock = threading.RLock()
        self._sessions: dict[tuple[str, str], str] = {}

    def bind_session(self, project_id: str, task_id: str, session_id: str) -> None:
        session = self.store.get("session", project_id, session_id)
        if session is None or session["task_id"] != task_id:
            raise ValueError("browser session authority unavailable")
        with self._lock:
            self._sessions[(project_id, task_id)] = session_id

    def unbind_session(self, project_id: str, task_id: str, session_id: str) -> None:
        with self._lock:
            if self._sessions.get((project_id, task_id)) == session_id:
                self._sessions.pop((project_id, task_id), None)

    def persist(
        self,
        project_id: str,
        task_id: str,
        execution_id: str,
        kind: str,
        data: bytes,
        max_bytes: int,
    ) -> Evidence:
        with self._lock:
            session_id = (
                self._sessions.get((project_id, task_id))
                if kind == "screenshot" and execution_id == task_id
                else None
            )
        if kind == "screenshot" and execution_id == task_id and session_id is None:
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
            kind=cast(Literal["screenshot", "trace", "result"], kind),
            object_ref=row["id"],
            sha256=sha,
            size_bytes=size,
        )
