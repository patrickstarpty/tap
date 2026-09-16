"""Recovery effects and durable operator coordination are separate boundaries."""

from datetime import timedelta
from typing import Protocol

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.knowledge.domain.operations import (
    OperationClaim,
    OperationRequest,
    OperationResult,
)


class OperationRepository(Protocol):
    @property
    def scope(self) -> ProjectScopeContext: ...

    async def claim(
        self, request: OperationRequest, *, lease_duration: timedelta
    ) -> OperationClaim: ...

    async def renew(self, claim: OperationClaim, *, lease_duration: timedelta) -> None: ...

    async def complete(self, claim: OperationClaim, result: OperationResult) -> OperationClaim: ...


class OperationEffects(Protocol):
    @property
    def scope(self) -> ProjectScopeContext: ...

    async def execute(self, command: str, *, limit: int) -> dict[str, int]: ...
