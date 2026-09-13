"""Authorize, coordinate and record bounded recovery; effects stay outside SQL."""

import asyncio
from datetime import timedelta

from tap.modules.access.application.ports import AuthorizationPolicy
from tap.modules.access.domain.authorization import ResourceRef
from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.access.domain.policy import AuthorizationDenied
from tap.modules.knowledge.domain.operations import (
    OperationClaim,
    OperationRequest,
    OperationResult,
)
from tap.modules.knowledge.ports.operations import OperationEffects, OperationRepository


class KnowledgeOperator:
    def __init__(
        self,
        *,
        scope: ProjectScopeContext,
        policy: AuthorizationPolicy,
        repository: OperationRepository,
        effects: OperationEffects,
        lease_duration: timedelta = timedelta(seconds=60),
    ) -> None:
        if (
            not isinstance(scope, ProjectScopeContext)
            or scope != repository.scope
            or scope != effects.scope
        ):
            raise ValueError("operator scope differs from bound dependencies")
        if not 3 <= lease_duration.total_seconds() <= 300:
            raise ValueError("operator lease must be between 3 and 300 seconds")
        self._scope = scope
        self._policy = policy
        self._repository = repository
        self._effects = effects
        self._lease_duration = lease_duration

    async def run(self, request: OperationRequest) -> OperationClaim:
        if type(request) is not OperationRequest:
            raise TypeError("operator requires a validated request")
        decision = await self._policy.authorize(
            self._scope,
            "knowledge.operate",
            ResourceRef(
                enterprise_id=self._scope.enterprise_id,
                project_id=self._scope.project_id,
                kind="knowledge",
            ),
        )
        if not decision.allowed:
            raise AuthorizationDenied(decision.reason)
        claim = await self._repository.claim(request, lease_duration=self._lease_duration)
        if claim.result is not None:
            return claim
        work = asyncio.create_task(self._observe(claim))
        heartbeat = asyncio.create_task(self._renew(claim))
        try:
            done, _ = await asyncio.wait({work, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                await heartbeat
                raise AssertionError("operator heartbeat stopped")
            result = await work
            await self._repository.renew(claim, lease_duration=self._lease_duration)
            return await self._repository.complete(claim, result)
        finally:
            work.cancel()
            heartbeat.cancel()
            await asyncio.gather(work, heartbeat, return_exceptions=True)

    async def _renew(self, claim: OperationClaim) -> None:
        while True:
            await asyncio.sleep(self._lease_duration.total_seconds() / 3)
            await self._repository.renew(claim, lease_duration=self._lease_duration)

    async def _observe(self, claim: OperationClaim) -> OperationResult:
        commands = (
            ("recover-uploads", "scavenge-staging", "rebuild-milvus", "reconcile-all")
            if claim.command == "reconcile-all"
            else (claim.command,)
        )
        counts: dict[str, int] = {}
        failed = 0
        for command in commands:
            await self._repository.renew(claim, lease_duration=self._lease_duration)
            try:
                observed = await self._effects.execute(command, limit=claim.limit)
                OperationResult(outcome="completed", counts=observed)
                for key, value in observed.items():
                    counts[key] = counts.get(key, 0) + value
            except asyncio.CancelledError:
                raise
            except Exception:
                failed += 1
        if failed:
            counts["failed_count"] = counts.get("failed_count", 0) + failed
        outcome = (
            "partial"
            if claim.fence > 1 or (failed and failed < len(commands))
            else "failed"
            if failed
            else "completed"
        )
        return OperationResult(outcome=outcome, counts=counts)
