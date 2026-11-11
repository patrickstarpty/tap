"""Deterministic fixed-template Playwright generation, with structural ID mapping."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from tap_platform.lca.definition import (
    DebugDefinition,
    ParameterReference,
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


def generate_playwright(
    definition: DebugDefinition, parameters: Mapping[str, object] | None = None
) -> GeneratedPlaywright:
    definition = validate_definition(definition)
    inputs = resolve_parameters(definition, parameters)
    frozen = canonical_bytes(definition)
    targets = {t.id: t for t in definition.targets}
    lines = [
        "import { test, expect } from '@playwright/test';",
        "test('LCA debug flow', async ({ page }) => {",
    ]
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
            target = targets[step.target_id]
            if target.kind == "test_id":
                locator = f"page.getByTestId({literal(target.value)})"
            elif target.kind == "role":
                locator = f"page.getByRole({literal(target.value)}, {{ name: {literal(target.name)}, exact: true }})"
            else:
                locator = f"page.locator({literal('css=' + target.value)})"
        if step.operation == "navigate":
            command = f"page.goto({literal(args['url'])}, {options})"
        elif step.operation == "click":
            command = f"{locator}.click({options})"
        elif step.operation in {"fill", "select"}:
            method = "fill" if step.operation == "fill" else "selectOption"
            command = f"{locator}.{method}({literal(args['value'])}, {options})"
        elif step.operation == "assert_text":
            command = (
                f"expect({locator}).toHaveText({literal(args['text'])}, {options})"
            )
        else:
            command = f"expect(page).toHaveURL({literal(args['url'])}, {options})"
        lines.extend(
            [
                "      try {",
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
