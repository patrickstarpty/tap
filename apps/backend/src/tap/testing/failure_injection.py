"""Redis-backed one-shot stage control for the exact Athena E2E profile."""

from __future__ import annotations

import re
from collections.abc import Awaitable
from typing import Literal, Protocol, cast

from tap.modules.knowledge.application.ingestion import IngestionStageFailure
from tap.modules.knowledge.ports.documents import JobStage

FailureStage = Literal["parsing", "embedding", "publishing"]
FailureArmStatus = Literal["armed", "already-armed"]
_STAGES: dict[FailureStage, JobStage] = {
    "parsing": JobStage.PARSING,
    "embedding": JobStage.EMBEDDING,
    "publishing": JobStage.PUBLISHING,
}
_PROJECT = re.compile(r"[a-z0-9][a-z0-9_-]{2,62}\Z")
_TTL_SECONDS = 300


class _FailureRedis(Protocol):
    def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool,
        ex: int,
    ) -> Awaitable[object] | object: ...

    def getdel(self, key: str) -> Awaitable[object] | object: ...


class RedisStageFailureController:
    """Arm with SET NX EX and consume with GETDEL under one project namespace."""

    def __init__(self, *, redis: _FailureRedis, project: str) -> None:
        if _PROJECT.fullmatch(project) is None:
            raise ValueError("failure-control project must be a safe Compose project")
        if not callable(getattr(redis, "set", None)) or not callable(
            getattr(redis, "getdel", None)
        ):
            raise TypeError("failure control requires atomic Redis operations")
        self._redis = redis
        self._project = project

    async def arm(self, stage: str) -> FailureArmStatus:
        closed = _stage(stage)
        armed = await cast(
            Awaitable[object],
            self._redis.set(
                self._key(closed),
                "armed",
                nx=True,
                ex=_TTL_SECONDS,
            ),
        )
        return "armed" if armed is True else "already-armed"

    async def before_stage(self, stage: JobStage) -> None:
        if stage not in _STAGES.values():
            raise ValueError("failure control received a non-injectable stage")
        closed = cast(FailureStage, stage.value)
        value = await cast(Awaitable[object], self._redis.getdel(self._key(closed)))
        if value is None:
            return
        if value != "armed":
            raise RuntimeError("failure control state is invalid")
        raise IngestionStageFailure(stage)

    def _key(self, stage: FailureStage) -> str:
        return f"tap:athena:e2e:{self._project}:fail-next:{stage}"


def _stage(value: str) -> FailureStage:
    if not isinstance(value, str) or value not in _STAGES:
        raise ValueError("failure stage is outside the closed set")
    return cast(FailureStage, value)
