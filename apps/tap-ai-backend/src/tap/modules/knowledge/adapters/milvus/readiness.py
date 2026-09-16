"""Reader-only readiness check for one immutable Milvus doc target."""

from __future__ import annotations

import asyncio
import json
import math
import re
from dataclasses import dataclass

from tap.modules.knowledge.adapters.milvus.config import MilvusIndexTarget
from tap.modules.knowledge.adapters.milvus.targets import bind_target
from tap.modules.knowledge.adapters.milvus.transport import MilvusQueryRequest, MilvusReader
from tap.modules.knowledge.ports.errors import SearchError, SearchUnavailable

_CHUNK_ID = re.compile(r"h_[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class MilvusReadinessCanary:
    chunk_id: str
    tenant_id: str
    project_id: str
    group_id: str
    corpus_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.chunk_id, str) or _CHUNK_ID.fullmatch(self.chunk_id) is None:
            raise ValueError("readiness canary chunk identity is malformed")
        for name in ("tenant_id", "project_id", "group_id", "corpus_version"):
            _bounded_literal(name, getattr(self, name))


class MilvusReadinessProbe:
    def __init__(
        self,
        target: MilvusIndexTarget,
        reader: MilvusReader,
        canary: MilvusReadinessCanary,
        timeout_seconds: float = 3.0,
    ) -> None:
        if not isinstance(target, MilvusIndexTarget):
            raise TypeError("Milvus readiness requires a configured target")
        if not isinstance(canary, MilvusReadinessCanary):
            raise TypeError("Milvus readiness requires a validated canary")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 30
        ):
            raise ValueError("readiness timeout must be finite and greater than zero through 30")
        self._target = target
        self._reader = reader
        self._canary = canary
        self._timeout_seconds = float(timeout_seconds)

    async def check(self) -> None:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                bound = await bind_target(self._reader, self._target)
                rows = await self._reader.query(
                    MilvusQueryRequest(
                        collection_name=bound.physical_collection,
                        filter_expression=_canary_filter(self._canary),
                        output_fields=("chunk_id",),
                        limit=2,
                    )
                )
                if rows != ({"chunk_id": self._canary.chunk_id},):
                    raise SearchUnavailable("Milvus readiness canary failed")
        except TimeoutError:
            raise SearchUnavailable("Milvus readiness check timed out") from None
        except SearchError:
            raise
        except Exception:
            raise SearchUnavailable("Milvus readiness check failed") from None


def _canary_filter(canary: MilvusReadinessCanary) -> str:
    def literal(value: str) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    return " and ".join(
        (
            f"chunk_id == {literal(canary.chunk_id)}",
            f"tenant_id == {literal(canary.tenant_id)}",
            f"project_id == {literal(canary.project_id)}",
            f"ARRAY_CONTAINS(allowed_group_ids, {literal(canary.group_id)})",
            f"corpus_version == {literal(canary.corpus_version)}",
            "deleted == false",
        )
    )


def _bounded_literal(name: str, value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or any(ord(character) < 0x20 for character in value)
    ):
        raise ValueError(f"readiness canary {name} must be a bounded string")
