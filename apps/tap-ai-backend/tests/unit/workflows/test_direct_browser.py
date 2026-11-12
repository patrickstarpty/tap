import pytest

from tap.modules.workflows.application.direct_browser import DirectBrowserLoop, compile_replay


@pytest.mark.asyncio
async def test_browser_loop_uses_observed_controls_and_returns_dom_values():
    events, actions, offered = [], [], []
    decisions = iter(
        [
            {
                "type": "plan",
                "steps": [
                    {"id": "open", "keyword": "Given", "title": "Open site"},
                    {"id": "quote", "keyword": "Then", "title": "Read quote"},
                ],
            },
            {"type": "navigate", "url": "https://example.com", "step": "open"},
            {
                "type": "read",
                "target": "1:0",
                "name": "quote",
                "attribute": "text",
                "contains": "/month",
                "step": "quote",
            },
            {"type": "finish"},
        ]
    )

    async def decide(messages, tools):
        offered.extend(tools)
        return next(decisions)

    async def execute(action):
        actions.append(action)
        observation = {
            "sequence": len(actions),
            "url": "https://example.com",
            "bounded_snapshot": "Quote",
            "fields": [
                {"selector": "#quote", "tag": "output", "label": "Quote", "readonly": False}
            ],
        }
        if action["type"] == "read":
            observation["extracted"] = {"name": "quote", "value": "HK$10.62/month"}
        return {"status": "completed", "observation": observation}

    async def emit(kind, payload):
        events.append((kind, payload))

    result = await DirectBrowserLoop(decide, execute, emit, max_rounds=8).run(
        "Get quote", "https://example.com"
    )
    assert result.status == "passed"
    assert result.outputs == {"quote": "HK$10.62/month"}
    assert actions[1]["selector"] == "#quote"
    assert events[0][0] == "browser.plan_created"
    assert all(t["function"]["name"] == "browser_step" for t in offered)
    before = len(actions)
    script = compile_replay(result)
    assert len(actions) == before
    assert "toContainText" in script and "testInfo.attach" in script
    assert "HK$10.62/month" not in script  # read anew; never freeze a quote constant


@pytest.mark.asyncio
async def test_loop_rejects_stale_refs_readonly_fill_and_fabricated_finish():
    messages_seen, actions = [], []
    decisions = iter(
        [
            {"type": "plan", "steps": [{"id": "open", "keyword": "Given", "title": "Open"}]},
            {"type": "navigate", "url": "https://example.com", "step": "open"},
            {"type": "fill", "target": "0:0", "value": "30", "step": "open"},
            {"type": "fill", "target": "1:0", "value": "30", "step": "open"},
            {"type": "finish", "outputs": {"quote": "invented"}},
        ]
    )

    async def decide(messages, tools):
        messages_seen.append(str(messages))
        return next(decisions)

    async def execute(action):
        actions.append(action)
        return {
            "status": "completed",
            "observation": {
                "sequence": 1,
                "url": "https://example.com",
                "bounded_snapshot": "Age",
                "fields": [{"selector": "#age", "readonly": True, "role": "combobox"}],
            },
        }

    async def emit(*args):
        pass

    result = await DirectBrowserLoop(decide, execute, emit, max_rounds=5).run(
        "Quote", "https://example.com"
    )
    assert len(actions) == 1
    assert result.status == "failed"
    assert "stale" in messages_seen[3]
    assert "readonly" in messages_seen[4]
    assert result.outputs == {}


@pytest.mark.asyncio
async def test_missing_input_blocks_without_inventing_a_site_failure():
    async def decide(messages, tools):
        return {"type": "request_input", "reason": "missing_input", "question": "Which account?"}

    async def execute(action):
        raise AssertionError("no browser action needed")

    async def emit(*args):
        pass

    result = await DirectBrowserLoop(decide, execute, emit, max_rounds=1).run(
        "Review account", "https://example.com"
    )
    assert result.status == "blocked"
    assert result.input_request == {"reason": "missing_input", "question": "Which account?"}


@pytest.mark.asyncio
async def test_failed_read_cannot_finish_using_an_older_value():
    calls = 0

    async def execute(action):
        nonlocal calls
        calls += 1
        observation = {
            "sequence": calls,
            "url": "https://example.com",
            "bounded_snapshot": "Quote",
            "fields": [{"selector": "#quote"}],
        }
        if action["type"] == "read" and calls == 2:
            observation["extracted"] = {"name": "quote", "value": "old"}
        return {"status": "completed", "observation": observation}

    async def emit(*args):
        pass

    async def decide(*args):
        raise AssertionError()

    loop = DirectBrowserLoop(decide, execute, emit, max_rounds=1)
    await loop._step(
        {
            "type": "plan",
            "steps": [{"id": "verify", "keyword": "Then", "title": "Verify actual quote"}],
        },
        "p",
    )
    await loop._step({"type": "navigate", "url": "https://example.com", "step": "verify"}, "n")
    await loop._step({"type": "read", "target": "1:0", "name": "quote", "step": "verify"}, "r1")
    with pytest.raises(ValueError, match="DOM fact"):
        await loop._step({"type": "read", "target": "2:0", "name": "quote", "step": "verify"}, "r2")
    with pytest.raises(ValueError):
        await loop._step({"type": "finish"}, "f")
    assert "quote" not in loop.outputs


@pytest.mark.asyncio
async def test_next_decision_keeps_verified_values_and_remaining_business_steps():
    seen = []
    decisions = iter(
        [
            {
                "type": "plan",
                "steps": [
                    {"id": "open", "keyword": "Given", "title": "Open"},
                    {"id": "age", "keyword": "Then", "title": "Verify age"},
                    {"id": "quote", "keyword": "And", "title": "Read quote"},
                ],
            },
            {"type": "navigate", "url": "https://example.com", "step": "open"},
            {
                "type": "read",
                "target": "1:0",
                "name": "age",
                "attribute": "value",
                "equals": "30",
                "step": "age",
            },
            {"type": "read", "target": "2:1", "name": "quote", "step": "quote"},
            {"type": "finish"},
        ]
    )

    async def decide(messages, tools):
        seen.append(messages[-1]["content"])
        return next(decisions)

    count = 0

    async def execute(action):
        nonlocal count
        count += 1
        value = {
            "sequence": count,
            "url": "https://example.com",
            "bounded_snapshot": "Quote",
            "fields": [{"selector": "#age"}, {"selector": "#quote"}],
        }
        if action["type"] == "read":
            value["extracted"] = {
                "name": action["name"],
                "value": "30" if action["name"] == "age" else "12/month",
            }
        return {"status": "completed", "observation": value}

    async def emit(*args):
        pass

    result = await DirectBrowserLoop(decide, execute, emit, max_rounds=5).run(
        "Quote", "https://example.com"
    )
    assert result.status == "passed"
    import json

    state = json.loads(seen[3].split(": ", 1)[1])["progress"]
    assert state["verified_outputs"] == {"age": "30"}
    assert state["completed_step_ids"] == ["age", "open"]
    assert [s["id"] for s in state["remaining_steps"]] == ["quote"]


@pytest.mark.asyncio
async def test_opening_a_combobox_does_not_complete_the_fill_step():
    async def emit(*args):
        pass

    async def decide(*args):
        raise AssertionError()

    async def execute(action):
        return {
            "status": "completed",
            "observation": {
                "sequence": 1,
                "url": "https://example.com",
                "bounded_snapshot": "Age",
                "fields": [
                    {"selector": "#age", "role": "combobox", "readonly": True, "value": "25"}
                ],
            },
        }

    loop = DirectBrowserLoop(decide, execute, emit, max_rounds=1)
    await loop._step(
        {
            "type": "plan",
            "steps": [
                {"id": "open", "keyword": "Given", "title": "Open"},
                {"id": "age", "keyword": "When", "title": "Set age to 30"},
            ],
        },
        "p",
    )
    await loop._step({"type": "navigate", "url": "https://example.com", "step": "open"}, "n")
    await loop._step({"type": "click", "target": "1:0", "step": "age"}, "c")
    assert "age" not in loop.completed


def replay_result(action):
    from tap.modules.workflows.application.direct_browser import DirectBrowserResult

    return DirectBrowserResult(
        "passed",
        actions=[action],
        steps={"choose": {"keyword": "When", "title": "Choose option"}},
    )


@pytest.mark.parametrize(
    "selector", ["aria-ref=e1", "aria-ref=f2e3", "button[ref='e1']", 'combobox[ref="f1e2"]']
)
def test_replay_rejects_session_local_locators(selector):
    result = replay_result({"type": "click", "step": "choose", "selector": selector})
    with pytest.raises(ValueError, match="session-local"):
        compile_replay(result)


@pytest.mark.parametrize("guard", [None, [], "Product", [""], [123]])
def test_replay_never_guesses_native_select_semantics_without_target_guard(guard):
    action = {"type": "select", "step": "choose", "selector": "#product", "value": "Standard"}
    if guard is not None:
        action["target_texts"] = guard
    with pytest.raises(ValueError, match="select requires"):
        compile_replay(replay_result(action))


def test_replay_guarded_select_preserves_semantics_and_observation_transition():
    result = replay_result(
        {
            "type": "select",
            "step": "choose",
            "selector": "#product",
            "value": "Standard",
            "target_texts": ["Product"],
            "scope_text": "Quote",
        }
    )
    result.actions.insert(0, {"type": "snapshot"})
    result.actions.insert(
        0, {"type": "navigate", "step": "choose", "url": "https://example.test/requested"}
    )
    script = compile_replay(result)
    assert 'await selectValue(page, {"type": "select"' in script
    assert 'await page.locator("#product").selectOption(' not in script
    assert 'await page.goto("https://example.test/requested"' in script
    assert '"scope_text": "Quote"' in script
    assert "documents, documents, null" in script
