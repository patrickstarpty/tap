from types import SimpleNamespace

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE


@pytest.mark.asyncio
async def test_public_knowledge_search_reserves_shared_model_budget_and_replays_result():
    from tap.modules.workflows.adapters.native_runtime import JournaledKnowledgeSearch

    calls = []

    class Store:
        def __init__(self):
            self.result = None

        async def task_request(self, scope, task_id):
            return {
                "resolved_resources": [
                    {
                        "source_id": "source-1",
                        "document_id": "doc-1",
                        "revision_id": "rev-1",
                        "source_content_hash": "sha256:" + "a" * 64,
                    }
                ]
            }

        async def graph_lineage(self, scope, task_id, node_id, attempt):
            return "run-1", 1

        async def action_result(self, scope, action_id):
            return self.result

        async def reserve_action(self, scope, task_id, token, action_id, kind, **kwargs):
            calls.append(("reserve", kind))
            assert kind == "model"
            return "reserved"

        async def settle_action(self, scope, task_id, token, action_id, result, **kwargs):
            calls.append(("settle", result["private_replay"]))
            self.result = result
            return result

    class Knowledge:
        async def search(self, request, policy):
            calls.append(("search", request.query))
            assert policy == "verified-policy"
            return SimpleNamespace(
                evidence=(SimpleNamespace(citation_id="cite-1", content="Login steps"),)
            )

    async def policy_for(resources):
        calls.append(("policy", len(resources)))
        return "verified-policy"

    async def authorize():
        calls.append(("authorize", None))
        return True

    tool = JournaledKnowledgeSearch(Store(), Knowledge(), policy_for)
    first = await tool.execute(
        VALIDATION_SCOPE,
        "task-1",
        "token-1",
        "node-1",
        1,
        "knowledge_search",
        {"query": "login"},
        "call-1",
        authorize,
    )
    second = await tool.execute(
        VALIDATION_SCOPE,
        "task-1",
        "token-2",
        "node-1",
        2,
        "knowledge_search",
        {"query": "login"},
        "call-1",
        authorize,
    )
    assert first == second
    assert "Login steps" in first
    assert [item[0] for item in calls].count("reserve") == 1
    assert [item[0] for item in calls].count("search") == 1
    assert calls.index(("reserve", "model")) < calls.index(("search", "login"))


@pytest.mark.asyncio
async def test_model_call_is_clamped_to_remaining_task_deadline_and_left_unknown_on_timeout():
    import asyncio
    from datetime import UTC, datetime, timedelta

    from tap.modules.ai.domain.models import ModelOperation, ModelRequest, text_digest
    from tap.modules.workflows.adapters.native_runtime import JournaledModelGateway

    deadline = (datetime.now(UTC) + timedelta(milliseconds=250)).isoformat()

    class Store:
        def __init__(self):
            self.reserved = 0
            self.settled = 0

        async def graph_lineage(self, scope, task_id, node_id, attempt):
            return "run-1", 1

        async def action_result(self, scope, action_id):
            return None

        async def task_request(self, scope, task_id):
            return {"deadline_at": deadline}

        async def reserve_action(self, *args, **kwargs):
            self.reserved += 1
            return "reserved"

        async def settle_action(self, *args, **kwargs):
            self.settled += 1
            return {}

    class SlowGateway:
        def __init__(self):
            self.timeout_seen = None

        async def chat(self, request):
            self.timeout_seen = request.timeout_seconds
            await asyncio.sleep(1)
            raise AssertionError("deadline did not clamp external call")

    store, gateway = Store(), SlowGateway()
    model = JournaledModelGateway(gateway, store, "task-1", "token-1", "node-1", 1)
    request = ModelRequest(
        scope=VALIDATION_SCOPE,
        alias="qwen-plus",
        operation=ModelOperation.CHAT,
        prompt="Review",
        prompt_digest=text_digest("Review"),
        context="Review",
        timeout_seconds=60,
        idempotency_key="task-1:node-1:graph:run-1:model:1",
    )
    with pytest.raises(TimeoutError):
        await model.chat(request)
    assert gateway.timeout_seen is not None and 0 < gateway.timeout_seen < 1
    assert store.reserved == 1 and store.settled == 0


@pytest.mark.asyncio
async def test_knowledge_search_is_clamped_to_task_deadline_and_left_unknown_on_timeout():
    import asyncio
    from datetime import UTC, datetime, timedelta

    from tap.modules.workflows.adapters.native_runtime import JournaledKnowledgeSearch

    deadline = (datetime.now(UTC) + timedelta(milliseconds=250)).isoformat()

    class Store:
        def __init__(self):
            self.reserved = 0
            self.settled = 0

        async def task_request(self, scope, task_id):
            return {
                "deadline_at": deadline,
                "resolved_resources": [
                    {
                        "source_id": "source-1",
                        "revision_id": "rev-1",
                        "source_content_hash": "sha256:" + "a" * 64,
                    }
                ],
            }

        async def graph_lineage(self, scope, task_id, node_id, attempt):
            return "run-1", 1

        async def action_result(self, scope, action_id):
            return None

        async def reserve_action(self, *args, **kwargs):
            self.reserved += 1
            return "reserved"

        async def settle_action(self, *args, **kwargs):
            self.settled += 1
            return {}

    class SlowKnowledge:
        async def search(self, request, policy):
            await asyncio.sleep(1)
            raise AssertionError("deadline did not clamp search")

    async def policy_for(resources):
        return "verified-policy"

    async def authorize():
        return True

    store = Store()
    tool = JournaledKnowledgeSearch(store, SlowKnowledge(), policy_for)
    with pytest.raises(TimeoutError):
        await tool.execute(
            VALIDATION_SCOPE,
            "task-1",
            "token-1",
            "node-1",
            1,
            "knowledge_search",
            {"query": "login"},
            "call-1",
            authorize,
        )
    assert store.reserved == 1 and store.settled == 0


@pytest.mark.asyncio
async def test_known_cost_overage_keeps_settlement_and_blocks_replay_progress():
    from decimal import Decimal

    from tap.modules.ai.domain.models import ModelOperation, ModelRequest, ModelUsage, text_digest
    from tap.modules.workflows.adapters.native_runtime import JournaledModelGateway
    from tap.modules.workflows.domain.commands import WorkflowLimitExceeded

    class Store:
        def __init__(self):
            self.saved = None
            self.settled = 0

        async def graph_lineage(self, scope, task_id, node_id, attempt):
            return "run-1", 1

        async def action_result(self, scope, action_id):
            return self.saved

        async def task_request(self, scope, task_id):
            return {}

        async def reserve_action(self, *args, **kwargs):
            return "reserved"

        async def settle_action(self, scope, task_id, token, action_id, result, **kwargs):
            self.settled += 1
            self.saved = result
            return result

        async def snapshot(self, scope, task_id):
            return {"budget_limit": {"cost_micros": 1}, "budget_used": {"cost_micros": 2}}

    class Gateway:
        def __init__(self):
            self.calls = 0

        async def chat(self, request):
            self.calls += 1
            return SimpleNamespace(
                output="Answer",
                actual_model="fixture",
                actual_provider="fixture",
                usage=ModelUsage(1, 1),
                provider_request_id=None,
                gateway_call_id=None,
                call_id=None,
                cost_usd=Decimal("0.000002"),
                tool_calls=(),
            )

    store, gateway = Store(), Gateway()
    journal = JournaledModelGateway(gateway, store, "task-1", "token-1", "node-1", 1)
    request = ModelRequest(
        scope=VALIDATION_SCOPE,
        alias="qwen-plus",
        operation=ModelOperation.CHAT,
        prompt="Review",
        prompt_digest=text_digest("Review"),
        context="Review",
        timeout_seconds=60,
        idempotency_key="task-1:node-1:graph:run-1:model:1",
    )
    with pytest.raises(WorkflowLimitExceeded):
        await journal.chat(request)
    with pytest.raises(WorkflowLimitExceeded):
        await journal.chat(request)
    assert gateway.calls == 1 and store.settled == 1
    assert store.saved["cost_usd"] == "0.000002"
