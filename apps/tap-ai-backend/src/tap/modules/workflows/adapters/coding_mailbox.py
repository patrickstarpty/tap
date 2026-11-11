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
_MAX_REQUEST = 12 * 1024 * 1024
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
        self._fds: dict[str, int] = {}
        try:
            self._fds["root"] = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for name in ("requests", "responses", "acks"):
                self._fds[name] = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=self._fds["root"],
                )
            self._assert_dirs()
        except (OSError, MailboxViolation) as error:
            for fd in self._fds.values():
                os.close(fd)
            raise MailboxViolation("mailbox requires private real directories") from error
        self.root = root
        self._seen: set[str] = set()

    def _assert_dirs(self) -> None:
        for name, fd in self._fds.items():
            opened = os.fstat(fd)
            if not stat.S_ISDIR(opened.st_mode) or opened.st_mode & 0o077:
                raise MailboxViolation("mailbox directory is not private")
            if name == "root":
                continue
            current = os.stat(name, dir_fd=self._fds["root"], follow_symlinks=False)
            if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
                opened.st_dev,
                opened.st_ino,
            ):
                raise MailboxViolation("mailbox child directory replaced")

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
        self._assert_dirs()
        for name in sorted(os.listdir(self._fds["acks"])):
            staged = Path(name)
            if staged.suffix == ".tmp" and _ID.fullmatch(staged.stem):
                continue
            info = os.stat(name, dir_fd=self._fds["acks"], follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise MailboxViolation("mailbox ack is not private regular file")
            ack = Path(name)
            if ack.suffix != ".json" or not _ID.fullmatch(ack.stem):
                raise MailboxViolation("mailbox invalid ack identity")
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self._fds["acks"])
            try:
                opened = os.fstat(fd)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or opened.st_mode & 0o077
                    or os.read(fd, 81) != ack.stem.encode()
                ):
                    raise MailboxViolation("mailbox ack identity mismatch")
            finally:
                os.close(fd)
            try:
                os.unlink(name, dir_fd=self._fds["responses"])
            except FileNotFoundError:
                pass
            os.unlink(name, dir_fd=self._fds["acks"])

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

    def _read(self, name: str) -> MailItem:
        path = Path(name)
        info = os.stat(name, dir_fd=self._fds["requests"], follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            raise MailboxViolation("mailbox symlink request")
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise MailboxViolation("mailbox request is not private regular file")
        if not path.name.endswith(".json") or not _ID.fullmatch(path.stem):
            raise MailboxViolation("mailbox invalid request identity")
        try:
            os.stat(name, dir_fd=self._fds["responses"], follow_symlinks=False)
            response_exists = True
        except FileNotFoundError:
            response_exists = False
        if path.stem in self._seen or response_exists:
            raise MailboxViolation("mailbox duplicate request identity")
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self._fds["requests"])
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_mode & 0o077:
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
        self._assert_dirs()
        for name in sorted(os.listdir(self._fds["requests"])):
            staged_path = Path(name)
            if staged_path.suffix == ".tmp" and _ID.fullmatch(staged_path.stem):
                continue
            item = self._read(name)
            self._seen.add(item.identifier)
            try:
                response = await handler(item)
                raw = json.dumps(response, separators=(",", ":"), ensure_ascii=True).encode()
                if len(raw) > _MAX_RESPONSE:
                    raise MailboxViolation("mailbox response size exceeded")
                staged = f"{item.identifier}.tmp"
                fd = os.open(
                    staged,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=self._fds["responses"],
                )
                try:
                    os.write(fd, raw)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                try:
                    os.link(
                        staged,
                        name,
                        src_dir_fd=self._fds["responses"],
                        dst_dir_fd=self._fds["responses"],
                        follow_symlinks=False,
                    )
                finally:
                    os.unlink(staged, dir_fd=self._fds["responses"])
            finally:
                try:
                    os.unlink(name, dir_fd=self._fds["requests"])
                except FileNotFoundError:
                    pass
            return True
        return False

    def close(self) -> None:
        try:
            self._assert_dirs()
            for name in ("requests", "responses", "acks"):
                for child in os.listdir(self._fds[name]):
                    os.unlink(child, dir_fd=self._fds[name])
                os.rmdir(name, dir_fd=self._fds["root"])
            self.root.rmdir()
        finally:
            for fd in self._fds.values():
                os.close(fd)
            self._fds.clear()
