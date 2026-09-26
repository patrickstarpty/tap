"""Credential-free, content-addressed report object storage adapter."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from collections.abc import Callable, Iterable, Iterator, Set
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import fcntl


class ObjectTooLarge(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StoredObject:
    ref: str
    checksum: str
    size_bytes: int


class FileReportObjectStore:
    """An owned filesystem store suitable for an isolated TAP deployment."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._staging = self._root / ".staging"
        self._raw = self._root / "raw"
        self._lock_path = self._root / ".intake-recovery.lock"
        self._process_lock = threading.RLock()
        self._lock_state = threading.local()
        self._staging.mkdir(parents=True, exist_ok=True)
        self._raw.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    @contextmanager
    def intake_guard(self) -> Iterator[None]:
        """Serialize raw persistence plus ledger commit against recovery."""
        with self._exclusive():
            yield

    def persist(self, chunks: Iterable[bytes], *, max_bytes: int) -> StoredObject:
        with self._exclusive():
            return self._persist_locked(chunks, max_bytes=max_bytes)

    def _persist_locked(
        self, chunks: Iterable[bytes], *, max_bytes: int
    ) -> StoredObject:
        digest = hashlib.sha256()
        size = 0
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._staging, prefix="upload-"
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        raise TypeError("report chunks must be bytes")
                    size += len(chunk)
                    if size > max_bytes:
                        raise ObjectTooLarge(
                            f"report exceeds the {max_bytes}-byte upload limit"
                        )
                    digest.update(chunk)
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            checksum = digest.hexdigest()
            relative = Path("raw") / checksum[:2] / f"{checksum}.xml"
            destination = self._root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                temporary.unlink()
            else:
                os.replace(temporary, destination)
            self._fsync_directory(destination.parent)
            self._fsync_directory(self._raw)
            return StoredObject(relative.as_posix(), checksum, size)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def read(self, ref: str) -> bytes:
        return self._resolve(ref).read_bytes()

    def list_raw_objects(self) -> list[str]:
        return sorted(
            path.relative_to(self._root).as_posix()
            for path in self._raw.rglob("*.xml")
            if path.is_file()
        )

    def recover_orphans(
        self, *, reference_supplier: Callable[[], Set[str]], older_than: datetime
    ) -> list[str]:
        if older_than.utcoffset() is None:
            raise ValueError("older_than must be timezone-aware")
        with self._exclusive():
            referenced = reference_supplier()
            cutoff = older_than.timestamp()
            removed: list[str] = []
            for temporary in self._staging.iterdir():
                if temporary.is_file() and temporary.stat().st_mtime < cutoff:
                    temporary.unlink()
                    removed.append(f".staging/{temporary.name}")
            for ref in self.list_raw_objects():
                candidate = self._resolve(ref)
                if ref not in referenced and candidate.stat().st_mtime < cutoff:
                    candidate.unlink()
                    self._fsync_directory(candidate.parent)
                    removed.append(ref)
            return sorted(removed)

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        with self._process_lock:
            depth = getattr(self._lock_state, "depth", 0)
            if depth == 0:
                descriptor = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                except BaseException:
                    os.close(descriptor)
                    raise
                self._lock_state.descriptor = descriptor
            self._lock_state.depth = depth + 1
            try:
                yield
            finally:
                self._lock_state.depth -= 1
                if self._lock_state.depth == 0:
                    descriptor = self._lock_state.descriptor
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                    os.close(descriptor)
                    del self._lock_state.descriptor

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _resolve(self, ref: str) -> Path:
        candidate = (self._root / ref).resolve()
        if self._root not in candidate.parents or not ref.startswith("raw/"):
            raise ValueError("invalid report object reference")
        return candidate
