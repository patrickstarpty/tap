from types import SimpleNamespace

import pytest

from tap.modules.access.adapters.validation import VALIDATION_SCOPE


@pytest.mark.asyncio
async def test_native_model_provider_key_is_bounded_without_changing_journal_replay_identity():
    from tap.modules.ai.domain.models import ModelOperation, ModelRequest, ModelUsage, text_digest
    from tap.modules.workflows.adapters.native_runtime import JournaledModelGateway

    task_id, node_id, run_id = "t" * 32, "n" * 32, "r" * 64
    original_key = f"{task_id}:{node_id}:graph:{run_id}:model:1"

    class Store:
        saved = None

        async def graph_lineage(self, scope, task_id, node_id, attempt):
            return run_id, 1

        async def action_result(self, scope, action_id):
            return self.saved

        async def task_request(self, scope, task_id):
            return {}

        async def reserve_action(self, *args, **kwargs):
            return "reserved"

        async def settle_action(self, scope, task_id, token, action_id, result, **kwargs):
            self.saved = result
            return result

        async def snapshot(self, scope, task_id):
            return {"budget_limit": {}, "budget_used": {}}

    class Gateway:
        keys = []

        async def chat(self, request):
            self.keys.append(request.idempotency_key)
            assert len(request.idempotency_key) <= 128
            return SimpleNamespace(
                output="Answer",
                actual_model="fixture",
                actual_provider="fixture",
                usage=ModelUsage(1, 1),
                provider_request_id=None,
                gateway_call_id=None,
                call_id=None,
                cost_usd=None,
                tool_calls=(),
            )

    store, gateway = Store(), Gateway()
    request = ModelRequest(
        scope=VALIDATION_SCOPE,
        alias="qwen-plus",
        operation=ModelOperation.CHAT,
        prompt="Review",
        prompt_digest=text_digest("Review"),
        context="Review",
        timeout_seconds=15,
        idempotency_key=original_key,
    )
    first = await JournaledModelGateway(gateway, store, task_id, "token", node_id, 1).chat(request)
    second = await JournaledModelGateway(gateway, store, task_id, "retry-token", node_id, 2).chat(
        request
    )
    assert first.output == second.output == "Answer"
    assert first.audit.idempotency_key == second.audit.idempotency_key == original_key
    assert len(gateway.keys) == 1


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

    async def actual_step(scope, task_id, token, node_id, attempt, kind, call_id, name, outcome):
        calls.append((kind, outcome))

    tool = JournaledKnowledgeSearch(Store(), Knowledge(), policy_for, on_actual_step=actual_step)
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
    assert [item for item in calls if item[0].startswith("node.tool_")] == [
        ("node.tool_started", None),
        ("node.tool_finished", "succeeded"),
    ]
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


@pytest.mark.asyncio
async def test_post_reservation_model_authorization_denial_settles_skipped_without_dispatch():
    from tap.modules.ai.domain.models import ModelOperation, ModelRequest, text_digest
    from tap.modules.workflows.adapters.native_runtime import JournaledModelGateway
    from tap.modules.workflows.domain.commands import WorkflowDispatchBlocked

    class Store:
        def __init__(self):
            self.settled = []

        async def graph_lineage(self, scope, task_id, node_id, attempt):
            return "run-1", 1

        async def action_result(self, scope, action_id):
            return self.settled[-1] if self.settled else None

        async def task_request(self, scope, task_id):
            return {}

        async def reserve_action(self, *args, **kwargs):
            return "reserved"

        async def settle_action(self, scope, task_id, token, action_id, result, **kwargs):
            self.settled.append(result)
            return result

    class Gateway:
        async def chat(self, request):
            raise AssertionError("authorization-denied model must not dispatch")

    checks = 0

    async def authorize():
        nonlocal checks
        checks += 1
        return checks == 1

    store = Store()
    model = JournaledModelGateway(
        Gateway(), store, "task-1", "token-1", "node-1", 1, authorize=authorize
    )
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
    with pytest.raises(PermissionError, match="authorized"):
        await model.chat(request)
    assert checks == 2
    assert store.settled == [{"skipped": "conversation dispatch authorization revoked"}]
    with pytest.raises(WorkflowDispatchBlocked, match="without dispatch"):
        await model.chat(request)


@pytest.mark.asyncio
async def test_post_reservation_knowledge_authorization_denial_settles_skipped_without_search():
    from tap.modules.workflows.adapters.native_runtime import JournaledKnowledgeSearch
    from tap.modules.workflows.domain.commands import WorkflowDispatchBlocked

    class Store:
        def __init__(self):
            self.settled = []

        async def task_request(self, scope, task_id):
            return {
                "resolved_resources": [
                    {
                        "source_id": "source-1",
                        "revision_id": "rev-1",
                        "source_content_hash": "sha256:" + "a" * 64,
                    }
                ]
            }

        async def graph_lineage(self, scope, task_id, node_id, attempt):
            return "run-1", 1

        async def action_result(self, scope, action_id):
            return self.settled[-1] if self.settled else None

        async def reserve_action(self, *args, **kwargs):
            return "reserved"

        async def settle_action(self, scope, task_id, token, action_id, result, **kwargs):
            self.settled.append(result)
            return result

    class Knowledge:
        async def search(self, request, policy):
            raise AssertionError("authorization-denied search must not dispatch")

    async def policy_for(resources):
        return "policy"

    checks = 0

    async def authorize():
        nonlocal checks
        checks += 1
        return checks in (1, 3)

    store = Store()
    tool = JournaledKnowledgeSearch(store, Knowledge(), policy_for)
    with pytest.raises(PermissionError, match="authorized"):
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
    assert checks == 2
    assert store.settled == [{"skipped": "conversation dispatch authorization revoked"}]
    with pytest.raises(WorkflowDispatchBlocked, match="without dispatch"):
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
