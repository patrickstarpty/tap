import { render, screen, fireEvent } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ActionArguments } from "./ActionArguments";
import { argumentFields, newAction } from "./bdd";
import type { LcaDefinition } from "./client";

it("edits optional read conditions without dropping exact whitespace", () => {
  const definition: LcaDefinition = {
    schema_version: "lca-debug-v1",
    allowed_origins: ["https://example.com"],
    targets: [],
    parameters: [],
    steps: [],
  };
  const step = newAction("assert_read", definition);
  expect(step.arguments).toEqual({ attribute: "text", nonempty: true });
  const onChange = vi.fn();
  render(
    <ActionArguments
      step={step}
      definition={definition}
      fields={argumentFields(step.operation)}
      onChange={onChange}
    />,
  );
  fireEvent.click(screen.getByLabelText(`Step ${step.id} enable equals`));
  expect(onChange.mock.lastCall?.[0].arguments).toEqual({
    attribute: "text",
    nonempty: true,
    equals: "",
  });
  expect(screen.queryByText("Value source")).toBeNull();
});

it("preserves quoted multiline read values and allows removing optional conditions", () => {
  const definition: LcaDefinition = {
    schema_version: "lca-debug-v1",
    allowed_origins: ["https://example.com"],
    targets: [],
    parameters: [],
    steps: [],
  };
  const step = {
    ...newAction("assert_read", definition),
    arguments: { attribute: "text", equals: "old" },
  };
  const onChange = vi.fn();
  const { rerender } = render(
    <ActionArguments
      step={step}
      definition={definition}
      fields={argumentFields(step.operation)}
      onChange={onChange}
    />,
  );
  fireEvent.change(screen.getByLabelText(`Step ${step.id} equals value`), {
    target: { value: '  "USD"\n12  ' },
  });
  expect(onChange.mock.lastCall?.[0].arguments.equals).toBe('  "USD"\n12  ');
  fireEvent.click(screen.getByLabelText(`Step ${step.id} enable equals`));
  const empty = onChange.mock.lastCall?.[0];
  expect(empty.arguments).toEqual({ attribute: "text" });
  rerender(
    <ActionArguments
      step={empty}
      definition={definition}
      fields={argumentFields(step.operation)}
      onChange={onChange}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("at least one condition");
});
