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

it("configures typed scroll, wait, checked and drag arguments", () => {
  render(<StatefulEditor />);
  const operation = screen.getByLabelText("Step open operation");
  const current = () =>
    JSON.parse(screen.getByTestId("value").textContent!).steps[0];
  fireEvent.change(operation, { target: { value: "scroll" } });
  expect(current().arguments).toEqual({ delta_x: 0, delta_y: 600 });
  expect(
    screen.getByRole("option", { name: "Page (no target)" }),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Step open delta_y value"), {
    target: { value: "-200" },
  });
  expect(current().arguments).toEqual({ delta_x: 0, delta_y: -200 });
  fireEvent.change(screen.getByLabelText("Step open delta_y value"), {
    target: { value: "10001" },
  });
  expect(current().arguments.delta_y).toBe("10001");
  fireEvent.change(screen.getByLabelText("Step open delta_x value"), {
    target: { value: "20" },
  });
  expect(current().arguments.delta_y).toBe("10001");
  expect(screen.getByRole("alert")).toHaveTextContent("10000");
  fireEvent.change(operation, { target: { value: "wait_for" } });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Step open value"), {
    target: { value: "hidden" },
  });
  expect(current().arguments).toEqual({ state: "hidden" });
  fireEvent.change(operation, { target: { value: "assert_checked" } });
  fireEvent.change(screen.getByLabelText("Step open value"), {
    target: { value: "false" },
  });
  expect(current().arguments).toEqual({ checked: false });
  fireEvent.change(operation, { target: { value: "drag_drop" } });
  expect(
    screen.getByLabelText("Step open destination target"),
  ).toBeInTheDocument();
  expect(
    screen.queryByLabelText("Step open value source"),
  ).not.toBeInTheDocument();
});

it("filters numeric and boolean parameters and protects referenced targets", () => {
  const change = vi.fn();
  const value: LcaDefinition = {
    ...definition,
    targets: [
      { id: "frame", kind: "test_id", value: "frame" },
      { id: "item", kind: "test_id", value: "item", frame_target_id: "frame" },
      { id: "drop", kind: "test_id", value: "drop" },
    ],
    parameters: [
      ...definition.parameters,
      { name: "flag", type: "boolean", required: true },
      { name: "text", type: "string", required: true },
    ],
    steps: [
      {
        id: "scroll",
        operation: "scroll",
        arguments: { delta_x: 0, delta_y: { parameter: "count" } },
        timeout_ms: 10000,
      },
      {
        id: "drag",
        operation: "drag_drop",
        target_id: "item",
        arguments: { destination_target_id: "drop" },
        timeout_ms: 10000,
      },
    ],
  };
  render(<DefinitionEditor value={value} onChange={change} />);
  const select = screen.getByLabelText("Step scroll delta_y parameter");
  expect(select).toHaveTextContent("count");
  expect(select).not.toHaveTextContent("flag");
  expect(select).not.toHaveTextContent("text");
  expect(
    screen.getByRole("button", { name: "Remove target frame", hidden: true }),
  ).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "Remove target drop", hidden: true }),
  ).toBeDisabled();
});

it("allows typing a negative scroll distance while preserving invalid intermediate values for schema rejection", async () => {
  const user = userEvent.setup();
  render(<StatefulEditor />);
  fireEvent.change(screen.getByLabelText("Step open operation"), {
    target: { value: "scroll" },
  });
  const distance = screen.getByLabelText("Step open delta_y value");
  await user.clear(distance);
  await user.type(distance, "-250");
  expect(
    JSON.parse(screen.getByTestId("value").textContent!).steps[0].arguments
      .delta_y,
  ).toBe(-250);
});

it("offers only drag destinations in the source frame", () => {
  render(
    <DefinitionEditor
      value={{
        ...definition,
        targets: [
          { id: "frame", kind: "test_id", value: "frame" },
          {
            id: "source",
            kind: "test_id",
            value: "source",
            frame_target_id: "frame",
          },
          {
            id: "inside",
            kind: "test_id",
            value: "inside",
            frame_target_id: "frame",
          },
          { id: "outside", kind: "test_id", value: "outside" },
        ],
        steps: [
          {
            id: "drag",
            operation: "drag_drop",
            target_id: "source",
            arguments: { destination_target_id: "inside" },
            timeout_ms: 10000,
          },
        ],
      }}
      onChange={vi.fn()}
    />,
  );
  const destination = screen.getByLabelText("Step drag destination target");
  expect(destination).toHaveTextContent("inside");
  expect(destination).not.toHaveTextContent("outside");
});

it("exposes exact rendered text separately without normalizing the value", () => {
  render(<StatefulEditor />);
  const operation = screen.getByLabelText("Step open operation");
  expect(
    screen.getByRole("option", { name: "Exact rendered text assertion" }),
  ).toHaveValue("assert_text_exact");
  expect(screen.getByRole("option", { name: "Assert text" })).toHaveValue(
    "assert_text",
  );
  fireEvent.change(operation, { target: { value: "assert_text_exact" } });
  fireEvent.change(screen.getByLabelText("Step open value"), {
    target: { value: "  exact\n text  " },
  });
  expect(
    JSON.parse(screen.getByTestId("value").textContent!).steps[0],
  ).toMatchObject({
    operation: "assert_text_exact",
    arguments: { text: "  exact\n text  " },
  });
});

it("adds an exact result assertion using its actual argument field label", async () => {
  const user = userEvent.setup();
  function ResultEditor() {
    const [value, setValue] = useState<LcaDefinition>({
      ...definition,
      targets: [{ id: "result", kind: "test_id", value: "result" }],
      bdd_steps: [
        {
          id: "then",
          keyword: "Then",
          description: "Verify result",
          action_ids: ["open"],
        },
      ],
    });
    return (
      <>
        <DefinitionEditor value={value} onChange={setValue} />
        <output data-testid="edited-result">{JSON.stringify(value)}</output>
      </>
    );
  }
  render(<ResultEditor />);
  await user.click(
    screen.getByRole("button", { name: "Add assertion" }),
  );
  await user.selectOptions(
    screen.getByRole("combobox", { name: /^Step .+ operation$/u }),
    "assert_text_exact",
  );
  await user.selectOptions(
    screen.getByRole("combobox", { name: /^Step .+ target$/u }),
    "result",
  );
  await user.type(
    screen.getByRole("textbox", { name: /^Step .+ value$/u }),
    "AC-42 / 120 / MATCH",
  );
  const edited = JSON.parse(screen.getByTestId("edited-result").textContent!);
  expect(edited.steps[1]).toMatchObject({
    operation: "assert_text_exact",
    target_id: "result",
    arguments: { text: "AC-42 / 120 / MATCH" },
  });
  expect(edited.bdd_steps[0].action_ids).toContain(edited.steps[1].id);
});
