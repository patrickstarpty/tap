"""Deterministic fixed-template Playwright generation, with structural ID mapping."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from tap_platform.lca.definition import (
    DebugDefinition,
    ParameterReference,
    Target,
    canonical_bytes,
    resolve_parameters,
    validate_definition,
)

GENERATOR_VERSION = "lca-playwright-v1"


@dataclass(frozen=True)
class CanonicalStepMapping:
    step_id: str
    file: str
    line: int
    column: int
    title: str


@dataclass(frozen=True)
class GeneratedPlaywright:
    source: str
    canonical_bytes: bytes
    definition_digest: str
    input_digest: str
    step_mapping: tuple[CanonicalStepMapping, ...]
    generator_version: str = GENERATOR_VERSION


def literal(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def target_locator(target_id: str, targets: dict[str, Target]) -> str:
    target = targets[target_id]
    scope = "page"
    if target.frame_target_id is not None:
        scope = target_locator(target.frame_target_id, targets) + ".contentFrame()"
    if target.kind == "test_id":
        return f"{scope}.getByTestId({literal(target.value)})"
    if target.kind == "role":
        return f"{scope}.getByRole({literal(target.value)}, {{ name: {literal(target.name)}, exact: true }})"
    return f"{scope}.locator({literal('css=' + target.value)})"


# Fixed code only: selectors, values and patterns are emitted as JSON literals.
READ_ASSERTION_HELPER = r"""
  const readAssertion = async (locator, args, timeout) => {
    const deadline = Date.now() + timeout;
    const options = () => ({timeout: Math.max(1, deadline - Date.now())});
    const pattern = args.pattern === undefined ? null : new RegExp(args.pattern, 'u');
    while (true) {
      const type = await locator.getAttribute('type', options());
      const autocomplete = await locator.getAttribute('autocomplete', options());
      if ((type || '').toLowerCase() === 'password' || /password|one-time-code|cc-number|cc-csc/i.test(autocomplete || '')) throw new Error('Sensitive control cannot be extracted');
      let value;
      if (args.attribute === 'text') value = await locator.innerText(options());
      else if (args.attribute === 'value') value = await locator.inputValue(options());
      else if (args.attribute === 'checked') value = await locator.evaluate(node => node.getAttribute('aria-checked') ?? String(node.checked), undefined, options());
      else value = await locator.getAttribute(args.attribute, options());
      if (typeof value !== 'string' || value.length > 16000) throw new Error('DOM value unavailable or too large');
      const numbers = value.replace(/,/g, '').match(/-?\d+(?:\.\d+)?/g) || [];
      if ((args.equals === undefined || value === args.equals)
          && (args.contains === undefined || value.includes(args.contains))
          && (!args.nonempty || value.trim().length > 0)
          && (!args.positive || (numbers.length === 1 && Number(numbers[0]) > 0))
          && (!pattern || pattern.test(value))) return value;
      if (Date.now() >= deadline) throw new Error('Expected DOM value was not observed');
      await new Promise(resolve => setTimeout(resolve, Math.min(100, Math.max(0, deadline - Date.now()))));
    }
  };
"""


# Same bounded semantic guard as the AA runtime; literals only.
TARGET_CONTRACT_HELPER = r"""
  const assertTargetContract = async (locator, contract, timeout) => {
  if (!contract.target_texts) return; // Older frozen bundles retain their original contract.
  if (!Array.isArray(contract.target_texts) || !contract.target_texts.length) throw new Error('missing target contract');
  const facts = await locator.evaluate(node => ({
    labels: [node.id, node.getAttribute('name'), node.getAttribute('aria-label'),
      ...(node.labels ? [...node.labels].map(n=>n.textContent) : []),
      node.textContent, ...(node.getAttribute('aria-labelledby') || '').split(/\s+/).map(id=>document.getElementById(id)?.textContent)].filter(Boolean),
    scopes: [node.closest('label'), node.parentElement, node.parentElement?.parentElement]
      .filter(n=>n && !['BODY','HTML'].includes(n.tagName) && (n.textContent || '').length <= 600)
      .map(n=>(n.innerText || n.textContent).trim())
      .filter(text=>text !== (node.innerText || node.textContent || '').trim()).slice(0,1),
    type: node.getAttribute('type'), autocomplete: node.getAttribute('autocomplete') || '',
  }), {timeout});
  const matches = (text, expected) => {
    const value = String(text).normalize('NFKC').toLowerCase().replace(/\s+/g,' ').trim();
    const term = String(expected).normalize('NFKC').toLowerCase().replace(/\s+/g,' ').trim();
    const escaped = term.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    return Boolean(term) && new RegExp('(^|[^\\p{L}\\p{N}])'+escaped+'($|[^\\p{L}\\p{N}])','u').test(value);
  };
  if (facts.type === 'password' || /password|one-time-code|cc-number|cc-csc/.test(facts.autocomplete)) throw new Error('sensitive target');
  if (!contract.target_texts.some(term=>facts.labels.some(text=>matches(text,term)))) throw new Error('target does not match frozen BDD contract');
  if (contract.scope_text && !facts.scopes.some(text=>matches(text,contract.scope_text))) throw new Error('scope does not match frozen BDD contract');
  };
"""


def generate_playwright(
    definition: DebugDefinition, parameters: Mapping[str, object] | None = None
) -> GeneratedPlaywright:
    definition = validate_definition(definition)
    for group in definition.bdd_steps:
        if not group.action_ids:
            raise ValueError(
                f"BDD group '{group.id}' has no actions; implement or remove it before generation or execution"
            )
    inputs = resolve_parameters(definition, parameters)
    frozen = canonical_bytes(definition)
    targets = {t.id: t for t in definition.targets}
    lines = [
        "import { test, expect } from '@playwright/test';",
        "test('LCA debug flow', async ({ page }) => {",
    ]
    if any(s.operation == "assert_read" for s in definition.steps):
        lines.extend(READ_ASSERTION_HELPER.strip("\n").split("\n"))
    if any(step.target_contract is not None for step in definition.steps):
        lines.extend(TARGET_CONTRACT_HELPER.strip("\n").split("\n"))
    digest = hashlib.sha256(frozen).hexdigest()
    result = {
        "schema_version": "lca-debug-result-v1",
        "definition_digest": digest,
        "steps": [{"step_id": s.id, "status": "not_run"} for s in definition.steps],
    }
    lines.extend([f"  const result = {literal(result)};", "  try {"])
    mapping = []
    for index, step in enumerate(definition.steps):
        mapping.append(
            CanonicalStepMapping(
                step.id, "flow.spec.ts", len(lines) + 1, 5, "lca:" + step.id
            )
        )
        lines.append(f"    await test.step({literal('lca:' + step.id)}, async () => {{")
        options = f"{{ timeout: {step.timeout_ms} }}"
        args = {
            key: inputs[value.parameter]
            if isinstance(value, ParameterReference)
            else value
            for key, value in step.arguments.items()
        }
        locator = ""
        if step.target_id is not None:
            locator = target_locator(step.target_id, targets)
        if step.operation == "navigate":
            command = f"page.goto({literal(args['url'])}, {options})"
        elif step.operation == "click":
            command = f"{locator}.click({options})"
        elif step.operation in {"fill", "select"}:
            method = "fill" if step.operation == "fill" else "selectOption"
            command = f"{locator}.{method}({literal(args['value'])}, {options})"
        elif step.operation == "assert_read":
            command = f"readAssertion({locator}, {literal(args)}, {step.timeout_ms})"
            # Named values stay in the bounded result evidence, never source/logs.
            if "name" in args:
                command += f".then(value => {{ result.steps[{index}].output = {{ name: {literal(args['name'])}, value }}; }})"
        elif step.operation == "assert_text_exact":
            command = f"expect.poll(() => {locator}.innerText(), {options}).toBe({literal(args['text'])})"
        elif step.operation == "assert_text":
            command = (
                f"expect({locator}).toHaveText({literal(args['text'])}, {options})"
            )
        elif step.operation == "assert_url":
            command = f"expect(page).toHaveURL({literal(args['url'])}, {options})"
        elif step.operation in {
            "hover",
            "check",
            "uncheck",
            "scroll_into_view",
            "double_click",
        }:
            method = {
                "scroll_into_view": "scrollIntoViewIfNeeded",
                "double_click": "dblclick",
            }.get(step.operation, step.operation)
            command = f"{locator}.{method}({options})"
        elif step.operation == "right_click":
            command = (
                f'{locator}.click({{ button: "right", timeout: {step.timeout_ms} }})'
            )
        elif step.operation == "press":
            command = f"{locator}.press({literal(args['key'])}, {options})"
        elif step.operation == "drag_drop":
            destination = args["destination_target_id"]
            assert isinstance(destination, str)
            command = (
                f"{locator}.dragTo({target_locator(destination, targets)}, {options})"
            )
        elif step.operation == "wait_for":
            command = f"{locator}.waitFor({{ state: {literal(args['state'])}, timeout: {step.timeout_ms} }})"
        elif step.operation == "assert_visible":
            command = f"expect({locator}).toBeVisible({options})"
        elif step.operation == "assert_value":
            command = (
                f"expect({locator}).toHaveValue({literal(args['value'])}, {options})"
            )
        elif step.operation == "assert_checked":
            negate = "" if args["checked"] else ".not"
            command = f"expect({locator}){negate}.toBeChecked({options})"
        else:  # Fixed scroll function only; no user-provided JavaScript is accepted.
            delta = literal([args["delta_x"], args["delta_y"]])
            if locator:
                command = f"{locator}.evaluate((element, [x, y]) => element.scrollBy({{ left: x, top: y, behavior: 'instant' }}), {delta}, {options})"
            else:
                command = f"page.evaluate(([x, y]) => window.scrollBy({{ left: x, top: y, behavior: 'instant' }}), {delta})"
        guard = []
        if step.target_contract is not None:
            guard = [
                f"        await assertTargetContract({locator}, {literal(step.target_contract.model_dump(exclude_unset=True))}, {step.timeout_ms});"
            ]
        lines.extend(
            [
                "      try {",
                *guard,
                f"        await {command};",
                f"        result.steps[{index}].status = 'passed';",
                "      } catch (error) {",
                f"        result.steps[{index}].status = 'failed';",
                "        throw error;",
                "      }",
                "    });",
            ]
        )
    lines.extend(
        [
            "  } finally {",
            "    await test.info().attach('tapper-result', { contentType: 'application/json', body: Buffer.from(JSON.stringify(result)) });",
            "  }",
            "});",
            "",
        ]
    )
    return GeneratedPlaywright(
        "\n".join(lines),
        frozen,
        hashlib.sha256(frozen).hexdigest(),
        hashlib.sha256(literal(inputs).encode()).hexdigest(),
        tuple(mapping),
    )
