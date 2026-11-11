import hashlib

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


@pytest.mark.asyncio
async def test_live_completion_requires_current_then_read_receipts():
    async def emit(*_args):
        pass

    async def execute(action):
        return {
            "status": "completed",
            "action_id": "actual-a1",
            "observation": {
                "sequence": 4,
                "url": "https://example.com/quote",
                "error": None,
                "extracted": {"name": "quote", "value": "42"} if action["type"] == "read" else None,
            },
        }

    p = BrowserProgress(emit)
    await p.handle(
        {
            "type": "plan",
            "steps": [
                {"id": "enter", "keyword": "When", "title": "Enter requested inputs"},
                {"id": "quote", "keyword": "Then", "title": "Read a positive quote"},
            ],
        },
        "plan",
        execute,
    )
    assert not p.live_complete
    read = {
        "type": "read",
        "selector": "#quote",
        "name": "quote",
        "positive": True,
        "step": "quote",
    }
    result = await p.handle(read, "read1", execute)
    assert result["action_id"] == "actual-a1"
    assert p.live_complete
    await p.handle(
        {"type": "fill", "selector": "#age", "value": "31", "step": "enter"}, "write", execute
    )
    assert not p.live_complete
    await p.handle(read, "read2", execute)
    assert p.live_complete


@pytest.mark.asyncio
async def test_verified_form_input_survives_navigation_to_quote_result():
    async def emit(*_args):
        pass

    page = "https://example.com/form"
    sequence = 0

    async def execute(action):
        nonlocal page, sequence
        sequence += 1
        if action["type"] == "click":
            page = "https://example.com/result"
        return {
            "status": "completed",
            "action_id": f"receipt-{sequence}",
            "observation": {
                "url": page,
                "sequence": sequence,
                "error": None,
                "extracted": (
                    {
                        "name": action["name"],
                        "value": "30" if action["name"] == "age" else "HK$12.50",
                    }
                    if action["type"] == "read"
                    else None
                ),
            },
        }

    progress = BrowserProgress(emit)
    assert (
        await progress.handle(
            {
                "type": "plan",
                "steps": [
                    {"id": "enter", "keyword": "When", "title": "Enter age"},
                    {
                        "id": "verify_age",
                        "keyword": "Then",
                        "title": "Verify age",
                        "verification_kind": "input",
                    },
                    {"id": "calculate", "keyword": "When", "title": "Calculate"},
                    {"id": "quote", "keyword": "Then", "title": "Verify quote"},
                ],
            },
            "plan",
            execute,
        )
    )["status"] == "planned"
    await progress.handle(
        {"type": "fill", "step": "enter", "selector": "#age", "value": "30"}, "fill", execute
    )
    await progress.handle(
        {"type": "read", "step": "verify_age", "selector": "#age", "name": "age", "equals": "30"},
        "age",
        execute,
    )
    assert not progress.live_complete
    await progress.handle(
        {"type": "click", "step": "calculate", "selector": "#calculate"}, "click", execute
    )
    await progress.handle(
        {"type": "read", "step": "quote", "selector": "#quote", "name": "quote", "positive": True},
        "quote",
        execute,
    )
    assert progress.live_complete
    assert progress.input_proofs["verify_age"] == {
        "source_url": "https://example.com/form",
        "source_url_sha256": hashlib.sha256(b"https://example.com/form").hexdigest(),
        "receipt_id": "receipt-2",
        "observed_value": "30",
    }
    assert set(progress.live_checks) == {"quote"}


@pytest.mark.asyncio
async def test_input_proof_requires_exact_observed_value_and_receipt_provenance():
    async def emit(*_args):
        pass

    async def execute(action):
        return {
            "status": "completed",
            "action_id": "read-receipt",
            "observation": {
                "url": "https://example.com/form?secret=1",
                "error": None,
                "extracted": {"name": "age", "value": "30"},
            },
        }

    progress = BrowserProgress(emit)
    steps = [
        {"id": "age", "keyword": "Then", "title": "Age is 30", "verification_kind": "input"},
        {"id": "result", "keyword": "Then", "title": "Show result"},
    ]
    assert (await progress.handle({"type": "plan", "steps": steps}, "plan", execute))[
        "status"
    ] == "planned"
    for action in (
        {"type": "read", "step": "age", "selector": "#age", "name": "age", "contains": "30"},
        {"type": "read", "step": "age", "selector": "#age", "name": "age", "equals": "31"},
    ):
        await progress.handle(action, "read", execute)
        assert "age" not in progress.input_proofs
    await progress.handle(
        {"type": "read", "step": "age", "selector": "#age", "name": "age", "equals": "30"},
        "read",
        execute,
    )
    assert progress.input_proofs["age"] == {
        "source_url": "https://example.com/form",
        "source_url_sha256": hashlib.sha256(b"https://example.com/form?secret=1").hexdigest(),
        "receipt_id": "read-receipt",
        "observed_value": "30",
    }
    assert not progress.live_complete  # An input proof alone cannot establish a result.
    assert (
        await progress.handle(
            {
                "type": "plan",
                "steps": [{**steps[0], "verification_kind": "result"}, steps[1]],
                "reason": "rewrite",
            },
            "revision",
            execute,
        )
    )["error"].endswith("executed steps")


@pytest.mark.asyncio
async def test_input_proof_is_invalidated_by_same_page_write_and_revisit():
    async def emit(*_args):
        pass

    page = "https://example.com/form"

    async def execute(action):
        nonlocal page
        if action["type"] == "navigate":
            page = action["url"]
        return {
            "status": "completed",
            "action_id": "receipt",
            "observation": {
                "url": page,
                "error": None,
                "extracted": {"name": "age", "value": "30"} if action["type"] == "read" else None,
            },
        }

    progress = BrowserProgress(emit)
    await progress.handle(
        {
            "type": "plan",
            "steps": [
                {"id": "age", "keyword": "Then", "title": "Age", "verification_kind": "input"},
                {"id": "act", "keyword": "When", "title": "Act"},
                {"id": "result", "keyword": "Then", "title": "Result"},
            ],
        },
        "plan",
        execute,
    )
    read = {"type": "read", "step": "age", "selector": "#age", "name": "age", "equals": "30"}
    await progress.handle(read, "read1", execute)
    assert "age" in progress.input_proofs
    await progress.handle(
        {"type": "fill", "step": "act", "selector": "#age", "value": "31"}, "write", execute
    )
    assert "age" not in progress.input_proofs
    await progress.handle(read, "read2", execute)
    await progress.handle(
        {"type": "navigate", "step": "act", "url": "https://example.com/result"}, "leave", execute
    )
    assert "age" in progress.input_proofs
    await progress.handle(
        {"type": "navigate", "step": "act", "url": "https://example.com/form"}, "return", execute
    )
    assert "age" not in progress.input_proofs


@pytest.mark.asyncio
async def test_result_check_is_invalidated_by_navigation_and_unknown_outcome_clears_all_proofs():
    async def emit(*_args):
        pass

    page = "https://example.com/form"
    unknown = False

    async def execute(action):
        nonlocal page
        if action["type"] == "navigate":
            page = action["url"]
        return {
            "status": "unknown" if unknown else "completed",
            "action_id": "receipt",
            "observation": {
                "url": page,
                "error": None,
                "extracted": {"name": action.get("name"), "value": "30"}
                if action["type"] == "read"
                else None,
            },
        }

    progress = BrowserProgress(emit)
    await progress.handle(
        {
            "type": "plan",
            "steps": [
                {"id": "age", "keyword": "Then", "title": "Age", "verification_kind": "input"},
                {"id": "result", "keyword": "Then", "title": "Result"},
                {"id": "act", "keyword": "When", "title": "Act"},
            ],
        },
        "plan",
        execute,
    )
    await progress.handle(
        {"type": "read", "step": "age", "selector": "#age", "name": "age", "equals": "30"},
        "age",
        execute,
    )
    await progress.handle(
        {"type": "read", "step": "result", "selector": "#result", "name": "result", "equals": "30"},
        "result",
        execute,
    )
    assert progress.live_complete
    await progress.handle(
        {"type": "navigate", "step": "act", "url": "https://example.com/result"}, "nav", execute
    )
    assert not progress.live_complete
    assert "age" in progress.input_proofs and not progress.live_checks
    unknown = True
    await progress.handle({"type": "click", "step": "act", "selector": "#go"}, "unknown", execute)
    assert progress.unknown_action
    assert not progress.input_proofs and not progress.live_checks


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["input", [], {}])
async def test_verification_kind_is_then_only(kind):
    async def emit(*_args):
        pass

    async def execute(_action):
        raise AssertionError("plan validation must reject before browser action")

    progress = BrowserProgress(emit)
    result = await progress.handle(
        {
            "type": "plan",
            "steps": [
                {"id": "enter", "keyword": "When", "title": "Enter", "verification_kind": kind},
            ],
        },
        "plan",
        execute,
    )
    assert "error" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [[], {}])
async def test_malformed_then_verification_kind_is_rejected_without_crash(kind):
    async def emit(*_args):
        pass

    progress = BrowserProgress(emit)
    result = await progress.handle(
        {
            "type": "plan",
            "steps": [
                {"id": "verify", "keyword": "Then", "title": "Verify", "verification_kind": kind},
            ],
        },
        "plan",
        lambda _action: None,
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_plan_error_identifies_and_result_verification_and_correction():
    async def emit(*_args):
        raise AssertionError("invalid plan must not be emitted")

    async def execute(_action):
        raise AssertionError("invalid plan must not execute")

    progress = BrowserProgress(emit)
    result = await progress.handle(
        {
            "type": "plan",
            "steps": [
                {"id": "step_1", "keyword": "Given", "title": "Open product page"},
                {"id": "step_2", "keyword": "When", "title": "Select product"},
                {"id": "step_3", "keyword": "And", "title": "Fill form"},
                {
                    "id": "step_4",
                    "keyword": "Then",
                    "title": "Verify inputs",
                    "verification_kind": "input",
                },
                {
                    "id": "step_5",
                    "keyword": "And",
                    "title": "Verify premium",
                    "verification_kind": "result",
                },
            ],
        },
        "plan",
        execute,
    )
    assert result == {
        "error": "BDD plan step 5 (id step_5): verification_kind is only allowed on Then steps; "
        "change keyword to Then or remove verification_kind"
    }
    invalid = await progress.handle(
        {
            "type": "plan",
            "steps": [
                {
                    "id": "verify",
                    "keyword": "Then",
                    "title": "Verify",
                    "verification_kind": "skip",
                }
            ],
        },
        "plan",
        execute,
    )
    assert invalid == {
        "error": "BDD plan step 1 (id verify): verification_kind must be 'input' or 'result'"
    }


@pytest.mark.asyncio
async def test_input_proof_tracks_full_url_identity_without_exposing_query():
    async def emit(*_args):
        pass

    page = "https://example.com/form?stage=first#age"

    async def execute(action):
        nonlocal page
        if action["type"] == "navigate":
            page = action["url"]
        return {
            "status": "completed",
            "action_id": "receipt",
            "observation": {
                "url": page,
                "error": None,
                "extracted": {"name": "age", "value": "30"} if action["type"] == "read" else None,
            },
        }

    progress = BrowserProgress(emit)
    await progress.handle(
        {
            "type": "plan",
            "steps": [
                {"id": "age", "keyword": "Then", "title": "Age", "verification_kind": "input"},
                {"id": "go", "keyword": "When", "title": "Go"},
                {"id": "result", "keyword": "Then", "title": "Result"},
            ],
        },
        "plan",
        execute,
    )
    await progress.handle(
        {"type": "read", "step": "age", "selector": "#age", "name": "age", "equals": "30"},
        "read",
        execute,
    )
    await progress.handle(
        {"type": "navigate", "step": "go", "url": "https://example.com/form?stage=second#result"},
        "leave",
        execute,
    )
    assert "age" in progress.input_proofs
    await progress.handle(
        {"type": "navigate", "step": "go", "url": "https://example.com/form?stage=first#age"},
        "return",
        execute,
    )
    assert "age" not in progress.input_proofs


@pytest.mark.asyncio
async def test_cross_step_read_name_collision_invalidates_departed_input_proof():
    async def emit(*_args):
        pass

    page = "https://example.com/form"

    async def execute(action):
        nonlocal page
        if action["type"] == "navigate":
            page = action["url"]
        return {
            "status": "completed",
            "action_id": "receipt",
            "observation": {
                "url": page,
                "error": None,
                "extracted": {
                    "name": action.get("name"),
                    "value": "30" if action.get("name") == "age" else "42",
                }
                if action["type"] == "read"
                else None,
            },
        }

    progress = BrowserProgress(emit)
    await progress.handle(
        {
            "type": "plan",
            "steps": [
                {"id": "act", "keyword": "When", "title": "Navigate"},
                {
                    "id": "age",
                    "keyword": "Then",
                    "title": "Verify age",
                    "verification_kind": "input",
                },
                {"id": "quote", "keyword": "Then", "title": "Verify quote"},
            ],
        },
        "plan",
        execute,
    )
    await progress.handle(
        {"type": "read", "step": "age", "selector": "#age", "name": "age", "equals": "30"},
        "age",
        execute,
    )
    await progress.handle(
        {"type": "navigate", "step": "act", "url": "https://example.com/result"},
        "nav",
        execute,
    )
    await progress.handle(
        {"type": "read", "step": "quote", "selector": "#quote", "name": "quote", "equals": "42"},
        "quote",
        execute,
    )
    assert progress.live_complete

    async def failed(_action):
        return {
            "status": "completed",
            "observation": {
                "url": page,
                "error": "#missing was not found",
            },
        }

    await progress.handle(
        {"type": "read", "step": "act", "selector": "#missing", "name": "age", "equals": "30"},
        "failed",
        failed,
    )
    assert "age" not in progress.input_proofs
    assert not progress.live_complete


@pytest.mark.asyncio
async def test_input_read_uses_field_source_when_page_moves_before_receipt_finishes():
    async def emit(*_args):
        pass

    async def execute(action):
        return {
            "status": "completed",
            "action_id": "receipt",
            "observation": {
                "url": "https://example.com/result",
                "error": None,
                "extracted": {
                    "name": action["name"],
                    "value": "30" if action["name"] == "age" else "42",
                    "source_url": "https://example.com/form"
                    if action["name"] == "age"
                    else "https://example.com/result",
                },
            },
        }

    progress = BrowserProgress(emit)
    await progress.handle(
        {
            "type": "plan",
            "steps": [
                {
                    "id": "age",
                    "keyword": "Then",
                    "title": "Verify age",
                    "verification_kind": "input",
                },
                {"id": "quote", "keyword": "Then", "title": "Verify quote"},
            ],
        },
        "plan",
        execute,
    )
    await progress.handle(
        {"type": "read", "step": "age", "selector": "#age", "name": "age", "equals": "30"},
        "age",
        execute,
    )
    await progress.handle(
        {"type": "read", "step": "quote", "selector": "#quote", "name": "quote", "equals": "42"},
        "quote",
        execute,
    )
    assert progress.input_proofs["age"]["source_url"] == "https://example.com/form"
    assert progress.live_complete
    assert [action["name"] for action, _is_input in progress.final_checks] == ["quote"]


@pytest.mark.asyncio
async def test_truncated_page_url_cannot_classify_input_proof_as_historical():
    async def emit(*_args):
        pass

    long_url = "https://example.com/form?state=" + "a" * 2100

    async def execute(action):
        return {
            "status": "completed",
            "action_id": "receipt",
            "observation": {
                "url": long_url[:2048],
                "error": None,
                "extracted": {"name": action["name"], "value": "30", "source_url": long_url},
            },
        }

    progress = BrowserProgress(emit)
    await progress.handle(
        {
            "type": "plan",
            "steps": [
                {"id": "age", "keyword": "Then", "title": "Age", "verification_kind": "input"},
                {"id": "quote", "keyword": "Then", "title": "Result"},
            ],
        },
        "plan",
        execute,
    )
    await progress.handle(
        {"type": "read", "step": "age", "selector": "#age", "name": "age", "equals": "30"},
        "read",
        execute,
    )
    assert "age" not in progress.input_proofs


@pytest.mark.asyncio
async def test_plain_read_or_failed_assertion_cannot_complete_live_task():
    async def emit(*_args):
        pass

    p = BrowserProgress(emit)

    async def execute(_action):
        return {
            "status": "completed",
            "observation": {"error": None, "extracted": {"name": "quote", "value": "0"}},
        }

    await p.handle(
        {"type": "plan", "steps": [{"id": "q", "keyword": "Then", "title": "Verify quote"}]},
        "p",
        execute,
    )
    await p.handle({"type": "read", "step": "q", "selector": "#q", "name": "quote"}, "r", execute)
    assert not p.live_complete

    async def failure(_action):
        return {
            "status": "completed",
            "observation": {"error": "Expected DOM value was not observed; actual: 0"},
        }

    await p.handle(
        {"type": "read", "step": "q", "selector": "#q", "name": "quote", "positive": True},
        "f",
        failure,
    )
    assert not p.live_complete


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        "locator.click: Target crashed\nCall log: ...",
        "page.goto: Page crashed",
        "browser page crashed",
    ],
)
async def test_crashed_page_latches_unknown_outcome_and_prevents_further_actions(error):
    calls = []

    async def emit(*_args):
        pass

    async def execute(action):
        calls.append(action)
        return {"status": "completed", "observation": {"error": error, "bounded_snapshot": ""}}

    progress = BrowserProgress(emit)
    await progress.handle(
        {"type": "plan", "steps": [{"id": "go", "keyword": "When", "title": "Continue"}]},
        "plan",
        execute,
    )
    receipt = await progress.handle(
        {"type": "click", "step": "go", "selector": "#go"}, "click", execute
    )
    assert receipt["error"] == error
    assert progress.unknown_action
    denied = await progress.handle(
        {"type": "navigate", "step": "go", "url": "https://example.com"}, "retry", execute
    )
    assert denied["status"] == "unknown"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,error", [("unknown", None), ("completed", "Target crashed")])
async def test_uncertain_action_is_preserved_in_timeline(status, error):
    events = []

    async def emit(kind, payload):
        events.append((kind, payload))

    async def execute(action):
        return {"status": status, "observation": {"error": error}}

    progress = BrowserProgress(emit)
    await progress.handle(
        {"type": "plan", "steps": [{"id": "go", "keyword": "When", "title": "Submit"}]},
        "plan",
        execute,
    )
    await progress.handle({"type": "click", "step": "go", "selector": "#submit"}, "go", execute)
    assert events[-1][0] == "browser.action_finished"
    assert events[-1][1]["outcome"] == "unknown"
