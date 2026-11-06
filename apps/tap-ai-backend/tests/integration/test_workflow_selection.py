"""Task selection and follow-up through owned MySQL command storage."""

import asyncio

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.chat.application.conversations import (
    ConversationService,
    InMemoryConversationRepository,
)
from tap.modules.chat.domain.conversations import TurnInput
from tap.modules.workflows.adapters.mysql import MysqlWorkflowStore
from tap.modules.workflows.adapters.schema import workflow_command, workflow_task
from tap.modules.workflows.application.selection import WorkflowSelection
from tap.modules.workflows.application.service import WorkflowService
from tests.owned_mysql import owned_project_database_url


@pytest.mark.asyncio
async def test_follow_up_replays_original_command_after_task_plan_advances(owned_project_mysql):
    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = MysqlWorkflowStore(sessions)
        conversations = ConversationService(
            InMemoryConversationRepository(), scope=VALIDATION_SCOPE
        )
        await conversations.create(
            "conversation-1",
            "turn-1",
            "initial",
            TurnInput("Start", VALIDATION_SCOPE.actor_id, "validation", "qwen-plus"),
        )
        task = await store.create_task(
            VALIDATION_SCOPE,
            {"goal": "Review", "conversation_id": "conversation-1"},
            "task-create",
        )
        selection = WorkflowSelection(conversations, store, WorkflowService(store))
        frozen = TurnInput(
            "Only login",
            VALIDATION_SCOPE.actor_id,
            "validation",
            "qwen-plus",
            workflow_task_id=task.task_id,
        )
        first = await selection.follow_up(VALIDATION_SCOPE, "conversation-1", frozen, "follow-up-1")
        async with sessions.begin() as session:
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == task.task_id)
                .values(plan_revision=2)
            )
        second = await selection.follow_up(
            VALIDATION_SCOPE, "conversation-1", frozen, "follow-up-1"
        )
        assert second == first
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(workflow_command)) == 1
            revision = await session.scalar(
                select(workflow_command.c.expected_plan_revision).where(
                    workflow_command.c.task_id == task.task_id
                )
            )
            assert revision == 1
        with pytest.raises(ValueError, match="command id payload conflict"):
            await selection.follow_up(
                VALIDATION_SCOPE,
                "conversation-1",
                TurnInput(
                    "Different work",
                    VALIDATION_SCOPE.actor_id,
                    "validation",
                    "qwen-plus",
                    workflow_task_id=task.task_id,
                ),
                "follow-up-1",
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_follow_up_reuses_winning_mysql_command(owned_project_mysql):
    engine = create_async_engine(owned_project_database_url(owned_project_mysql))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = MysqlWorkflowStore(sessions)
        conversations = ConversationService(
            InMemoryConversationRepository(), scope=VALIDATION_SCOPE
        )
        await conversations.create(
            "conversation-1",
            "turn-1",
            "initial",
            TurnInput("Start", VALIDATION_SCOPE.actor_id, "validation", "qwen-plus"),
        )
        task = await store.create_task(
            VALIDATION_SCOPE,
            {"goal": "Review", "conversation_id": "conversation-1"},
            "task-create",
        )

        class PausedFirstSubmit(WorkflowService):
            def __init__(self):
                super().__init__(store)
                self.waiting = asyncio.Event()
                self.resume = asyncio.Event()
                self.calls = 0

            async def submit(self, scope, command):
                self.calls += 1
                if self.calls == 1:
                    self.waiting.set()
                    await self.resume.wait()
                return await super().submit(scope, command)

        controls = PausedFirstSubmit()
        selection = WorkflowSelection(conversations, store, controls)
        frozen = TurnInput(
            "Only login",
            VALIDATION_SCOPE.actor_id,
            "validation",
            "qwen-plus",
            workflow_task_id=task.task_id,
        )
        first = asyncio.create_task(
            selection.follow_up(VALIDATION_SCOPE, "conversation-1", frozen, "follow-up-1")
        )
        await controls.waiting.wait()
        async with sessions.begin() as session:
            await session.execute(
                update(workflow_task)
                .where(workflow_task.c.task_id == task.task_id)
                .values(plan_revision=2)
            )
        second_receipt = await selection.follow_up(
            VALIDATION_SCOPE, "conversation-1", frozen, "follow-up-1"
        )
        controls.resume.set()
        assert await first == second_receipt
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(workflow_command)) == 1
            revision = await session.scalar(
                select(workflow_command.c.expected_plan_revision).where(
                    workflow_command.c.task_id == task.task_id
                )
            )
            assert revision == 2
    finally:
        await engine.dispose()
