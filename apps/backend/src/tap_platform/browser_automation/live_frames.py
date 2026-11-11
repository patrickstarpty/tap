"""Single-slot expiring live frames. Never entered into the evidence ledger."""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Callable


class LiveFrameBroker:
    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time):
        if root.is_symlink():
            raise ValueError("live frame root cannot be a symlink")
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.root = root.resolve()
        self.clock = clock

    def _path(self, session_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session_id):
            raise ValueError("invalid live frame session")
        return self.root / (session_id + ".json")

    def publish(
        self, session_id: str, frame: dict[str, Any], *, control_epoch: int
    ) -> None:
        path = self._path(session_id)
        if type(control_epoch) is not int or control_epoch < 0:
            raise ValueError("invalid live frame epoch")
        image = frame.get("image_base64")
        if (
            not isinstance(image, str)
            or len(image) > 700_000
            or frame.get("media_type") != "image/jpeg"
        ):
            raise ValueError("invalid live frame image")
        try:
            data = base64.b64decode(image, validate=True)
        except ValueError as exc:
            raise ValueError("invalid live frame image") from exc
        if not data.startswith(b"\xff\xd8") or len(data) > 500_000:
            raise ValueError("invalid live frame image")
        viewport = frame.get("viewport")
        if not isinstance(viewport, dict) or any(
            type(viewport.get(k)) is not int or not 1 <= viewport[k] <= 4096
            for k in ("width", "height")
        ):
            raise ValueError("invalid live frame viewport")
        if type(frame.get("frame_sequence")) is not int or frame["frame_sequence"] < 0:
            raise ValueError("invalid live frame sequence")
        if (
            not isinstance(frame.get("captured_at"), str)
            or len(frame["captured_at"]) > 80
        ):
            raise ValueError("invalid live frame time")
        value = {
            k: frame[k]
            for k in (
                "frame_sequence",
                "captured_at",
                "viewport",
                "image_base64",
                "media_type",
            )
        }
        value.update(control_epoch=control_epoch, expires_at=self.clock() + 5)
        descriptor, temporary = tempfile.mkstemp(prefix=".frame-", dir=self.root)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(value, stream, separators=(",", ":"), allow_nan=False)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def read(self, session_id: str, *, control_epoch: int) -> dict[str, Any]:
        path = self._path(session_id)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor) as stream:
                raw = stream.read(750_001)
            if len(raw) > 750_000:
                raise ValueError("oversized frame")
            value = json.loads(raw)
            if (
                not isinstance(value, dict)
                or value.get("expires_at", 0) <= self.clock()
            ):
                self.delete(session_id)
                raise LookupError("live frame expired")
            if value.get("control_epoch") != control_epoch:
                raise LookupError("live frame epoch changed")
            return {k: v for k, v in value.items() if k != "expires_at"}
        except (OSError, ValueError, TypeError) as exc:
            raise LookupError("live frame unavailable") from exc

    def delete(self, session_id: str) -> None:
        self._path(session_id).unlink(missing_ok=True)

    def purge_expired(self) -> None:
        for path in self.root.glob("*.json"):
            try:
                if self.clock() - path.stat().st_mtime > 5:
                    self.delete(path.stem)
            except (OSError, ValueError):
                continue
