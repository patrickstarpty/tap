"""Run generated semantic guards against real local DOM, without Docker/model calls."""

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
    "label,scope,passed",
    [
        ("Age", "Applicant", True),
        ("Other age", "Child", False),
        ("Storage", "Applicant", False),
    ],
)
def test_generated_guard_preserves_target_and_scope(tmp_path, label, scope, passed):
    definition = validate_definition(
        {
            "schema_version": "lca-debug-v1",
            "allowed_origins": ["https://fixture.example"],
            "targets": [{"id": "age", "kind": "test_id", "value": "age"}],
            "parameters": [],
            "steps": [
                {
                    "id": "read",
                    "operation": "assert_read",
                    "target_id": "age",
                    "target_contract": {
                        "target_texts": ["Age"],
                        "scope_text": "Applicant",
                    },
                    "arguments": {
                        "attribute": "value",
                        "name": "Age-Years",
                        "equals": "30",
                    },
                    "timeout_ms": 300,
                }
            ],
        }
    )
    source = generate_playwright(definition).source.split("\n", 1)[1]
    playwright = (
        Path(__file__).resolve().parents[4]
        / "deploy/browser/node_modules/@playwright/test/index.mjs"
    )
    html = f'<section><label>{scope} {label}<input data-testid="age" value="30"></label></section>'
    harness = f"""import {{chromium,expect}} from {json.dumps(playwright.as_uri())};
const browser=await chromium.launch({{headless:true}}); const page=await browser.newPage(); await page.setContent({json.dumps(html)});
let result,error; const test=async (_,fn)=>{{try{{await fn({{page}})}}catch(e){{error=String(e)}} console.log(JSON.stringify({{result,error}})); await browser.close();}};
test.step=async (_,fn)=>fn();test.info=()=>({{attach:async(_,data)=>{{result=JSON.parse(data.body.toString())}}}});
"""
    script = tmp_path / "guard.mjs"
    script.write_text(harness + source)
    run = subprocess.run(
        ["node", str(script)], capture_output=True, text=True, timeout=20, check=True
    )
    result = json.loads(run.stdout.splitlines()[-1])
    assert (result["result"]["steps"][0]["status"] == "passed") is passed
    if passed:
        assert result["result"]["steps"][0]["output"] == {
            "name": "Age-Years",
            "value": "30",
        }
    else:
        assert "contract" in result["error"]
