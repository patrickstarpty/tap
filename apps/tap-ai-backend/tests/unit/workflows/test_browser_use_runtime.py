from types import SimpleNamespace

import pytest
from browser_use.llm.messages import SystemMessage, UserMessage
from pydantic import BaseModel

from tap.modules.access.adapters.validation import VALIDATION_SCOPE
from tap.modules.ai.domain.models import ModelToolCall
from tap.modules.workflows.adapters.browser_use_runtime import GatewayChatModel


def test_repeated_failed_browser_action_stops_even_with_interleaved_step_changes():
    from browser_use.agent.views import ActionResult

    from tap.modules.workflows.adapters.browser_use_runtime import ActionFailureLimit

    guard = ActionFailureLimit()
    action = {"tap_verify": {"index": 6171, "attribute": "text", "name": "premium"}}
    failure = ActionResult(error="Expected DOM value was not observed")
    for attempt in range(3):
        result = guard.apply(action, failure)
        assert result.is_done is (attempt == 2)
        guard.apply({"tap_step": {"step": "quote"}}, ActionResult())
    assert result.success is False
    assert "Repeated browser action failed 3 times" in result.error


def test_successful_browser_action_clears_its_failure_count():
    from browser_use.agent.views import ActionResult

    from tap.modules.workflows.adapters.browser_use_runtime import ActionFailureLimit

    guard = ActionFailureLimit()
    action = {"click": {"index": 10}}
    failure = ActionResult(error="Element unavailable")
    guard.apply(action, failure)
    guard.apply(action, failure)
    guard.apply(action, ActionResult())
    assert not guard.apply(action, failure).is_done


@pytest.mark.asyncio
@pytest.mark.parametrize("response_format", ["tool", "json"])
async def test_browser_use_decisions_use_the_existing_budget_gateway(response_format):
    requests = []

    class Output(BaseModel):
        action: str

    class Gateway:
        async def chat(self, request):
            requests.append(request)
            return SimpleNamespace(
                tool_calls=(ModelToolCall("call", "browser_step", '{"action":"navigate"}'),)
                if response_format == "tool"
                else (),
                output='{"action":"navigate"}',
            )

    async def bounded(work, **kwargs):
        return await work

    llm = GatewayChatModel(
        Gateway(), bounded, VALIDATION_SCOPE, "qwen-plus", "task:run", lambda: 30, 2048
    )
    response = await llm.ainvoke(
        [
            SystemMessage(content="Browser instructions"),
            UserMessage(content="Read the quote\n[12]<button>Quote</button>"),
        ],
        Output,
    )
    assert response.completion.action == "navigate"
    assert requests[0].tools[0].parameters == Output.model_json_schema()
    assert requests[0].context == "Read the quote\n[12]<button>Quote</button>"
    assert requests[0].allow_retries is False
    assert requests[0].timeout_seconds == 15
    assert requests[0].idempotency_key == "task:run:browser-use:1"


class ClickParameters(BaseModel):
    index: int


class ClickDecision(BaseModel):
    click: ClickParameters


class BrowserDecision(BaseModel):
    action: list[ClickDecision]


class DecisionGateway:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        return SimpleNamespace(
            tool_calls=(ModelToolCall("call", "browser_step", next(self.outputs)),),
            output="",
        )


def decision_model(gateway):
    async def bounded(work, **kwargs):
        return await work

    return GatewayChatModel(
        gateway, bounded, VALIDATION_SCOPE, "qwen3.8-max", "task:run", lambda: 30, 2048
    )


@pytest.mark.asyncio
async def test_browser_request_preserves_roles_and_requires_named_function():
    from browser_use.llm.messages import AssistantMessage

    gateway = DecisionGateway(['{"action":[{"click":{"index":12}}]}'])
    model = decision_model(gateway)
    await model.ainvoke(
        [
            SystemMessage(content="Browser instructions"),
            UserMessage(content="Open quote"),
            AssistantMessage(content='{"action":[{"click":{"index":10}}]}'),
            UserMessage(content="Previous click failed; current element index is 12"),
        ],
        BrowserDecision,
    )
    req = gateway.requests[0]
    assert getattr(req, "tool_choice", None) == "browser_step"
    assert req.context == "Open quote"
    assert [(m.role, m.content) for m in req.messages] == [
        ("assistant", '{"action":[{"click":{"index":10}}]}'),
        ("user", "Previous click failed; current element index is 12"),
    ]
    assert req.tools[0].parameters["properties"]["action"]["maxItems"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        '{"action":[{"click":{"click":{"index":3689}}}]}',
        '{"action":[{"click":{"index":1}},{"click":{"index":2}}]}',
        '{"action":[{"click":{}}]}',
        "not json",
    ],
)
async def test_invalid_decision_gets_one_concise_metered_correction(bad):
    gateway = DecisionGateway([bad, '{"action":[{"click":{"index":17}}]}'])
    model = decision_model(gateway)
    response = await model.ainvoke([UserMessage(content="Click observed quote")], BrowserDecision)
    assert response.completion.action[0].click.index == 17
    assert len(gateway.requests) == 2
    assert len({r.idempotency_key for r in gateway.requests}) == 2
    correction = gateway.requests[1].messages[-1]
    assert correction.role == "user"
    assert len(correction.content) < 700
    assert all(r.allow_retries is False for r in gateway.requests)


@pytest.mark.asyncio
async def test_repeated_invalid_decision_stops_without_more_model_calls():
    bad = '{"action":[{"click":{"click":{"index":3689}}}]}'
    gateway = DecisionGateway([bad, bad])
    model = decision_model(gateway)
    with pytest.raises(ValueError, match="format"):
        await model.ainvoke([UserMessage(content="Click quote")], BrowserDecision)
    assert model.format_error
    with pytest.raises(ValueError, match="format"):
        await model.ainvoke([UserMessage(content="Try again")], BrowserDecision)
    assert len(gateway.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["ordered", "future", "old", "native_write", "review_rejected", "unknown", "drift"]
)
async def test_contract_runtime_guards_actions_before_dispatch(monkeypatch, mode):
    import json

    import browser_use
    from browser_use.agent.views import ActionResult

    from tap.modules.workflows.adapters.browser_use_runtime import BrowserUseRuntime
    from tap.modules.workflows.adapters.opencode_runtime import CodingConfig
    from tap.modules.workflows.application.direct_browser import DirectBrowserLoop

    executed, offered, events = [], [], []
    plan = {
        "requirements": [
            {"id": "premium", "kind": "result", "source": "premium", "expected": "positive"}
        ],
        "steps": [
            {"id": "open", "keyword": "Given", "title": "Open", "operation": "navigate"},
            {
                "id": "show",
                "keyword": "When",
                "title": "Show quote",
                "operation": "click",
                "target_texts": ["Get quote"],
            },
            {
                "id": "premium",
                "keyword": "Then",
                "title": "Read premium",
                "operation": "assert",
                "requirement": "premium",
                "target_texts": ["Premium"],
                "assertion": {"kind": "positive"},
            },
        ],
    }

    class Browser:
        def __init__(self, **kwargs):
            pass

        async def stop(self):
            pass

        async def get_dom_element_by_index(self, index):
            return SimpleNamespace(attributes={"id": "show"}, xpath="/button")

    async def sdk_action(self, action, browser_session, *args, **kwargs):
        name, params = next(iter(action.model_dump().items()))
        if name.startswith("tap_"):
            registered = self.registry.registry.actions[name]
            return await registered.function(params=registered.param_model(**params))
        return ActionResult(extracted_content="observed")

    class Agent:
        def __init__(self, *, tools, browser, llm, **kwargs):
            self.tools, self.browser, self.progress = tools, browser, llm.progress
            offered.extend(tools.registry.registry.actions)

        async def act(self, name, params):
            return await self.tools.act(
                SimpleNamespace(model_dump=lambda **kwargs: {name: params}), self.browser
            )

        async def run(self, **kwargs):
            state = self.progress()
            assert state["active_step"]["id"] == "show"
            target = {"step": "show", "plan_digest": state["plan_digest"], "index": 1}
            if mode == "native_write":
                assert "click" not in offered and "navigate" not in offered
                result = await self.act("click", {"index": 1})
                assert result.error
            elif mode in {"future", "old"}:
                target["step" if mode == "future" else "plan_digest"] = (
                    "premium" if mode == "future" else "old"
                )
                result = await self.act("tap_execute", target)
                assert result.error
            else:
                result = await self.act("tap_execute", target)
                if mode == "unknown":
                    assert result.error and result.is_done
                else:
                    assert not result.error
                    assert self.progress()["active_step"]["id"] == "premium"
                    target = {
                        "step": "premium",
                        "plan_digest": state["plan_digest"],
                        "selector": "#premium",
                    }
                    result = await self.act("tap_execute", target)
                    assert not result.error
                    result = await self.act("done", {"text": "Done", "success": True})
                    assert bool(result.success) is (mode != "drift")
                    return SimpleNamespace(is_successful=lambda: mode != "drift")
            assert len(executed) == (2 if mode == "unknown" else 1)
            return SimpleNamespace(is_successful=lambda: False)

    class Gateway:
        calls = 0

        async def chat(self, request):
            self.calls += 1
            payload = (
                plan
                if self.calls == 1
                else {
                    "approved": mode != "review_rejected",
                    "issues": ["missing input"] if mode == "review_rejected" else [],
                }
            )
            return SimpleNamespace(tool_calls=(), output=json.dumps(payload))

    async def execute(action):
        executed.append(action)
        obs = {"url": "https://example.com", "bounded_snapshot": "Quote"}
        if action["type"] == "read":
            obs["extracted"] = {
                "name": "premium",
                "value": "0.00"
                if mode == "drift" and sum(a["type"] == "read" for a in executed) > 1
                else "HK$12.50/month",
            }
        return {
            "status": "unknown" if mode == "unknown" and action["type"] == "click" else "completed",
            "observation": obs,
        }

    async def emit(*args):
        events.append(args)

    async def bounded(work, **kwargs):
        return await work

    async def stop():
        return False

    monkeypatch.setattr(browser_use, "Browser", Browser)
    monkeypatch.setattr(browser_use, "Agent", Agent)
    monkeypatch.setattr(browser_use.Tools, "act", sdk_action)
    runtime = BrowserUseRuntime(CodingConfig.for_test(), store=None, gateway_factory=None)
    loop = DirectBrowserLoop(None, execute, emit, max_rounds=8)
    context = {
        "request": {"goal": "Read premium", "browser": {"start_url": "https://example.com"}},
        "execute": execute,
        "session": {"observation": {"cdp_url": "http://127.0.0.1:9222"}},
        "gateway": Gateway(),
        "bounded": bounded,
        "scope": VALIDATION_SCOPE,
        "task_id": "t",
        "node_id": "n",
        "run_id": "r",
        "seconds_left": lambda: 30,
        "remaining_calls": 8,
        "stop_requested": stop,
    }
    if mode == "review_rejected":
        with pytest.raises(ValueError, match="review"):
            await runtime._run_agent(loop, context)
        assert executed == []
    else:
        result = await runtime._run_agent(loop, context)
        assert result.status == ("passed" if mode == "ordered" else "failed")
        assert events[0][1]["plan_digest"]
        assert events[0][1]["contract"]["requirements"][0]["id"] == "premium"
        if mode == "ordered":
            assert [a["type"] for a in result.actions] == ["navigate", "click", "read"]
            assert result.outputs == {"premium": "HK$12.50/month"}
