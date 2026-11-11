import pytest

from tap_platform.lca.definition import (
    canonical_bytes,
    resolve_parameters,
    validate_definition,
)
from tap_platform.lca.generator import generate_playwright


def flow(operation, arguments=None, target=True):
    step = dict(
        id="action", operation=operation, arguments=arguments or {}, timeout_ms=1000
    )
    if target:
        step["target_id"] = "field"
    return dict(
        schema_version="lca-debug-v1",
        allowed_origins=["https://fixture.example"],
        targets=[
            dict(id="field", kind="test_id", value="field"),
            dict(id="dest", kind="test_id", value="dest"),
        ],
        parameters=[],
        steps=[step],
    )


@pytest.mark.parametrize(
    "operation,arguments,fragment",
    [
        ("scroll", {"delta_x": 0, "delta_y": 500}, "scrollBy"),
        ("scroll_into_view", {}, ".scrollIntoViewIfNeeded("),
        ("press", {"key": "ControlOrMeta+Enter"}, '.press("ControlOrMeta+Enter"'),
        ("hover", {}, ".hover("),
        ("double_click", {}, ".dblclick("),
        ("right_click", {}, 'button: "right"'),
        ("check", {}, ".check("),
        ("uncheck", {}, ".uncheck("),
        (
            "drag_drop",
            {"destination_target_id": "dest"},
            '.dragTo(page.getByTestId("dest")',
        ),
        ("wait_for", {"state": "hidden"}, 'state: "hidden"'),
        ("assert_visible", {}, ".toBeVisible("),
        ("assert_value", {"value": "hello"}, '.toHaveValue("hello"'),
        ("assert_checked", {"checked": False}, ".not.toBeChecked("),
    ],
)
def test_supported_web_actions(operation, arguments, fragment):
    parsed = validate_definition(flow(operation, arguments))
    assert fragment in generate_playwright(parsed).source
    assert generate_playwright(parsed) == generate_playwright(parsed)


def test_page_scroll_and_frame_locator():
    parsed = validate_definition(
        flow("scroll", {"delta_x": -100, "delta_y": 500}, False)
    )
    assert "page.evaluate(" in generate_playwright(parsed).source
    raw = flow("click")
    raw["targets"][0]["frame_target_id"] = "dest"
    assert (
        'page.getByTestId("dest").contentFrame().getByTestId("field")'
        in generate_playwright(validate_definition(raw)).source
    )
    raw["targets"][1]["frame_target_id"] = "field"
    with pytest.raises(ValueError):
        validate_definition(raw)


@pytest.mark.parametrize(
    "operation,args",
    [
        ("scroll", {"delta_x": True, "delta_y": 1}),
        ("scroll", {"delta_x": 0, "delta_y": 10001}),
        ("scroll", {"delta_x": 0, "delta_y": float("nan")}),
        ("press", {"key": ""}),
        ("press", {"key": "oops\nEnter"}),
        ("wait_for", {"state": "ready"}),
        ("assert_checked", {"checked": "false"}),
        ("drag_drop", {"destination_target_id": "absent"}),
        ("assert_value", {"value": 1}),
        ("hover", {"code": "alert(1)"}),
    ],
)
def test_invalid_action_arguments(operation, args):
    with pytest.raises(ValueError):
        validate_definition(flow(operation, args))


def test_runtime_parameter_constraints():
    raw = flow("scroll", {"delta_x": 0, "delta_y": {"parameter": "dy"}})
    raw["parameters"] = [dict(name="dy", type="number", required=True)]
    parsed = validate_definition(raw)
    assert resolve_parameters(parsed, {"dy": 30}) == {"dy": 30}
    with pytest.raises(ValueError):
        resolve_parameters(parsed, {"dy": 10001})
    raw = flow("wait_for", {"state": {"parameter": "state"}})
    raw["parameters"] = [dict(name="state", type="string", required=True)]
    with pytest.raises(ValueError):
        resolve_parameters(validate_definition(raw), {"state": "invalid"})


def test_legacy_canonical_shape():
    raw = flow("click")
    assert b"frame_target_id" not in canonical_bytes(validate_definition(raw))


def test_drag_rejects_cross_frame_destination():
    raw = flow("drag_drop", {"destination_target_id": "dest"})
    raw["targets"].append(dict(id="frame", kind="test_id", value="frame"))
    raw["targets"][0]["frame_target_id"] = "frame"
    with pytest.raises(ValueError, match="same frame"):
        validate_definition(raw)


def test_generated_actions_dispatch_in_javascript(tmp_path):
    import json
    import shutil
    import subprocess

    if not shutil.which("node"):
        pytest.skip("Node required for generated JavaScript contract test")
    actions = [
        ("scroll", {"delta_x": 20, "delta_y": 30}),
        ("scroll_into_view", {}),
        ("press", {"key": "Enter"}),
        ("hover", {}),
        ("double_click", {}),
        ("right_click", {}),
        ("check", {}),
        ("uncheck", {}),
        ("drag_drop", {"destination_target_id": "dest"}),
        ("wait_for", {"state": "visible"}),
        ("assert_visible", {}),
        ("assert_value", {"value": '"); globalThis.pwned=true; //'}),
        ("assert_checked", {"checked": True}),
        ("assert_checked", {"checked": False}),
    ]
    raw = flow("click")
    raw["steps"] = [
        dict(
            id=f"action_{i}",
            operation=op,
            arguments=args,
            target_id="field",
            timeout_ms=1000,
        )
        for i, (op, args) in enumerate(actions)
    ]
    raw["steps"].append(
        dict(
            id="page_scroll",
            operation="scroll",
            arguments={"delta_x": 1, "delta_y": 2},
            timeout_ms=1000,
        )
    )
    source = generate_playwright(validate_definition(raw)).source
    harness = """
const events = [];
const target = {scrollBy: (v) => events.push(['scroll', v])};
globalThis.window = target;
const locator = new Proxy({}, {get: (_, key) => {
  if (key === 'evaluate') return async (fn, args) => fn(target, args);
  return async (...args) => events.push([key, ...args]);
}});
const page = {getByTestId: () => locator, evaluate: async (fn, args) => fn(args)};
const assertion = {toBeVisible: async () => events.push(['visible']), toHaveValue: async (v) => events.push(['value', v]), toBeChecked: async () => events.push(['checked']), not: {toBeChecked: async () => events.push(['unchecked'])}};
const expect = () => assertion;
const test = async (_, fn) => {await fn({page}); console.log(JSON.stringify({events, pwned: !!globalThis.pwned}));};
test.step = async (_, fn) => fn();
test.info = () => ({attach: async () => {}});
"""
    path = tmp_path / "generated.cjs"
    path.write_text(harness + source.split("\n", 1)[1])
    data = json.loads(
        subprocess.run(
            ["node", str(path)], capture_output=True, text=True, check=True
        ).stdout
    )
    assert not data["pwned"]
    assert [event[0] for event in data["events"]] == [
        "scroll",
        "scrollIntoViewIfNeeded",
        "press",
        "hover",
        "dblclick",
        "click",
        "check",
        "uncheck",
        "dragTo",
        "waitFor",
        "visible",
        "value",
        "checked",
        "unchecked",
        "scroll",
    ]
    assert data["events"][0][1] == {"left": 20, "top": 30, "behavior": "instant"}


def test_exact_text_assertion_preserves_inner_text_and_frame_scope():
    text = "  first\nsecond  "
    raw = flow("assert_text_exact", {"text": {"parameter": "expected"}})
    raw["parameters"] = [dict(name="expected", type="string", required=True)]
    raw["targets"][0]["frame_target_id"] = "dest"
    source = generate_playwright(validate_definition(raw), {"expected": text}).source
    assert (
        'expect.poll(() => page.getByTestId("dest").contentFrame().getByTestId("field").innerText(), { timeout: 1000 }).toBe("  first\\nsecond  ")'
        in source
    )
    raw["steps"][0]["arguments"] = {"text": False}
    with pytest.raises(ValueError):
        validate_definition(raw)
