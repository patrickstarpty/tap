"""Port for durably recording model calls; failures degrade, never raise."""

from __future__ import annotations

from typing import Protocol

from tap.modules.ai.domain.model_calls import ModelCallRecord


class ModelCallRecorder(Protocol):
    async def record(self, call: ModelCallRecord) -> None: ...

    def degraded(self) -> bool: ...


__all__ = ["ModelCallRecorder"]
