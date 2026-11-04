"""The execute node owns durability; transient tool-loop state must stay local."""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.adapters.litellm import LiteLLMModelGatewayConfig
from tap.modules.ai.adapters.litellm_catalog import ModelRoles
from tap.modules.ai.adapters.mysql_checkpointer import MysqlGraphCheckpointer, graph_checkpoint
from tap.modules.ai.application.interaction_graph import InteractionGraph
from tap.modules.ai.application.tool_loop import AgentToolRuntime
from tap.modules.ai.domain.models import ChatMessage, ModelOperation, ModelRequest, text_digest
from tap.modules.ai.domain.tools import ToolResult
from tap.testing.deterministic_model_gateway import DeterministicModelGateway
from tests.owned_mysql import owned_project_database_url


def test_nested_tool_loop_uses_only_outer_mysql_checkpoints(owned_project_mysql):
    async def scenario():
        engine = create_async_engine(owned_project_database_url(owned_project_mysql))
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def redact(value):
            return value

        gateway = DeterministicModelGateway(
            LiteLLMModelGatewayConfig(
                base_url="http://127.0.0.1:4000",
                api_key="fixture",
                roles=ModelRoles("qwen-plus", "text-embedding-v4", None),
                embedding_dimension=2,
            ),
            scope=VALIDATION_SCOPE,
            redact=redact,
        )
        tools = AgentToolRuntime(gateway)

        class Search:
            async def invoke(self, scope, arguments):
                return ToolResult("fixture evidence")

        async def authorized():
            return True

        async def classify(state):
            return {"reasoning_mode": "agentic"}

        async def admit(state):
            return {"admitted": True}

        async def execute(state):
            prompt, context = (
                "Explore the authorized relationships.",
                '{"question":"What relationship?"}',
            )
            request = ModelRequest(
                VALIDATION_SCOPE,
                "qwen-plus",
                ModelOperation.CHAT,
                prompt,
                text_digest(prompt),
                context,
                1.0,
                "nested-loop",
                tool_allowlist=frozenset({"knowledge.answer", "knowledge.search"}),
                governance_digests=(text_digest("fixture"),),
            )
            outcome = await tools.loop(Search()).run(
                VALIDATION_SCOPE,
                request,
                (ChatMessage("system", prompt), ChatMessage("user", context)),
                request.tool_allowlist,
            )
            return {"result": {"text": outcome.transcript[-1].content}}

        try:
            graph = InteractionGraph(
                graph_version="nested-test-v1",
                state_schema_version=1,
                checkpointer=MysqlGraphCheckpointer(sessions, scope=VALIDATION_SCOPE),
                classify=classify,
                admit=admit,
                execute=execute,
                authorize=authorized,
            )
            result = await graph.start(run_id="nested-tool", payload={}, execution_mode="durable")
            assert result["result"]["text"] == "Grounded"
            async with sessions() as session:
                namespaces = (await session.scalars(select(graph_checkpoint.c.checkpoint_ns))).all()
                assert namespaces and set(namespaces) == {""}
        finally:
            await engine.dispose()

    asyncio.run(scenario())
