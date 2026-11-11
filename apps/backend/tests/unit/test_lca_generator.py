import json
import shutil
import subprocess

import pytest

from tap_platform.lca.definition import validate_definition
from tap_platform.lca.generator import generate_playwright
from test_lca_definition import definition


def test_generation_is_deterministic_and_mapping_is_structural() -> None:
    parsed = validate_definition(definition())
    first = generate_playwright(parsed, {"name": "Ada"})
    assert first == generate_playwright(parsed, {"name": "Ada"})
    assert [m.step_id for m in first.step_mapping] == [
        "open",
        "fill",
        "select",
        "click",
        "text",
        "url",
    ]
    assert len({m.line for m in first.step_mapping}) == 6
    assert all(
        first.source.splitlines()[m.line - 1].lstrip().startswith("await test.step(")
        for m in first.step_mapping
    )
    assert (
        first.input_digest != generate_playwright(parsed, {"name": "Bob"}).input_digest
    )


def test_generated_operations_execute_literals_without_interpolation(tmp_path) -> None:
    if not shutil.which("node"):
        pytest.skip("Node is required for generated JavaScript syntax/dispatch test")
    attack = '"); globalThis.pwned=true; //\n${evil}\\\u2028'
    result = generate_playwright(validate_definition(definition()), {"name": attack})
    # Contract harness executes generated JavaScript, not a browser acceptance claim.
    harness = """
const events = [];
const locator = {fill: async (v) => events.push(['fill',v]), selectOption: async (v) => events.push(['select',v]), click: async () => events.push(['click'])};
const page = {goto: async (u) => events.push(['navigate',u]), getByTestId: () => locator};
const expect = (target) => ({toHaveText: async (v) => events.push(['text',v]), toHaveURL: async (v) => events.push(['url',v])});
const test = async (title, fn) => {await fn({page}); console.log(JSON.stringify({events, pwned: !!globalThis.pwned}));};
test.step = async (title, fn) => fn();
test.info = () => ({attach: async () => {}});
"""
    path = tmp_path / "generated.cjs"
    path.write_text(harness + result.source.split("\n", 1)[1])
    output = subprocess.run(
        ["node", str(path)], capture_output=True, text=True, check=True
    )
    data = json.loads(output.stdout)
    assert data == {
        "events": [
            ["navigate", "https://fixture.example/form"],
            ["fill", attack],
            ["select", "a"],
            ["click"],
            ["text", "Done"],
            ["url", "https://fixture.example/done"],
        ],
        "pwned": False,
    }


@pytest.mark.parametrize("fail", [False, True])
def test_canonical_result_attachment_preserves_failed_assertion(
    tmp_path, fail: bool
) -> None:
    result = generate_playwright(validate_definition(definition()), {"name": "Ada"})
    assert all(
        m.file == "flow.spec.ts" and m.title == "lca:" + m.step_id
        for m in result.step_mapping
    )
    harness = """
let output;
const locator = {fill: async () => {}, selectOption: async () => {}, click: async () => {}};
const page = {goto: async () => {}, getByTestId: () => locator};
const expect = () => ({toHaveText: async () => {if (FAIL) throw Error('wrong assertion');}, toHaveURL: async () => {}});
const test = async (title, fn) => {let failed = false; try {await fn({page});} catch {failed=true;} console.log(JSON.stringify({output,failed}));};
test.step = async (title, fn) => fn();
test.info = () => ({attach: async (name, data) => {if (name !== 'tapper-result' || data.contentType !== 'application/json') throw Error('bad attachment'); output=JSON.parse(data.body.toString());}});
""".replace("FAIL", json.dumps(fail))
    path = tmp_path / "result.cjs"
    path.write_text(harness + result.source.split("\n", 1)[1])
    data = json.loads(
        subprocess.run(
            ["node", str(path)], capture_output=True, text=True, check=True
        ).stdout
    )
    assert data["failed"] is fail
    assert data["output"] == {
        "schema_version": "lca-debug-result-v1",
        "definition_digest": result.definition_digest,
        "steps": [
            {
                "step_id": s,
                "status": ("failed" if s == "text" else "not_run")
                if fail and s in {"text", "url"}
                else "passed",
            }
            for s in ["open", "fill", "select", "click", "text", "url"]
        ],
    }
