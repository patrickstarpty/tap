import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { DefinitionEditor } from "./DefinitionEditor";
import type { LcaDefinition } from "./client";

const base: LcaDefinition = {
  schema_version: "lca-debug-v1",
  allowed_origins: ["https://example.com"],
  targets: [{ id: "button", kind: "test_id", value: "button" }],
  parameters: [],
  steps: [
    {
      id: "open",
      operation: "open_page",
      arguments: { page_id: "details", url: "https://example.com/details" },
      timeout_ms: 10000,
    },
    {
      id: "switch",
      operation: "switch_page",
      arguments: { page_id: "details" },
      timeout_ms: 10000,
    },
    {
      id: "act",
      operation: "click",
      target_id: "button",
      arguments: {},
      timeout_ms: 10000,
    },
  ],
};
function Editor({ initial = base }: { initial?: LcaDefinition }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <DefinitionEditor value={value} onChange={setValue} />
      <output data-testid="definition">{JSON.stringify(value)}</output>
    </>
  );
}
const current = () =>
  JSON.parse(screen.getByTestId("definition").textContent!) as LcaDefinition;

it("shows the effective page and preserves implicit legacy bindings until explicitly changed", () => {
  render(<Editor />);
  fireEvent.click(screen.getByRole("button", { name: "Edit action 3: Click" }));
  expect(screen.getByLabelText("Step act page context")).toHaveValue("");
  expect(screen.getByLabelText("Step act page context")).toHaveTextContent(
    "Active page (details)",
  );
  expect(current().steps[2].page_id).toBeUndefined();
  fireEvent.change(screen.getByLabelText("Step act page context"), {
    target: { value: "main" },
  });
  expect(current().steps[2].page_id).toBe("main");
  expect(screen.getByText("Selected page: main")).toBeVisible();
  fireEvent.change(screen.getByLabelText("Step act page context"), {
    target: { value: "" },
  });
  expect(current().steps[2].page_id).toBeUndefined();
});

it("offers explicit page creation and lifecycle destinations without parameter coercion", () => {
  render(<Editor />);
  expect(
    screen.queryByLabelText("Step open page context"),
  ).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Step open page_id value"), {
    target: { value: "review" },
  });
  expect(current().steps[0].arguments.page_id).toBe("review");
  expect(
    screen.queryByLabelText("Step open page_id value source"),
  ).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", { name: "Edit action 2: Switch page" }),
  );
  const destination = screen.getByLabelText("Step switch page_id value");
  expect(destination).toHaveValue("details");
  expect(
    within(destination).getByRole("option", { name: "details (unavailable)" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("not open");
  fireEvent.change(destination, { target: { value: "review" } });
  expect(current().steps[1].arguments).toEqual({ page_id: "review" });
});

it("keeps exact dialog text and makes prompt input explicit without dropping incompatible data", () => {
  render(
    <Editor
      initial={{
        ...base,
        steps: [
          {
            id: "dialog",
            operation: "click_dialog",
            target_id: "button",
            arguments: {
              action: "accept",
              type: "prompt",
              message: '  Name?\n"Exact"',
              prompt_text: "  Alice  ",
            },
            timeout_ms: 10000,
          },
        ],
      }}
    />,
  );
  expect(screen.getByLabelText("Step dialog message value")).toHaveValue(
    '  Name?\n"Exact"',
  );
  fireEvent.change(screen.getByLabelText("Step dialog prompt_text value"), {
    target: { value: ' Bob\n"B" ' },
  });
  expect(current().steps[0].arguments.prompt_text).toBe(' Bob\n"B" ');
  fireEvent.change(screen.getByLabelText("Step dialog action value"), {
    target: { value: "dismiss" },
  });
  expect(current().steps[0].arguments.prompt_text).toBe(' Bob\n"B" ');
  expect(screen.getByRole("alert")).toHaveTextContent("Prompt text requires");
  fireEvent.click(screen.getByLabelText("Step dialog enable prompt_text"));
  expect(current().steps[0].arguments).toEqual({
    action: "dismiss",
    type: "prompt",
    message: '  Name?\n"Exact"',
  });
});

it("shows invalid closed page bindings rather than silently selecting an available page", () => {
  render(
    <Editor
      initial={{
        ...base,
        steps: [
          base.steps[0],
          {
            id: "close",
            operation: "close_page",
            arguments: { page_id: "details" },
            timeout_ms: 10000,
          },
          { ...base.steps[2], page_id: "details" },
        ],
      }}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Edit action 3: Click" }));
  expect(screen.getByLabelText("Step act page context")).toHaveValue("details");
  expect(screen.getByRole("alert")).toHaveTextContent("not open");
  expect(current().steps[2].page_id).toBe("details");
});

it("rejects noncanonical uppercase or hyphenated page IDs without rewriting them", () => {
  render(<Editor />);
  const id = screen.getByLabelText("Step open page_id value");
  for (const invalid of ["Details", "order-details"]) {
    fireEvent.change(id, { target: { value: invalid } });
    expect(current().steps[0].arguments.page_id).toBe(invalid);
    expect(screen.getByRole("alert")).toHaveTextContent("lowercase");
  }
  fireEvent.change(id, { target: { value: "order_details" } });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
