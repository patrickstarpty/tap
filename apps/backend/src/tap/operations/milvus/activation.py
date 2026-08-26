"""Atomic local activation marker for the Milvus experiment only."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


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
        _bounded("corpus_version", corpus_version, 256)
        _bounded("physical_collection", physical_collection, 255)
        if (
            len(manifest_sha256) != 71
            or not manifest_sha256.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in manifest_sha256[7:])
        ):
            raise ValueError("manifest_sha256 must be a canonical digest")
        identity = {
            "corpusVersion": corpus_version,
            "manifestSha256": manifest_sha256,
            "physicalCollection": physical_collection,
        }
        encoded_identity = json.dumps(
            identity,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        activation_id = "h_" + hashlib.sha256(encoded_identity).hexdigest()
        marker = {"activationId": activation_id, **identity}
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
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return activation_id


def _bounded(name: str, value: object, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 0x20 for character in value)
    ):
        raise ValueError(f"{name} must be bounded text")
