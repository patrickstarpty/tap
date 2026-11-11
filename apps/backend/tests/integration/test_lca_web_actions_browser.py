"""Opt-in actual generated action dispatch against an isolated local Chromium page."""

import json
import os
from pathlib import Path
import subprocess

import pytest

from tap_platform.lca.definition import validate_definition
from tap_platform.lca.generator import generate_playwright


@pytest.mark.skipif(
    os.getenv("TAP_RUN_LCA_LOCAL_BROWSER") != "1",
    reason="local Chromium opt-in required",
)
@pytest.mark.parametrize("fail", [False, True])
def test_generated_web_actions_in_real_browser(tmp_path, fail):
    root = Path(__file__).resolve().parents[4]
    playwright = root / "deploy/browser/node_modules/@playwright/test/index.mjs"
    targets = [
        dict(id=name, kind="test_id", value=name)
        for name in [
            "field",
            "box",
            "button",
            "container",
            "bottom",
            "source",
            "dest",
            "frame",
            "inside",
        ]
    ]
    targets[-1]["frame_target_id"] = "frame"
    targets.append(
        dict(id="exact", kind="test_id", value="exact", frame_target_id="frame")
    )
    actions = [
        ("fill", "field", {"value": "hello"}),
        ("press", "field", {"key": "End"}),
        ("assert_value", "field", {"value": "hello"}),
        ("hover", "button", {}),
        ("double_click", "button", {}),
        ("right_click", "button", {}),
        ("check", "box", {}),
        ("assert_checked", "box", {"checked": True}),
        ("uncheck", "box", {}),
        ("assert_checked", "box", {"checked": False}),
        ("drag_drop", "source", {"destination_target_id": "dest"}),
        ("scroll", "container", {"delta_x": 20, "delta_y": 300}),
        ("scroll", None, {"delta_x": 0, "delta_y": 300}),
        ("scroll_into_view", "bottom", {}),
        ("wait_for", "bottom", {"state": "visible"}),
        ("assert_visible", "inside", {}),
        ("fill", "inside", {"value": "framed"}),
        ("assert_value", "inside", {"value": "framed"}),
        (
            "assert_text_exact",
            "exact",
            {"text": "first second" if fail else "  first\nsecond  "},
        ),
    ]
    steps = [
        dict(
            id=f"action_{i}",
            operation=op,
            arguments=args,
            timeout_ms=1000,
            **({"target_id": target} if target else {}),
        )
        for i, (op, target, args) in enumerate(actions)
    ]
    parsed = validate_definition(
        dict(
            schema_version="lca-debug-v1",
            allowed_origins=["https://fixture.example"],
            targets=targets,
            parameters=[],
            steps=steps,
        )
    )
    source = generate_playwright(parsed).source
    html = """<input data-testid="field"><input type="checkbox" data-testid="box">
<button data-testid="button" ondblclick="this.dataset.double='yes'" oncontextmenu="event.preventDefault();this.dataset.right='yes'">button</button>
<div data-testid="source" draggable="true" ondragstart="event.dataTransfer.setData('text/plain','source')">Drag</div>
<div data-testid="dest" style="width:200px;height:100px" ondragover="event.preventDefault()" ondrop="event.preventDefault();this.dataset.dropped='yes'">Drop</div>
<div data-testid="container" style="overflow:scroll;width:200px;height:100px"><div style="width:1000px;height:1000px">scroll</div></div>
<iframe data-testid="frame" srcdoc='<input data-testid="inside"><pre data-testid="exact">  first
second  </pre>'></iframe>
<div style="height:1500px"></div><div data-testid="bottom">bottom</div>"""
    harness = f"""import {{chromium, expect}} from {json.dumps(playwright.as_uri())};
const browser = await chromium.launch({{headless:true}});
const page = await browser.newPage();
await page.setContent({json.dumps(html)});
let result, error;
const test = async (_, fn) => {{
  try {{ await fn({{page}}); }} catch(e) {{error = String(e);}}
  try {{
    await expect(page.getByTestId('button')).toHaveAttribute('data-double','yes');
    await expect(page.getByTestId('button')).toHaveAttribute('data-right','yes');
    await expect(page.getByTestId('dest')).toHaveAttribute('data-dropped','yes');
    if (await page.getByTestId('container').evaluate(el => el.scrollTop) !== 300) throw new Error('container did not scroll');
    console.log(JSON.stringify({{result, error}}));
  }} finally {{await browser.close();}}
}};
test.step = async (_, fn) => fn();
test.info = () => ({{attach: async (_, data) => {{result=JSON.parse(data.body.toString());}}}});
"""
    path = tmp_path / "actions.mjs"
    path.write_text(harness + source.split("\n", 1)[1])
    execution = subprocess.run(
        ["node", str(path)], capture_output=True, text=True, timeout=60
    )
    assert execution.returncode == 0, execution.stderr
    output = json.loads(execution.stdout)
    statuses = [step["status"] for step in output["result"]["steps"]]
    assert statuses == ["passed"] * (len(steps) - 1) + [
        "failed" if fail else "passed"
    ], output
    assert bool(output.get("error")) == fail
