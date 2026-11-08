import pytest

from tap.modules.workflows.application.browser_steps import BrowserProgress


@pytest.mark.asyncio
async def test_plan_is_committed_before_any_action_and_failures_keep_step_identity():
    events = []
    calls = []

    async def emit(kind, payload):
        events.append((kind, payload))

    async def execute(action):
        calls.append(action)
        return {
            "status": "completed",
            "observation": {
                "url": "https://example.com/quote?token=private",
                "title": "Quote",
                "bounded_snapshot": "Choose cover",
                "error": "strict mode violation: 64 elements",
            },
        }

    progress = BrowserProgress(emit)
    assert not progress.has_page_observation
    denied = await progress.handle(
        {"type": "click", "selector": "text=Age", "step": "cover"}, "a0", execute
    )
    assert "plan" in denied["error"]
    assert not calls
    plan = [
        {
            "id": "cover",
            "keyword": "When",
            "title": "Choose cover",
            "expected_result": "Requested cover is selected",
        }
    ]
    assert (await progress.handle({"type": "plan", "steps": plan}, "p1", execute))[
        "status"
    ] == "planned"
    result = await progress.handle(
        {
            "type": "fill",
            "selector": "text=Age",
            "value": "private-personal-value",
            "step": "cover",
        },
        "a1",
        execute,
    )
    assert progress.has_page_observation
    assert result["error"].startswith("strict mode")
    assert events[0][0] == "browser.plan_created"
    assert [e[0] for e in events[1:]] == ["browser.action_started", "browser.action_finished"]
    details = events[-1][1]
    assert details["step_id"] == "cover" and details["outcome"] == "failed"
    assert details["url"] == "https://example.com/quote"
    assert details["input"] == "[hidden]"
    assert details["duration_ms"] >= 0
    assert "private-personal-value" not in str(events)
    assert calls[0] == {"type": "fill", "selector": "text=Age", "value": "private-personal-value"}


@pytest.mark.asyncio
async def test_plan_revision_cannot_rewrite_steps_already_executed():
    events = []

    async def emit(kind, payload):
        events.append((kind, payload))

    async def execute(action):
        return {"status": "completed", "observation": {"error": None}}

    p = BrowserProgress(emit)
    steps = [{"id": "open", "keyword": "Given", "title": "The catalog is available"}]
    await p.handle({"type": "plan", "steps": steps}, "p1", execute)
    await p.handle(
        {"type": "navigate", "url": "https://example.com", "step": "open"}, "a1", execute
    )
    result = await p.handle(
        {
            "type": "plan",
            "steps": [{**steps[0], "title": "Pretend completed"}],
            "reason": "new understanding",
        },
        "p2",
        execute,
    )
    assert "executed" in result["error"]
    assert len([e for e in events if e[0] == "browser.plan_created"]) == 1
