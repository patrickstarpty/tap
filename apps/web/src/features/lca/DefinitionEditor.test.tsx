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
