"""Frozen browser-coding task result boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from tap.modules.access.domain.context import ProjectScopeContext


@dataclass(frozen=True)
class CodingResult:
    status: Literal["passed", "failed", "blocked", "canceled", "unknown"]
    provider_session_id: str | None = None
    bundle_ref: str | None = None
    execution_ref: str | None = None
    usage: dict[str, object] = field(default_factory=dict)
    terminal_evidence: dict[str, Any] = field(default_factory=dict)
    input_request: dict[str, str] | None = None
    replay_model_calls: int = 0


class CodingRuntimeConfig(Protocol):
    @property
    def model_alias(self) -> str: ...


class CodingRuntimePort(Protocol):
    @property
    def config(self) -> CodingRuntimeConfig: ...

    async def run(
        self,
        task_id: str,
        request: dict[str, Any],
        snapshot: dict[str, Any],
        scope: ProjectScopeContext,
        lease_token: str,
        node_id: str,
        attempt: int,
        stop_requested: Callable[[], Any] | None = None,
        *,
        script_source: dict[str, Any] | None = None,
    ) -> CodingResult: ...
