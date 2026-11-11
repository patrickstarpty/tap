"""Execute generated multipage and one-shot dialog actions in real Chromium."""

import json
import os
from pathlib import Path
import subprocess

import pytest

from tap_platform.lca.definition import validate_definition
from tap_platform.lca.generator import generate_playwright

pytestmark = pytest.mark.skipif(
    os.getenv("TAP_RUN_LCA_LOCAL_BROWSER") != "1",
    reason="local Chromium opt-in required",
)


def action(operation, arguments=None, target_id=None, **fields):
    result = dict(
        operation=operation, arguments=arguments or {}, timeout_ms=700, **fields
    )
    if target_id:
        result["target_id"] = target_id
    return result


def execute(tmp_path, steps, html, after=""):
    root = Path(__file__).resolve().parents[4]
    playwright = root / "deploy/browser/node_modules/@playwright/test/index.mjs"
    steps = [dict(step, id=f"s{i}") for i, step in enumerate(steps)]
    definition = validate_definition(
        dict(
            schema_version="lca-debug-v1",
            allowed_origins=["https://fixture.example"],
            targets=[
                dict(id=name, kind="test_id", value=name)
                for name in ["button", "result", "field", "close", "frame", "inside"]
            ],
            parameters=[],
            steps=steps,
        )
    )
    source = generate_playwright(definition).source.split("\n", 1)[1]
    harness = f"""import {{chromium, expect}} from {json.dumps(playwright.as_uri())};
const browser = await chromium.launch({{headless:true}});
const context = await browser.newContext();
await context.route('**/*', route => route.fulfill({{contentType:'text/html', body:{json.dumps(html)}}}));
const page = await context.newPage();
await page.goto('https://fixture.example/main');
let result, error;
const test = async (_, fn) => {{
  try {{ await fn({{page}}); }} catch(e) {{error = String(e);}}
  try {{
    {after}
    console.log(JSON.stringify({{result,error,pageCount:context.pages().length,dialogs:page.listenerCount('dialog'),popups:page.listenerCount('popup')}}));
  }} finally {{await browser.close();}}
}};
test.step = async (_, fn) => fn();
test.info = () => ({{attach: async (name, data) => {{if(name === 'tapper-result') result=JSON.parse(data.body.toString());}}}});
"""
    path = tmp_path / "pages.mjs"
    path.write_text(harness + source)
    execution = subprocess.run(
        ["node", str(path)], capture_output=True, text=True, timeout=30
    )
    assert execution.returncode == 0, execution.stderr
    return json.loads(execution.stdout)


def test_independent_pages_switch_explicit_target_and_close(tmp_path):
    steps = [
        action(
            "open_page", {"page_id": "second", "url": "https://fixture.example/second"}
        ),
        action("fill", {"value": "second value"}, "field", page_id="second"),
        action("assert_value", {"value": ""}, "field"),
        action("switch_page", {"page_id": "second"}),
        action("assert_value", {"value": "second value"}, "field"),
        action("assert_url", {"url": "https://fixture.example/second"}),
        action("assert_url", {"url": "https://fixture.example/main"}, page_id="main"),
        action("close_page", {"page_id": "main"}),
    ]
    output = execute(tmp_path, steps, '<input data-testid="field">')
    assert [s["status"] for s in output["result"]["steps"]] == ["passed"] * len(
        steps
    ), output
    assert output["pageCount"] == 1


@pytest.mark.parametrize(
    "popup",
    [
        "window.open('/popup')",
        "const p=window.open('about:blank');setTimeout(()=>p.location='/popup',100)",
    ],
)
def test_click_popup_waits_for_real_allowed_url_without_switching(tmp_path, popup):
    steps = [
        action("click_popup", {"page_id": "popup"}, "button"),
        action("assert_url", {"url": "https://fixture.example/main"}),
        action("assert_url", {"url": "https://fixture.example/popup"}, page_id="popup"),
        action("close_page", {"page_id": "popup"}),
    ]
    output = execute(
        tmp_path, steps, f'<button data-testid="button" onclick="{popup}">open</button>'
    )
    assert [s["status"] for s in output["result"]["steps"]] == ["passed"] * len(
        steps
    ), output
    assert output["popups"] == 0
    assert output["pageCount"] == 1


@pytest.mark.parametrize(
    "popup",
    ["window.open('https://evil.example/')", "window.open('about:blank')", "void 0"],
)
def test_popup_missing_blank_and_disallowed_fail_without_running_later_steps(
    tmp_path, popup
):
    output = execute(
        tmp_path,
        [
            action("click_popup", {"page_id": "popup"}, "button"),
            action("click", {}, "button"),
        ],
        f'<button data-testid="button" onclick="{popup}">open</button>',
    )
    assert [s["status"] for s in output["result"]["steps"]] == ["failed", "not_run"], (
        output
    )
    assert output["popups"] == 0
    assert output["error"]


@pytest.mark.parametrize(
    "kind,decision,prompt,result",
    [
        ("alert", "accept", None, "undefined"),
        ("confirm", "accept", None, "true"),
        ("confirm", "dismiss", None, "false"),
        ("prompt", "accept", "Ada", "Ada"),
        ("prompt", "dismiss", None, "null"),
    ],
)
def test_dialog_explicit_decision_is_scoped_to_once(
    tmp_path, kind, decision, prompt, result
):
    arguments = dict(type=kind, action=decision, message="Expected message")
    if prompt is not None:
        arguments["prompt_text"] = prompt
    html = f"""<button data-testid="button" onclick="document.querySelector('output').textContent=String({kind}('Expected message'))">go</button><output data-testid="result"></output>"""
    output = execute(
        tmp_path,
        [
            action("click_dialog", arguments, "button"),
            action("assert_text", {"text": result}, "result"),
        ],
        html,
        # A later dialog is auto-dismissed by Playwright. No stale accept listener.
        "if (await page.evaluate(() => confirm('Later decision'))) throw new Error('stale dialog acceptance');",
    )
    assert [s["status"] for s in output["result"]["steps"]] == ["passed", "passed"], (
        output
    )
    assert output["dialogs"] == 0


@pytest.mark.parametrize(
    "trigger", ["confirm('Unexpected')", "alert('Expected message')", "void 0"]
)
def test_dialog_mismatch_or_absence_fails_and_cleans_listener(tmp_path, trigger):
    html = f"""<button data-testid="button" onclick="document.querySelector('output').textContent=String({trigger})">go</button><output data-testid="result"></output>"""
    output = execute(
        tmp_path,
        [
            action(
                "click_dialog",
                {"type": "confirm", "action": "accept", "message": "Expected message"},
                "button",
            ),
            action("click", {}, "button"),
        ],
        html,
        "if (await page.evaluate(() => confirm('Later decision'))) throw new Error('stale dialog acceptance');",
    )
    assert [s["status"] for s in output["result"]["steps"]] == ["failed", "not_run"], (
        output
    )
    assert output["dialogs"] == 0


def test_unexpected_page_close_fails_instead_of_switching_to_ambient_tab(tmp_path):
    steps = [
        action("click_popup", {"page_id": "popup"}, "button"),
        action("click", {}, "close", page_id="popup"),
        action("assert_visible", {}, "field", page_id="popup"),
    ]
    html = """<button data-testid="button" onclick="window.open('/popup')">open</button><button data-testid="close" onclick="window.close()">close</button><input data-testid="field">"""
    output = execute(tmp_path, steps, html)
    assert [s["status"] for s in output["result"]["steps"]] == [
        "passed",
        "passed",
        "failed",
    ], output
    assert output["error"]
