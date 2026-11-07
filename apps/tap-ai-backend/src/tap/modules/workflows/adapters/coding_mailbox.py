"""Private task mailbox shared with a network-none coding container."""

from __future__ import annotations

import json
import os
import re
import stat
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_MAX_REQUEST = 196_608
_MAX_RESPONSE = 1_400_000


class MailboxViolation(ValueError):
    pass


@dataclass(frozen=True)
class MailItem:
    identifier: str
    kind: str
    deadline_at: float
    payload: dict[str, Any]


class CodingMailbox:
    def __init__(self, root: Path) -> None:
        if root.is_symlink() or not root.is_dir() or root.stat().st_mode & 0o077:
            raise MailboxViolation("mailbox root must be a private real directory")
        for name in ("requests", "responses", "acks"):
            child = root / name
            if child.is_symlink() or not child.is_dir() or child.stat().st_mode & 0o077:
                raise MailboxViolation("mailbox child must be a private real directory")
        self.root = root
        self._seen: set[str] = set()

    @classmethod
    def create(cls, root: Path) -> CodingMailbox:
        if root.exists() or root.is_symlink():
            raise MailboxViolation("mailbox root already exists")
        root.mkdir(mode=0o700)
        (root / "requests").mkdir(mode=0o700)
        (root / "responses").mkdir(mode=0o700)
        (root / "acks").mkdir(mode=0o700)
        return cls(root)

    def _drain_acks(self) -> None:
        for ack in sorted((self.root / "acks").iterdir()):
            if ack.is_symlink() or not ack.is_file() or ack.stat().st_mode & 0o077:
                raise MailboxViolation("mailbox ack is not private regular file")
            if ack.suffix != ".json" or not _ID.fullmatch(ack.stem):
                raise MailboxViolation("mailbox invalid ack identity")
            fd = os.open(ack, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode) or os.read(fd, 81) != ack.stem.encode():
                    raise MailboxViolation("mailbox ack identity mismatch")
            finally:
                os.close(fd)
            (self.root / "responses" / ack.name).unlink(missing_ok=True)
            ack.unlink()

    @staticmethod
    def _check_paths(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"path", "file", "filename"} and isinstance(child, str):
                    if child.startswith("/") or ".." in Path(child).parts:
                        raise MailboxViolation("mailbox path escape")
                CodingMailbox._check_paths(child)
        elif isinstance(value, list):
            for child in value:
                CodingMailbox._check_paths(child)

    def _read(self, path: Path) -> MailItem:
        if path.is_symlink():
            raise MailboxViolation("mailbox symlink request")
        if not path.is_file() or path.stat().st_mode & 0o077:
            raise MailboxViolation("mailbox request is not private regular file")
        if not path.name.endswith(".json") or not _ID.fullmatch(path.stem):
            raise MailboxViolation("mailbox invalid request identity")
        if path.stem in self._seen or (self.root / "responses" / path.name).exists():
            raise MailboxViolation("mailbox duplicate request identity")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise MailboxViolation("mailbox request is not regular")
            raw = os.read(fd, _MAX_REQUEST + 1)
        finally:
            os.close(fd)
        if len(raw) > _MAX_REQUEST:
            raise MailboxViolation("mailbox request size exceeded")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as error:
            raise MailboxViolation("mailbox invalid JSON") from error
        if not isinstance(value, dict) or set(value) != {"id", "kind", "deadline_at", "payload"}:
            raise MailboxViolation("mailbox invalid envelope")
        if value["id"] != path.stem or value["kind"] not in {"model", "browser"}:
            raise MailboxViolation("mailbox identity mismatch")
        if (
            not isinstance(value["deadline_at"], (int, float))
            or value["deadline_at"] <= time.time()
        ):
            raise MailboxViolation("mailbox request expired")
        if not isinstance(value["payload"], dict):
            raise MailboxViolation("mailbox invalid payload")
        self._check_paths(value["payload"])
        return MailItem(path.stem, value["kind"], float(value["deadline_at"]), value["payload"])

    async def serve_once(self, handler: Callable[[MailItem], Awaitable[dict[str, Any]]]) -> bool:
        self._drain_acks()
        requests = self.root / "requests"
        for path in sorted(requests.iterdir()):
            item = self._read(path)
            self._seen.add(item.identifier)
            try:
                response = await handler(item)
                raw = json.dumps(response, separators=(",", ":"), ensure_ascii=True).encode()
                if len(raw) > _MAX_RESPONSE:
                    raise MailboxViolation("mailbox response size exceeded")
                destination = self.root / "responses" / path.name
                staged = self.root / f"{item.identifier}.response.tmp"
                fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                try:
                    os.write(fd, raw)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                try:
                    os.link(staged, destination, follow_symlinks=False)
                finally:
                    staged.unlink(missing_ok=True)
            finally:
                path.unlink(missing_ok=True)
            return True
        return False

    def close(self) -> None:
        for directory in (self.root / "requests", self.root / "responses", self.root / "acks"):
            for path in directory.iterdir():
                path.unlink()
            directory.rmdir()
        self.root.rmdir()
