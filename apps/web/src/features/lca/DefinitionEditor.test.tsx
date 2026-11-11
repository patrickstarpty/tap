import { useState } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { DefinitionEditor, parseParameterValue } from "./DefinitionEditor";
import type { LcaDefinition } from "./client";

const definition: LcaDefinition = {
  schema_version: "lca-debug-v1",
  allowed_origins: ["https://example.com"],
  targets: [],
  parameters: [{ name: "count", type: "integer", required: false }],
  steps: [
    {
      id: "open",
      operation: "navigate",
      arguments: { url: "https://example.com" },
      timeout_ms: 10000,
    },
  ],
};
it("applies a typed default only after validation", async () => {
  const change = vi.fn();
  const user = userEvent.setup();
  render(<DefinitionEditor value={definition} onChange={change} />);
  await user.click(screen.getByText("Parameters (1)"));
  fireEvent.change(screen.getByLabelText("Default for count"), {
    target: { value: "abc" },
  });
  await user.click(
    screen.getByRole("button", { name: "Apply default for count" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("integer");
  expect(change).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Default for count"), {
    target: { value: "4" },
  });
  await user.click(
    screen.getByRole("button", { name: "Apply default for count" }),
  );
  expect(change.mock.calls[0][0].parameters[0].default).toBe(4);
});
it("rejects ambiguous boolean and fractional integer inputs", () => {
  expect(() => parseParameterValue("boolean", "yes")).toThrow();
  expect(() => parseParameterValue("integer", "1.5")).toThrow();
  expect(parseParameterValue("boolean", "false")).toBe(false);
  expect(parseParameterValue("string", "")).toBe("");
});

function StatefulEditor() {
  const [value, setValue] = useState<LcaDefinition>({
    ...definition,
    bdd_steps: [
      {
        id: "given",
        keyword: "Given",
        description: "Open insurance website",
        action_ids: ["open"],
      },
      {
        id: "when",
        keyword: "When",
        description: "Choose a product",
        action_ids: [],
      },
    ],
  });
  return (
    <>
      <DefinitionEditor value={value} onChange={setValue} />
      <output data-testid="value">{JSON.stringify(value)}</output>
    </>
  );
}
it("edits BDD steps and actions through contextual panes without executing", async () => {
  const user = userEvent.setup();
  render(<StatefulEditor />);
  expect(screen.getByRole("navigation", { name: "BDD steps" })).toBeVisible();
  await user.click(
    screen.getByRole("button", { name: /When Choose a product/ }),
  );
  expect(screen.queryByLabelText("Step open value")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Add action" }));
  expect(screen.getByLabelText(/Step step_.* operation/)).toHaveValue("click");
  fireEvent.change(screen.getByLabelText("BDD description"), {
    target: { value: "Choose MyTerm Plus" },
  });
  expect(
    JSON.parse(screen.getByTestId("value").textContent!).bdd_steps[1]
      .description,
  ).toBe("Choose MyTerm Plus");
});
it("reorders BDD steps and preserves action identity", async () => {
  const user = userEvent.setup();
  render(<StatefulEditor />);
  await user.click(
    screen.getByRole("button", { name: /When Choose a product/ }),
  );
  await user.click(screen.getByRole("button", { name: "Add action" }));
  const added = JSON.parse(screen.getByTestId("value").textContent!).steps[1]
    .id;
  await user.click(screen.getByRole("button", { name: "Move BDD step up" }));
  const value = JSON.parse(screen.getByTestId("value").textContent!);
  expect(value.bdd_steps[0].id).toBe("when");
  expect(value.steps.map((s: { id: string }) => s.id)).toEqual([added, "open"]);
});
it("materializes BDD metadata only after a legacy settings edit", async () => {
  const change = vi.fn();
  render(<DefinitionEditor value={definition} onChange={change} />);
  expect(change).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Allowed origins (one per line)"), {
    target: { value: "https://example.org" },
  });
  expect(change.mock.calls[0][0].bdd_steps[0].action_ids).toEqual(["open"]);
});
