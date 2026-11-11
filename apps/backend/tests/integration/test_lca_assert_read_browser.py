"""Execute generated, model-free read assertions in real Chromium."""

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
@pytest.mark.parametrize(
    "attribute,condition,html,expected",
    [
        (
            "text",
            {"equals": '  "USD" 1,234.50\nquoted  '},
            '<pre data-testid="field">  "USD" 1,234.50\nquoted  </pre>',
            '  "USD" 1,234.50\nquoted  ',
        ),
        (
            "text",
            {
                "positive": True,
                "nonempty": True,
                "pattern": r"^USD [0-9]+",
                "contains": "USD",
            },
            '<div data-testid="field">USD 12</div>',
            "USD 12",
        ),
        (
            "value",
            {"equals": 'hello "world"'},
            '<input data-testid="field" value="hello &quot;world&quot;">',
            'hello "world"',
        ),
        (
            "checked",
            {"equals": "true"},
            '<input data-testid="field" type="checkbox" checked>',
            "true",
        ),
        (
            "checked",
            {"equals": "mixed"},
            '<div data-testid="field" aria-checked="mixed"></div>',
            "mixed",
        ),
        (
            "aria-valuenow",
            {"positive": True},
            '<div data-testid="field" aria-valuenow="23"></div>',
            "23",
        ),
        (
            "data-active",
            {"equals": ""},
            '<button data-testid="field" data-active>Monthly</button>',
            "",
        ),
        (
            "data-active",
            {"equals": ""},
            '<button data-testid="field">Monthly</button>',
            None,
        ),
        ("text", {"positive": True}, '<div data-testid="field">1 of 2</div>', None),
        ("text", {"positive": True}, '<div data-testid="field">-12</div>', None),
        (
            "text",
            {"equals": "first second"},
            '<pre data-testid="field">first\nsecond</pre>',
            None,
        ),
        (
            "value",
            {"nonempty": True},
            '<input data-testid="field" type="password" value="secret">',
            None,
        ),
        (
            "title",
            {"nonempty": True},
            '<input data-testid="field" autocomplete="cc-number" title="secret">',
            None,
        ),
        ("title", {"nonempty": True}, '<div data-testid="field"></div>', None),
    ],
)
def test_read_in_chromium(tmp_path, attribute, condition, html, expected):
    root = Path(__file__).resolve().parents[4]
    playwright = root / "deploy/browser/node_modules/@playwright/test/index.mjs"
    definition = validate_definition(
        dict(
            schema_version="lca-debug-v1",
            allowed_origins=["https://fixture.example"],
            targets=[dict(id="field", kind="test_id", value="field")],
            parameters=[],
            steps=[
                dict(
                    id="read",
                    operation="assert_read",
                    target_id="field",
                    arguments=dict(attribute=attribute, name="quote", **condition),
                    timeout_ms=300,
                )
            ],
        )
    )
    source = generate_playwright(definition).source.split("\n", 1)[1]
    harness = f"""import {{chromium, expect}} from {json.dumps(playwright.as_uri())};
const browser = await chromium.launch({{headless:true}});
const page = await browser.newPage();
await page.setContent({json.dumps(html)});
let result, error;
const test = async (_, fn) => {{
  try {{ await fn({{page}}); }} catch(e) {{error = String(e);}}
  console.log(JSON.stringify({{result,error}}));
  await browser.close();
}};
test.step = async (_, fn) => fn();
test.info = () => ({{attach: async (_, data) => {{result=JSON.parse(data.body.toString());}}}});
"""
    script = tmp_path / "read.mjs"
    script.write_text(harness + source)
    run = subprocess.run(
        ["node", str(script)], capture_output=True, text=True, timeout=20
    )
    assert run.returncode == 0, run.stderr
    output = json.loads(run.stdout)
    step = output["result"]["steps"][0]
    if expected is None:
        assert step == {"step_id": "read", "status": "failed"}, output
        assert output["error"]
        assert "secret" not in json.dumps(output)
    else:
        assert step == {
            "step_id": "read",
            "status": "passed",
            "output": {"name": "quote", "value": expected},
        }, output
        assert not output.get("error")
