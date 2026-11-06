"""Source-specific adapters for observed Agent lifecycle projection."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import replace

from sqlalchemy.ext.asyncio import AsyncSession

from tap.modules.access.domain.context import ProjectScopeContext
from tap.modules.chat.domain.conversations import ConversationTurn
from tap.modules.workflows.adapters.mysql_observations import MysqlAgentObservations
from tap.modules.workflows.application.observations import model_observation_scope

_chat_relation_task: ContextVar[str | None] = ContextVar("tapper_chat_relation_task", default=None)


class ChatObservationBinding:
    """Composition-owned adapter for the Chat answer phase's public scope port."""

    def __init__(
        self,
        observations: MysqlAgentObservations,
        catalog: object,
        conversations: object,
        scope: ProjectScopeContext,
    ) -> None:
        self._observations = observations
        self._catalog = catalog
        self._conversations = conversations
        self._scope = scope

    @asynccontextmanager
    async def bind(
        self,
        conversation_id: str,
        turn: ConversationTurn,
        authorize: Callable[[], Awaitable[None]],
        *,
        force_default: bool = False,
    ) -> AsyncIterator[None]:
        input_value = turn.input_snapshot.value
        revision_id = None if force_default else input_value.agent_revision_id

        async def start() -> None:
            if revision_id is None:
                agent_key, label = "orchestrator", "Tapper"
            else:
                asset = await self._catalog.resolve_historical_agent(self._scope, revision_id)  # type: ignore[attr-defined]
                agent_key = asset.asset_id
                label = input_value.agent_label or asset.display_name
            task_id = await self._observations.start(
                self._scope,
                source_kind="chat",
                source_key=turn.turn_id,
                invocation_key=f"{turn.turn_id}:{revision_id or 'legacy-default'}",
                agent_key=agent_key,
                agent_revision_id=revision_id,
                display_name=label,
                conversation_id=conversation_id,
                source_attempt=max(1, turn.attempt),
                source_claim_key=turn.lease_token,
            )
            await self._conversations.link_observed_task(  # type: ignore[attr-defined]
                conversation_id,
                turn.turn_id,
                task_id,
            )

        async with model_observation_scope(authorize, start):
            yield

    async def finish_source(self, conversation_id: str, turn_id: str, source_state: str) -> bool:
        task_id = await self._conversations.observed_task_id(conversation_id, turn_id)  # type: ignore[attr-defined]
        if task_id is None:
            return False
        return await self._observations.finish(self._scope, task_id, source_state)

    @asynccontextmanager
    async def bind_relation(
        self,
        conversation_id: str,
        turn_id: str,
    ) -> AsyncIterator[None]:
        task_id = await self._conversations.observed_task_id(  # type: ignore[attr-defined]
            conversation_id, turn_id
        )
        if task_id is None:
            raise ValueError("Chat relation tool has no observed source Agent")
        token = _chat_relation_task.set(task_id)
        try:
            yield
        finally:
            _chat_relation_task.reset(token)

    async def repair(self, limit: int = 50) -> int:
        """Bounded worker pass over durable source terminal facts after a crash."""
        candidates = await self._observations.pending_source(self._scope, "chat", limit)
        repaired = 0
        for task_id, conversation_id, source_key in candidates:
            if conversation_id is None:
                continue
            state = await self._conversations.observation_turn_state(  # type: ignore[attr-defined]
                conversation_id, source_key
            )
            if state is None:
                continue
            await self._conversations.link_observed_task(  # type: ignore[attr-defined]
                conversation_id, source_key, task_id
            )
            if state in ("completed", "abstained", "failed", "canceled"):
                repaired += int(await self._observations.finish(self._scope, task_id, state))
        return repaired


class SuggestionObservationBinding:
    """The original refresh row owns completion and the logical cycle ID."""

    def __init__(self, observations: MysqlAgentObservations, scope: ProjectScopeContext) -> None:
        self._observations = observations
        self._scope = scope

    async def before_generate(self, claim: object) -> None:
        cycle = claim.refresh_cycle_id  # type: ignore[attr-defined]
        if not cycle:
            raise ValueError("claimed suggestion refresh lacks cycle identity")
        scope = replace(self._scope, actor_id=claim.key.actor_id)  # type: ignore[attr-defined]
        await self._observations.start(
            scope,
            source_kind="suggestions",
            source_key=cycle,
            invocation_key=cycle,
            agent_key="prompt_suggestions",
            agent_revision_id=None,
            display_name="Recommended questions",
            source_attempt=claim.attempt_count + 1,  # type: ignore[attr-defined]
            source_claim_key=claim.lease_token,  # type: ignore[attr-defined]
        )

    async def accepted_complete(self, session: AsyncSession, claim: object) -> None:
        cycle = claim.refresh_cycle_id  # type: ignore[attr-defined]
        if not cycle:
            return
        scope = replace(self._scope, actor_id=claim.key.actor_id)  # type: ignore[attr-defined]
        task_id = self._observations.task_id_for(scope, "suggestions", cycle)
        await self._observations.finish_in_transaction(
            session,
            scope,
            task_id,
            "completed",
            missing_ok=True,
            winning_claim_key=claim.lease_token,  # type: ignore[attr-defined]
        )


class RelationObservationBinding:
    """Observe the deterministic Agent at its actual run boundary."""

    def __init__(
        self,
        observations: MysqlAgentObservations,
        scope: ProjectScopeContext,
    ) -> None:
        self._observations = observations
        self._scope = scope

    @asynccontextmanager
    async def bind(
        self,
        turn_id: str | None,
        operation_id: str,
    ) -> AsyncIterator[None]:
        chat_task_id = _chat_relation_task.get()
        if chat_task_id is None:
            task_id = await self._observations.start(
                self._scope,
                source_kind="relation_analysis",
                source_key=operation_id,
                invocation_key=operation_id,
                agent_key="relation-analysis",
                agent_revision_id=None,
                display_name="Relation analysis",
            )
            try:
                yield
            except BaseException:
                await self._observations.finish(self._scope, task_id, "failed")
                raise
            else:
                await self._observations.finish(self._scope, task_id, "completed")
            return

        snapshot = await self._observations.snapshot(self._scope, chat_task_id)
        root = next((node for node in snapshot["nodes"] if node["parent_node_id"] is None), None)
        if root is None:
            raise ValueError("Chat observed source Agent has no root")
        async with self._observations.record_agent_invocation(
            self._scope,
            chat_task_id,
            "relation-analysis",
            root["node_id"],
            operation_id,
            source_kind="relation_analysis",
        ) as node_id:
            try:
                yield
            except BaseException:
                await self._observations.finish_node(
                    self._scope, chat_task_id, node_id, succeeded=False
                )
                raise
            else:
                await self._observations.finish_node(
                    self._scope, chat_task_id, node_id, succeeded=True
                )


class TestDesignObservationBinding:
    """The existing Test Design job remains the terminal authority."""

    def __init__(
        self,
        observations: MysqlAgentObservations,
        catalog: object,
        jobs: object,
        scope: ProjectScopeContext,
    ) -> None:
        self._observations = observations
        self._catalog = catalog
        self._jobs = jobs
        self._scope = scope

    @staticmethod
    def _invocation_key(job: object) -> str:
        request = job.request  # type: ignore[attr-defined]
        return f"{request.job_id}:{request.agent_revision_id}"

    async def before_generate(self, claim: object) -> None:
        job = claim.job  # type: ignore[attr-defined]
        asset = await self._catalog.resolve_historical_agent(  # type: ignore[attr-defined]
            self._scope, job.request.agent_revision_id
        )
        await self._observations.start(
            self._scope,
            source_kind="test_design",
            source_key=job.request.job_id,
            invocation_key=self._invocation_key(job),
            agent_key=asset.asset_id,
            agent_revision_id=job.request.agent_revision_id,
            display_name=asset.display_name,
            conversation_id=job.request.conversation_id,
            source_attempt=max(1, job.attempt_count),
            source_claim_key=claim.lease_token,  # type: ignore[attr-defined]
        )

    async def finish_source(self, job_id: str) -> bool:
        job = await self._jobs.get_generation_job(self._scope, job_id)  # type: ignore[attr-defined]
        if job.status.value not in ("DRAFT_READY", "FAILED", "CANCELED"):
            return False
        task_id = self._observations.task_id_for(
            self._scope, "test_design", self._invocation_key(job)
        )
        try:
            return await self._observations.finish(self._scope, task_id, job.status.value)
        except ValueError as exc:
            if str(exc) == "observed task required":
                return False
            raise

    async def repair(self, limit: int = 50) -> int:
        candidates = await self._observations.pending_source(self._scope, "test_design", limit)
        repaired = 0
        for _task_id, _conversation_id, job_id in candidates:
            repaired += int(await self.finish_source(job_id))
        return repaired
