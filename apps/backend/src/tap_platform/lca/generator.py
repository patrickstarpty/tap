"""Deterministic fixed-template Playwright generation, with structural ID mapping."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from tap_platform.lca.document_acceptance import DOCUMENT_ACCEPTANCE_HELPER

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


# Fixed, self-contained DOM association proof shared semantically with AA.
ASSOCIATED_CONTROL_HELPER = r"""
function associatedControlState(nodes, {target = null} = {}) {
  const visible = node => Boolean(node?.getClientRects().length && getComputedStyle(node).visibility !== 'hidden' && !node.closest('[hidden],[inert],[aria-hidden="true"]'));
  const normalize = text => String(text || '').normalize('NFKC').replace(/\s+/g, ' ').trim();
  const sensitive = text => /password|passcode|secret|token|csrf|xsrf|session|auth|credential|otp|one.?time|verification|security|cvv|cvc|card|payment|bank|account|routing|iban|swift|private|api.?key|access.?key|ssn|social.?security|passport|licen[cs]e|pin(?:$|[^a-z]|code)/i.test(text);
  const unique = selector => document.querySelectorAll(selector).length === 1;
  const selectorFor = node => {
    if (node.id && unique(`#${CSS.escape(node.id)}`)) return `#${CSS.escape(node.id)}`;
    for (const attribute of ['data-testid', 'name', 'aria-label']) {
      const value = node.getAttribute(attribute);
      if (!value) continue;
      const candidate = `${node.tagName.toLowerCase()}[${attribute}=${JSON.stringify(value)}]`;
      if (unique(candidate)) return candidate;
    }
    const parts = [];
    for (let element = node; element && element !== document.body; element = element.parentElement) {
      const siblings = [...(element.parentElement?.children || [element])].filter(item => item.tagName === element.tagName);
      parts.unshift(`${element.tagName.toLowerCase()}:nth-of-type(${siblings.indexOf(element) + 1})`);
      if (unique(parts.join(' > '))) return parts.join(' > ');
    }
    return `body > ${parts.join(' > ')}`;
  };
  const state = node => {
    const hidden = node?.matches('input[type="hidden"]');
    const group = hidden ? node.parentElement : node?.closest('[data-name]');
    if (!group || ['BODY','HTML','FORM'].includes(group.tagName) || !visible(group)
      || (!hidden && !visible(node)) || group.querySelectorAll('*').length > 120
      || group.querySelector('[data-name]') || group.parentElement?.closest('[data-name]') || group.closest('[data-secret],[data-private],[data-sensitive]')) return null;
    const controls = [...group.querySelectorAll('input,select,textarea')];
    if (controls.length !== 1) return null;
    const input = controls[0];
    if (input.type !== 'hidden' || input.parentElement !== group || (hidden && input !== node)) return null;
    const name = input.getAttribute('name') || '';
    if (!/^[A-Za-z][A-Za-z0-9_-]{0,79}$/.test(name) || group.getAttribute('data-name') !== name
      || sensitive([name,input.id,input.getAttribute('autocomplete'),input.getAttribute('aria-label')].join(' '))
      || input.matches('[data-secret],[data-private],[data-sensitive]')) return null;
    const attributes = ['data-value','data-option-value','data-optionvalue'];
    const choices = [...group.querySelectorAll(attributes.map(attribute=>`[${attribute}]`).join(','))];
    if (choices.length < 2 || choices.length > 12 || choices.some(choice=>!visible(choice))) return null;
    let valueAttribute;
    const options = [];
    for (const choice of choices) {
      const present = attributes.filter(attribute=>choice.hasAttribute(attribute));
      if (present.length !== 1 || (valueAttribute && valueAttribute !== present[0])) return null;
      valueAttribute = present[0];
      const value = choice.getAttribute(valueAttribute);
      const label = normalize(choice.innerText);
      // Short enum codes only, never opaque payloads, identifiers or credentials.
      if (!/^[A-Za-z0-9][A-Za-z0-9 _.-]{0,31}$/.test(value) || /\d{9,}|[A-Fa-f0-9]{16,}/.test(value)
        || !label || label.length > 100 || sensitive(label)
        || choices.some(other=>other !== choice && choice.contains(other))) return null;
      options.push({label,value});
    }
    if (new Set(options.map(option=>option.value)).size !== options.length
      || new Set(options.map(option=>normalize(option.label).toLowerCase())).size !== options.length) return null;
    const labelled = (group.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean).map(id=>document.getElementById(id));
    const headings = [...group.querySelectorAll('legend,label,p,h1,h2,h3,h4,h5,h6,[role="heading"]')]
      .filter(heading=>visible(heading) && !choices.some(choice=>choice.contains(heading)) && normalize(heading.innerText));
    const label = normalize(group.getAttribute('aria-label')
      || (labelled.length && labelled.every(visible) ? labelled.map(node=>node.innerText).join(' ') : '')
      || (headings.length === 1 ? headings[0].innerText : ''));
    if (!label || label.length > 120 || sensitive(label) || normalize(group.innerText).length > 600) return null;
    // Read the hidden payload only after proving its ordinary visible enum context.
    const value = String(input.value);
    const selected = options.filter(option=>option.value === value);
    if (value !== '' && selected.length !== 1) return null;
    return {tag:'input',type:'hidden',id:input.id,name,selector:selectorFor(input),
      context:'associated_selection_state',label,value,selected_label:selected[0]?.label ?? null,
      options,readonly:true,disabled:Boolean(input.disabled),
      association:{kind:'named_widget_enum',group_selector:selectorFor(group),group_label:label,value_attribute:valueAttribute},
    };
  };
  if (!Array.isArray(nodes)) return state(nodes);
  return nodes.slice(0,200).map(state).filter(Boolean)
    .sort((a,b)=>Number(Boolean(target && document.querySelector(b.association.group_selector)?.contains(target)))
      - Number(Boolean(target && document.querySelector(a.association.group_selector)?.contains(target))))
    .slice(0,12);
}
"""


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
      if (args.attribute === 'selected_label' || String(type).toLowerCase() === 'hidden') {
        if (typeof args.equals !== 'string' || (args.attribute !== 'selected_label' && args.attribute !== 'value')) throw new Error('Associated selection requires exact read');
        const state = await locator.evaluate(associatedControlState, undefined, options());
        if (!state || state.selected_label === null) throw new Error('No safe associated selected state');
        value = args.attribute === 'selected_label' ? state.selected_label : state.value;
      }
      else if (args.attribute === 'text') value = await locator.innerText(options());
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
      node.innerText, node.textContent, ...(node.getAttribute('aria-labelledby') || '').split(/\s+/).map(id=>document.getElementById(id)?.textContent)].filter(Boolean),
    scopes: [node.closest('label'), node.parentElement, node.parentElement?.parentElement]
      .filter(n=>n && !['BODY','HTML'].includes(n.tagName) && (n.textContent || '').length <= 600)
      .map(n=>(n.innerText || n.textContent).trim())
      .filter(text=>text !== (node.innerText || node.textContent || '').trim()).slice(0,1),
    type: node.getAttribute('type'), autocomplete: node.getAttribute('autocomplete') || '',
  }), {timeout});
  if (String(facts.type).toLowerCase() === 'hidden') {
    const state = await locator.evaluate(associatedControlState);
    if (!state) throw new Error('hidden target has no safe associated selection state');
    if (contract.type && !['read','snapshot'].includes(contract.type)) throw new Error('hidden selection state is read-only');
    facts.labels = [state.id,state.name,state.label].filter(Boolean);
    facts.scopes = [state.association.group_label];
  }
  const matches = (text, expected) => {
    const value = String(text).normalize('NFKC').toLowerCase().replace(/\s+/g,' ').trim();
    const term = String(expected).normalize('NFKC').toLowerCase().replace(/\s+/g,' ').trim();
    const escaped = term.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    return Boolean(term) && new RegExp('(^|[^\\p{L}\\p{N}])'+escaped+'($|[^\\p{L}\\p{N}])','u').test(value);
  };
  if (String(facts.type).toLowerCase() === 'password' || /password|one-time-code|cc-number|cc-csc/i.test(facts.autocomplete)) throw new Error('sensitive target');
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
    contract = (
        definition.source_acceptance.acceptance_contract
        if definition.source_acceptance
        else None
    )
    lines = [
        "import { test, expect } from '@playwright/test';",
        "test('LCA debug flow', async ({ page }) => {",
    ]
    if any(
        s.operation == "assert_read" or s.target_contract is not None
        for s in definition.steps
    ):
        lines.extend(ASSOCIATED_CONTROL_HELPER.strip("\n").split("\n"))
    if any(s.operation == "assert_read" for s in definition.steps):
        lines.extend(READ_ASSERTION_HELPER.strip("\n").split("\n"))
    if contract is not None or any(
        step.target_contract is not None for step in definition.steps
    ):
        lines.extend(TARGET_CONTRACT_HELPER.strip("\n").split("\n"))
    contract = (
        definition.source_acceptance.acceptance_contract
        if definition.source_acceptance
        else None
    )
    if contract is not None:
        lines.extend(DOCUMENT_ACCEPTANCE_HELPER.strip("\n").split("\n"))
        lines.extend(
            [
                f"  const acceptanceContract = {literal(contract.model_dump(exclude_unset=True))};",
                "  const acceptanceProofs = new Map();",
                "  let acceptanceError = false;",
                "  const proofTargets = {"
                + ",".join(
                    literal(key) + ":" + target_locator(key, targets) for key in targets
                )
                + "};",
                "  const proofPage = {frames:()=>page.frames(),locator:key=>proofTargets[key]};",
                "  const documentTracker = createDocumentProvenance(proofPage);",
                "  const guardTarget = async (scope,action) => assertTargetContract(scope.locator(action.selector),action,100);",
                "  const readValue = async (scope,action) => readAssertion(scope.locator(action.selector),action,100);",
            ]
        )
    elif any(step.operation == "observe" for step in definition.steps):
        lines.extend(DOCUMENT_ACCEPTANCE_HELPER.strip("\n").split("\n"))
        lines.append("  const documentTracker = createDocumentProvenance(page);")
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
        current_action = {
            "type": "read"
            if step.operation == "assert_read"
            else "snapshot"
            if step.operation in {"observe", "wait_for"}
            or step.operation.startswith("assert_")
            else step.operation
            if step.operation
            in {"navigate", "click", "fill", "select", "press", "scroll"}
            else "fill",
            "selector": step.target_id,
            "lca_step_id": step.id,
            **args,
            **(
                step.target_contract.model_dump(exclude_unset=True)
                if step.target_contract
                else {}
            ),
        }
        if step.operation == "observe":
            command = "documentTracker.snapshot()"
        elif step.operation == "navigate":
            command = f"page.goto({literal(args['url'])}, {options})"
        elif step.operation == "click":
            command = f"{locator}.click({options})"
        elif step.operation in {"fill", "select"}:
            method = "fill" if step.operation == "fill" else "selectOption"
            command = f"{locator}.{method}({literal(args['value'])}, {options})"
        elif step.operation == "assert_read":
            command = f"readAssertion({locator}, {literal(args)}, {step.timeout_ms})"
            if contract is not None:
                command = f"documentTracker.read(currentAction, async () => {command}).then(proof => {{ readProof = {{...proof, step_id: {literal(step.id)}}}; return proof.value; }})"
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
                f"        await assertTargetContract({locator}, {literal({**step.target_contract.model_dump(exclude_unset=True), 'type': 'read' if step.operation == 'assert_read' else step.operation})}, {step.timeout_ms});"
            ]
        lines.extend(
            [
                "      try {",
                *(
                    [
                        f"        const currentAction = {literal(current_action)};",
                        "        const beforeDocuments = await documentTracker.snapshot();",
                        "        let readProof = null;",
                    ]
                    if contract is not None
                    else []
                ),
                *guard,
                f"        await {command};",
                *(
                    [
                        "        try { await recordAcceptance(acceptanceContract, acceptanceProofs, currentAction, beforeDocuments, await documentTracker.snapshot(), readProof); if (currentAction.type === 'click') await recheckPendingAcceptance(proofPage, acceptanceContract, acceptanceProofs, documentTracker); } catch { acceptanceError = true; acceptanceProofs.clear(); }",
                    ]
                    if contract is not None
                    else []
                ),
                f"        result.steps[{index}].status = 'passed';",
                "      } catch (error) {",
                *(
                    ["        acceptanceError = true; acceptanceProofs.clear();"]
                    if contract is not None
                    else []
                ),
                f"        result.steps[{index}].status = 'failed';",
                "        throw error;",
                "      }",
                "    });",
            ]
        )
    lines.extend(
        [
            "  } finally {",
            *(
                [
                    "    result.requirements_evidence = {version:1, error:acceptanceError, documents:await documentTracker.snapshot(), fields:Object.fromEntries(acceptanceContract.fields.map(field=>{const proof=acceptanceProofs.get(field.id);return [field.id,proof ? {...Object.fromEntries(['value','source_frame_id','source_document_id','source_url','state','destination'].filter(key=>proof[key] !== undefined).map(key=>[key,proof[key]])),step_id:proof.step_id || proof.read_action?.lca_step_id} : null];}))};",
                ]
                if contract is not None
                else []
            ),
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
