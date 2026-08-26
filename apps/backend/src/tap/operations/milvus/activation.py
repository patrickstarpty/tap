"""Atomic local activation marker for the Milvus experiment only."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CorpusActivationState:
    activation_id: str
    corpus_version: str
    physical_collection: str
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class LocalCorpusActivator:
    """Write local experiment state; this is not an authorization policy store."""

    path: Path = Path(".local/milvus-active-corpus.json")

    async def activate(
        self,
        corpus_version: str,
        physical_collection: str,
        manifest_sha256: str,
    ) -> str:
        state = _activation_state(corpus_version, physical_collection, manifest_sha256)
        self._write(state)
        return state.activation_id

    async def snapshot(self) -> CorpusActivationState | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"), object_pairs_hook=_closed_pairs)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("local corpus activation marker is malformed") from error
        if not isinstance(raw, dict) or set(raw) != {
            "activationId",
            "corpusVersion",
            "manifestSha256",
            "physicalCollection",
        }:
            raise ValueError("local corpus activation marker is widened")
        state = _activation_state(
            raw["corpusVersion"],
            raw["physicalCollection"],
            raw["manifestSha256"],
        )
        if raw["activationId"] != state.activation_id:
            raise ValueError("local corpus activation identity does not match")
        return state

    async def restore(self, state: CorpusActivationState | None) -> None:
        if state is not None:
            expected = _activation_state(
                state.corpus_version,
                state.physical_collection,
                state.manifest_sha256,
            )
            if state != expected:
                raise ValueError("local corpus activation snapshot is malformed")
            self._write(state)
            return
        if self.path.exists():
            self.path.unlink()
            _fsync_directory(self.path.parent)

    def _write(self, state: CorpusActivationState) -> None:
        identity = {
            "corpusVersion": state.corpus_version,
            "manifestSha256": state.manifest_sha256,
            "physicalCollection": state.physical_collection,
        }
        marker = {"activationId": state.activation_id, **identity}
        encoded_marker = (json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
            )
            temporary_path = Path(raw_path)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded_marker)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
            _fsync_directory(self.path.parent)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def _activation_state(
    corpus_version: object,
    physical_collection: object,
    manifest_sha256: object,
) -> CorpusActivationState:
    _bounded("corpus_version", corpus_version, 256)
    _bounded("physical_collection", physical_collection, 255)
    if (
        not isinstance(manifest_sha256, str)
        or len(manifest_sha256) != 71
        or not manifest_sha256.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in manifest_sha256[7:])
    ):
        raise ValueError("manifest_sha256 must be a canonical digest")
    assert isinstance(corpus_version, str)
    assert isinstance(physical_collection, str)
    identity = {
        "corpusVersion": corpus_version,
        "manifestSha256": manifest_sha256,
        "physicalCollection": physical_collection,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return CorpusActivationState(
        activation_id="h_" + hashlib.sha256(encoded).hexdigest(),
        corpus_version=corpus_version,
        physical_collection=physical_collection,
        manifest_sha256=manifest_sha256,
    )


def _closed_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("local corpus activation marker contains duplicate keys")
        value[key] = item
    return value


def _fsync_directory(path: Path) -> None:
    directory = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _bounded(name: str, value: object, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 0x20 for character in value)
    ):
        raise ValueError(f"{name} must be bounded text")
