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

from tap_platform.lca.file_actions import FILE_ACTION_HELPER

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


def target_locator(
    target_id: str, targets: dict[str, Target], scope: str = "page"
) -> str:
    target = targets[target_id]
    if target.frame_target_id is not None:
        scope = (
            target_locator(target.frame_target_id, targets, scope) + ".contentFrame()"
        )
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


PAGE_CONTEXT_HELPER = r"""
  const pages = new Map([['main', page]]);
  let activePageId = 'main';
  const getPage = id => {
    const selected = pages.get(id);
    if (!selected || selected.isClosed()) throw new Error('Page is missing or closed: ' + id);
    return selected;
  };
  const checkPageURL = selected => {
    const value = selected.url();
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || !allowedPageOrigins.includes(url.origin)) throw new Error('Page URL is outside allowed origins');
  };
  const openPage = async (id, url, timeout) => {
    const created = await page.context().newPage();
    try {
      await created.goto(url, {timeout});
      checkPageURL(created);
      pages.set(id, created);
    } catch (error) {
      await created.close().catch(() => {});
      throw error;
    }
  };
  const switchPage = async id => {
    await getPage(id).bringToFront();
    activePageId = id;
  };
  const closePage = async id => {
    await getPage(id).close();
    pages.delete(id);
  };
"""

SCOPED_EVENT_HELPER = r"""
  const scopedEvent = async (source, event, timeout, trigger, consume) => {
    const deadline = Date.now() + timeout;
    const remaining = () => {
      const left = deadline - Date.now();
      if (left <= 0) throw new Error('Timed out waiting for ' + event);
      return left;
    };
    let handler, timer;
    const observed = new Promise((resolve, reject) => {
      handler = value => {
        source.removeListener(event, handler);
        Promise.resolve().then(() => consume(value, remaining)).then(resolve, reject);
      };
      source.on(event, handler);
      timer = setTimeout(() => reject(new Error('Timed out waiting for ' + event)), timeout);
    });
    try {
      const [value] = await Promise.all([observed, trigger(remaining)]);
      return value;
    } finally {
      clearTimeout(timer);
      source.removeListener(event, handler);
    }
  };
"""

POPUP_HELPER = r"""
  const capturePopup = async (source, locator, id, timeout) => {
    let captured;
    try {
      const created = await scopedEvent(source, 'popup', timeout,
        remaining => locator.click({timeout: remaining()}),
        async (popup, remaining) => {
          captured = popup;
          await popup.waitForURL(url => url.href !== 'about:blank', {timeout: remaining(), waitUntil: 'domcontentloaded'});
          checkPageURL(popup);
          return popup;
        });
      pages.set(id, created);
    } catch (error) {
      if (captured && !captured.isClosed()) await captured.close().catch(() => {});
      throw error;
    }
  };
"""

DIALOG_HELPER = r"""
  const clickDialog = async (source, locator, args, timeout) => {
    await scopedEvent(source, 'dialog', timeout,
      remaining => locator.click({timeout: remaining()}),
      async dialog => {
        if (dialog.type() !== args.type || dialog.message() !== args.message) {
          await dialog.dismiss();
          throw new Error('Dialog did not match the expected type and message');
        }
        if (args.action === 'accept') await dialog.accept(args.prompt_text);
        else await dialog.dismiss();
      });
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
    page_context = any(
        s.page_id is not None
        or s.operation in {"open_page", "switch_page", "close_page", "click_popup"}
        for s in definition.steps
    )
    if page_context:
        lines.append(
            f"  const allowedPageOrigins = {literal(definition.allowed_origins)};"
        )
        lines.extend(PAGE_CONTEXT_HELPER.strip("\n").split("\n"))
    if any(s.operation in {"click_popup", "click_dialog"} for s in definition.steps):
        lines.extend(SCOPED_EVENT_HELPER.strip("\n").split("\n"))
    if any(s.operation == "click_popup" for s in definition.steps):
        lines.extend(POPUP_HELPER.strip("\n").split("\n"))
    if any(s.operation == "click_dialog" for s in definition.steps):
        lines.extend(DIALOG_HELPER.strip("\n").split("\n"))
    if any(s.operation in {"upload_file", "download_file"} for s in definition.steps):
        lines.extend(FILE_ACTION_HELPER.strip("\n").split("\n"))
    if any(s.operation == "assert_read" for s in definition.steps):
        lines.extend(READ_ASSERTION_HELPER.strip("\n").split("\n"))
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
        scope = "stepPage" if page_context else "page"
        locator = ""
        if step.target_id is not None:
            locator = target_locator(step.target_id, targets, scope)
        if step.operation == "navigate":
            command = f"{scope}.goto({literal(args['url'])}, {options})"
        elif step.operation == "open_page":
            command = f"openPage({literal(args['page_id'])}, {literal(args['url'])}, {step.timeout_ms})"
        elif step.operation == "switch_page":
            command = f"switchPage({literal(args['page_id'])})"
        elif step.operation == "close_page":
            command = f"closePage({literal(args['page_id'])})"
        elif step.operation == "click_popup":
            command = f"capturePopup({scope}, {locator}, {literal(args['page_id'])}, {step.timeout_ms})"
        elif step.operation == "click_dialog":
            command = (
                f"clickDialog({scope}, {locator}, {literal(args)}, {step.timeout_ms})"
            )
        elif step.operation == "upload_file":
            command = f"uploadFile({locator}, {literal(args)}, {step.timeout_ms})"
        elif step.operation == "download_file":
            command = f"captureDownload({scope}, {locator}, {literal(step.id)}, {step.timeout_ms})"
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
            command = f"expect({scope}).toHaveURL({literal(args['url'])}, {options})"
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
            command = f"{locator}.dragTo({target_locator(destination, targets, scope)}, {options})"
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
                command = f"{scope}.evaluate(([x, y]) => window.scrollBy({{ left: x, top: y, behavior: 'instant' }}), {delta})"
        lines.append("      try {")
        if page_context and step.operation not in {
            "open_page",
            "switch_page",
            "close_page",
        }:
            page_id = (
                literal(step.page_id) if step.page_id is not None else "activePageId"
            )
            lines.append(f"        const stepPage = getPage({page_id});")
        lines.extend(
            [
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
