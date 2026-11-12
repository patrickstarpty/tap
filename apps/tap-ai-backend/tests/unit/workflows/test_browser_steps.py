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


@pytest.mark.asyncio
async def test_known_failed_action_keeps_original_error_and_current_observation():
    async def emit(*args):
        pass

    progress = BrowserProgress(emit)

    async def execute(action):
        return {
            "status": "failed",
            "observation": {
                "error": "invalid selector before write",
                "write_started": False,
                "url": "https://example.com",
                "bounded_snapshot": "Choose age",
                "fields": [{"selector": "#age"}],
            },
        }

    await progress.handle(
        {"type": "plan", "steps": [{"id": "choose", "keyword": "When", "title": "Choose"}]},
        "plan",
        execute,
    )
    result = await progress.handle(
        {"type": "fill", "step": "choose", "selector": "[", "value": "30"}, "bad", execute
    )
    assert result["error"] == "invalid selector before write"
    assert result["bounded_snapshot"] == "Choose age"
    assert not progress.unknown_action


_MISSING_TARGET_ERROR = (
    "no target matched before write; take a fresh snapshot and use its aria-ref, "
    "or wait explicitly for an observed stable selector"
)


def _target_receipt(*, status="failed", sequence=8, error=_MISSING_TARGET_ERROR, **observation):
    return {
        "status": status,
        "action_id": f"receipt-{sequence}",
        "observation": {
            "sequence": sequence,
            "url": "https://example.com/form",
            "bounded_snapshot": "Observed form",
            "write_started": False,
            "error": error,
            **observation,
        },
    }


async def _planned_target_progress(*receipts):
    calls = []
    remaining = list(receipts)

    async def emit(*_args):
        pass

    async def execute(action):
        calls.append(action)
        return remaining.pop(0)

    progress = BrowserProgress(emit)
    await progress.handle(
        {
            "type": "plan",
            "steps": [
                {"id": "choose", "keyword": "When", "title": "Choose age"},
                {"id": "recover", "keyword": "When", "title": "Recover observed target"},
            ],
        },
        "plan",
        execute,
    )
    return progress, calls, execute


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_type", ["click", "fill", "select", "press"])
@pytest.mark.parametrize("retry_type", ["click", "fill", "select", "press"])
async def test_missing_target_retries_cannot_bypass_brake_by_operation_payload_or_step(
    initial_type, retry_type
):
    progress, calls, execute = await _planned_target_progress(
        _target_receipt(), _target_receipt(status="completed", sequence=9, error=None)
    )
    selector = "#field-age .ant-select-selection-item"
    initial = {"type": initial_type, "step": "choose", "selector": selector, "value": "30"}
    await progress.handle(initial, "missing", execute)
    first = await progress.handle(
        {**initial, "type": retry_type, "step": "recover", "value": "40", "key": "ArrowDown"},
        "retry-one",
        execute,
    )
    assert first["status"] == "rejected"
    assert "without dispatch" in first["error"]
    assert "scoped snapshot" in first["error"]
    assert progress.stalled_reason is None
    assert not progress.unknown_action
    assert len(calls) == 1

    corrective = {"type": "click", "step": "recover", "selector": "aria-ref=fresh-age"}
    assert not (await progress.handle(corrective, "corrective", execute)).get("error")
    assert calls[-1] == {"type": "click", "selector": "aria-ref=fresh-age"}
    second = await progress.handle(
        {**initial, "type": "press", "key": "Enter"}, "retry-two", execute
    )
    assert second["status"] == "rejected"
    assert progress.stalled_reason == "repeated_missing_target"
    assert not progress.unknown_action
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot,receipt",
    [
        ({"selector": "#age"}, _target_receipt(sequence=9, error="scope missing")),
        (
            {"selector": "#age"},
            _target_receipt(status="failed", sequence=9, error=None),
        ),
        (
            {"selector": "#age"},
            _target_receipt(status="completed", sequence=9, error="scope missing"),
        ),
        ({"selector": "#age"}, _target_receipt(status="completed", sequence=8, error=None)),
        ({"selector": "#age"}, _target_receipt(status="completed", sequence=None, error=None)),
        ({}, _target_receipt(status="completed", sequence=9, error=None)),
        ({"selector": "#other"}, _target_receipt(status="completed", sequence=9, error=None)),
    ],
)
async def test_missing_target_plan_revision_and_nonrecovering_snapshots_do_not_clear_brake(
    snapshot, receipt
):
    progress, calls, execute = await _planned_target_progress(_target_receipt(), receipt)
    action = {"type": "click", "step": "choose", "selector": "#age"}
    await progress.handle(action, "missing", execute)
    revision = await progress.handle(
        {
            "type": "plan",
            "steps": [
                *progress.steps.values(),
                {"id": "another", "keyword": "And", "title": "Try observed recovery"},
            ],
            "reason": "Inspect current evidence",
        },
        "revision",
        execute,
    )
    assert revision["status"] == "planned"
    await progress.handle({"type": "snapshot", "step": "another", **snapshot}, "scope", execute)
    retry = await progress.handle({**action, "step": "another"}, "retry", execute)
    assert retry["status"] == "rejected"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_only_fresh_successful_exact_scoped_snapshot_recovers_target_and_retry_allowance():
    recovered = _target_receipt(status="completed", sequence=9, error=None)
    recovered["observation"].pop("write_started")
    progress, calls, execute = await _planned_target_progress(
        _target_receipt(), recovered, _target_receipt(sequence=10)
    )
    action = {"type": "fill", "step": "choose", "selector": "#age", "value": "30"}
    await progress.handle(action, "missing", execute)
    assert (await progress.handle(action, "retry-before-snapshot", execute))["status"] == "rejected"
    await progress.handle(
        {"type": "snapshot", "step": "recover", "selector": "#age"}, "scope", execute
    )
    assert (await progress.handle(action, "new-attempt", execute))["error"] == _MISSING_TARGET_ERROR
    assert len(calls) == 3
    assert (await progress.handle(action, "new-rejection", execute))["status"] == "rejected"
    assert progress.stalled_reason is None


@pytest.mark.asyncio
async def test_out_of_order_scoped_snapshot_does_not_recover_target():
    progress, calls, execute = await _planned_target_progress(
        _target_receipt(),
        _target_receipt(status="completed", sequence=11, error=None),
        _target_receipt(status="completed", sequence=10, error=None),
    )
    action = {"type": "click", "step": "choose", "selector": "#age"}
    await progress.handle(action, "missing", execute)
    await progress.handle({"type": "snapshot", "step": "recover"}, "full", execute)
    await progress.handle(
        {"type": "snapshot", "step": "recover", "selector": "#age"}, "old-scope", execute
    )
    assert (await progress.handle(action, "retry", execute))["status"] == "rejected"
    assert len(calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "receipt",
    [
        _target_receipt(status="completed"),
        _target_receipt(write_started=True),
        _target_receipt(write_started="false"),
        _target_receipt(write_started=0),
        _target_receipt(write_started=None),
        _target_receipt(sequence=None),
        _target_receipt(sequence=True),
        _target_receipt(error="locator.click: Timeout 30000ms exceeded"),
        _target_receipt(error="locator.click: " + _MISSING_TARGET_ERROR),
        _target_receipt(error=_MISSING_TARGET_ERROR + "\nCall log: ..."),
    ],
)
async def test_missing_target_brake_requires_exact_trusted_fresh_no_write_failure(receipt):
    progress, calls, execute = await _planned_target_progress(
        receipt, _target_receipt(status="completed", sequence=9, error=None)
    )
    action = {"type": "click", "step": "choose", "selector": "#age"}
    await progress.handle(action, "first", execute)
    assert not (await progress.handle(action, "second", execute)).get("error")
    assert len(calls) == 2
    assert progress.stalled_reason is None


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery_type", ["click", "select"])
async def test_readonly_fill_failure_does_not_block_legal_same_target_recovery(recovery_type):
    progress, calls, execute = await _planned_target_progress(
        _target_receipt(error="target is readonly before write; use the observed date widget"),
        _target_receipt(status="completed", sequence=9, error=None),
    )
    await progress.handle(
        {"type": "fill", "step": "choose", "selector": "#date", "value": "2026-11-11"},
        "readonly",
        execute,
    )
    assert not (
        await progress.handle(
            {"type": recovery_type, "step": "recover", "selector": "#date", "value": "11"},
            "recover",
            execute,
        )
    ).get("error")
    assert len(calls) == 2
    assert progress.stalled_reason is None


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["target_missing", "target_ambiguous"])
async def test_controlled_pre_actuation_failure_allows_correction_not_success(category):
    events = []

    async def emit(kind, payload):
        events.append((kind, payload))

    progress = BrowserProgress(emit)
    calls = []

    async def execute(action):
        calls.append(action)
        failed = len(calls) == 1
        return {
            "status": "failed" if failed else "completed",
            "observation": {
                "sequence": len(calls),
                "url": "https://example.com",
                "error": f"controlled target {category.removeprefix('target_')} before actuation"
                if failed
                else None,
                "write_started": False if failed else True,
                "extracted": {
                    "pre_actuation_failure": {
                        "phase": "target_resolution",
                        "category": category,
                    }
                }
                if failed
                else {},
            },
        }

    await progress.handle(
        {
            "type": "plan",
            "steps": [
                {"id": "choose", "keyword": "When", "title": "Choose product"},
            ],
        },
        "plan",
        execute,
    )
    result = await progress.handle(
        {"type": "click", "step": "choose", "selector": "text=Choose"}, "bad", execute
    )
    assert result["error"]
    assert not progress.unknown_action
    assert not progress.recorded_actions
    assert events[-1][1]["outcome"] == "failed"
    await progress.handle(
        {"type": "click", "step": "choose", "selector": "#unique"}, "corrected", execute
    )
    assert len(calls) == 2
    assert len(progress.recorded_actions) == 1
    assert events[-1][1]["outcome"] == "completed"


@pytest.mark.asyncio
async def test_preflight_rejected_is_not_business_action_and_requires_fresh_snapshot():
    events = []
    calls = []

    async def emit(kind, payload):
        events.append((kind, payload))

    progress = BrowserProgress(emit)
    progress.session_id = "session-a"

    async def execute(action):
        calls.append(action)
        if len(calls) == 1:
            return {
                "status": "preflight_rejected",
                "business_action_admitted": False,
                "operation": "fill",
                "session_id": "session-a",
                "control_epoch": 0,
                "preflight_action_id": "preflight-a",
                "observation_sequence": 2,
                "category": "target_missing",
            }
        return {
            "status": "completed",
            "session_id": "session-a",
            "control_epoch": 0,
            "observation": {
                "session_id": "session-a",
                "sequence": len(calls) + 2,
                "url": "https://example.com",
                "bounded_snapshot": "observed actual target",
            },
        }

    await progress.handle(
        {
            "type": "plan",
            "steps": [{"id": "input", "keyword": "When", "title": "Input", "expected_result": ""}],
        },
        "plan",
        execute,
    )
    result = await progress.handle(
        {"type": "fill", "selector": "#missing", "value": "secret", "step": "input"}, "a1", execute
    )
    assert result["status"] == "rejected" and result["code"] == "preflight_rejected"
    assert not progress.unknown_action and not progress.recorded_actions and not progress.used
    assert "secret" not in str(result)
    denied = await progress.handle(
        {"type": "fill", "selector": "#guessed", "value": "secret", "step": "input"}, "a2", execute
    )
    assert denied["status"] == "rejected" and len(calls) == 1
    await progress.handle(
        {"type": "snapshot", "selector": "#actual", "step": "input"}, "a3", execute
    )
    await progress.handle(
        {"type": "fill", "selector": "#actual", "value": "secret", "step": "input"}, "a4", execute
    )
    assert len(calls) == 3 and len(progress.recorded_actions) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["session", "sequence", "epoch", "operation", "category", "admitted", "extra"]
)
async def test_preflight_rejected_malformed_envelope_stops(mutation):
    async def emit(*args):
        pass

    progress = BrowserProgress(emit)
    progress.session_id = "session-a"
    receipt = {
        "status": "preflight_rejected",
        "business_action_admitted": False,
        "operation": "fill",
        "session_id": "session-a",
        "control_epoch": 0,
        "preflight_action_id": "preflight-a",
        "observation_sequence": 2,
        "category": "target_missing",
    }
    field, value = {
        "session": ("session_id", "other"),
        "sequence": ("observation_sequence", True),
        "epoch": ("control_epoch", True),
        "operation": ("operation", "select"),
        "category": ("category", "other"),
        "admitted": ("business_action_admitted", True),
        "extra": ("private", "secret"),
    }[mutation]
    receipt[field] = value

    async def execute(action):
        return receipt

    await progress.handle(
        {
            "type": "plan",
            "steps": [{"id": "input", "keyword": "When", "title": "Input", "expected_result": ""}],
        },
        "plan",
        execute,
    )
    with pytest.raises(ValueError, match="preflight rejection binding invalid"):
        await progress.handle(
            {"type": "fill", "selector": "#target", "value": "secret", "step": "input"},
            "a1",
            execute,
        )
    assert progress.unknown_action and not progress.recorded_actions


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["stale", "epoch", "session", "empty", "failed"])
async def test_preflight_rejection_not_cleared_by_untrusted_snapshot(mutation):
    async def emit(*args):
        pass

    progress = BrowserProgress(emit)
    progress.session_id = "session-a"
    progress._preflight_rejection = (2, 0, "session-a")
    receipt = {
        "status": "completed",
        "session_id": "session-a",
        "control_epoch": 0,
        "observation": {
            "session_id": "session-a",
            "sequence": 3,
            "url": "https://example.com",
            "bounded_snapshot": "Target",
        },
    }
    if mutation == "stale":
        receipt["observation"]["sequence"] = 2
    if mutation == "epoch":
        receipt["control_epoch"] = 1
    if mutation == "session":
        receipt["observation"]["session_id"] = "other"
    if mutation == "empty":
        receipt["observation"]["bounded_snapshot"] = ""
    if mutation == "failed":
        receipt["status"] = "failed"

    async def execute(action):
        return receipt

    await progress.handle(
        {
            "type": "plan",
            "steps": [{"id": "input", "keyword": "When", "title": "Input", "expected_result": ""}],
        },
        "plan",
        execute,
    )
    await progress.handle({"type": "snapshot", "step": "input"}, "snapshot", execute)
    assert progress._preflight_rejection is not None
