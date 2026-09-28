import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { TapProductPrototype } from "./TapProductPrototype";
beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  HTMLElement.prototype.scrollIntoView = vi.fn();
});
it("retains all baseline modules in one product shell", () => {
  render(<TapProductPrototype />);
  for (const name of [
    "Tapper",
    "Test Management",
    "Test Insights",
    "Low Code Automation",
  ])
    expect(screen.getByRole("button", { name })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Test Management" }));
  expect(
    screen.getByRole("heading", { name: "Test Management" }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Low Code Automation" }));
  expect(
    screen.getByRole("heading", { name: "Low Code Automation" }),
  ).toBeVisible();
  expect(
    screen.queryByText(/交互原型|数据场景|演示上传|填入示例问题/),
  ).not.toBeInTheDocument();
});
it("places chunk management inside the existing Library", () => {
  render(<TapProductPrototype />);
  fireEvent.click(screen.getByRole("button", { name: "Library" }));
  fireEvent.click(screen.getByRole("tab", { name: "Documents" }));
  expect(
    screen.getByRole("button", {
      name: "Manage chunks Life underwriting guide · v1.2.md",
    }),
  ).toBeVisible();
});

it("uses one review workbench for long text, PDF and Excel sources", () => {
  render(<TapProductPrototype />);
  fireEvent.click(screen.getByRole("button", { name: "Library" }));
  fireEvent.click(screen.getByRole("tab", { name: "Documents" }));
  for (const [name, location] of [
    ["Life underwriting guide · v1.2.md", "4. Health disclosure"],
    ["Underwriting evidence.pdf", "Page 4"],
    ["Premium rates.xlsx", "Rates!A4:C4"],
  ]) {
    fireEvent.click(
      screen.getByRole("button", { name: `Manage chunks ${name}` }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Original document" }));
    expect(
      screen.getByRole("navigation", { name: "Document outline" }),
    ).toBeVisible();
    expect(screen.getByRole("heading", { name: location })).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Mark selected text" }),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
  }
});

it("uses indexed chunks directly and removes disabled documents from sources", async () => {
  render(<TapProductPrototype />);
  fireEvent.click(screen.getByRole("button", { name: "Library" }));
  fireEvent.click(screen.getByRole("tab", { name: "Documents" }));
  fireEvent.click(
    screen.getByRole("button", {
      name: "Manage chunks Life underwriting guide · v1.2.md",
    }),
  );
  expect(
    screen.queryByRole("button", { name: "Publish" }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Ask Tapper" }));
  expect(
    screen.getByRole("checkbox", { name: /Life underwriting guide · v1.2.md/ }),
  ).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Library" }));
  fireEvent.click(screen.getByRole("tab", { name: "Documents" }));
  fireEvent.click(
    screen.getByRole("button", {
      name: "Manage chunks Life underwriting guide · v1.2.md",
    }),
  );
  fireEvent.click(screen.getByLabelText("Select page"));
  fireEvent.click(screen.getByRole("button", { name: "Disable selected" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Ask Tapper" })).toBeDisabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  fireEvent.click(screen.getByRole("button", { name: "New chat" }));
  expect(
    screen.queryByRole("checkbox", {
      name: /Life underwriting guide · v1.2.md/,
    }),
  ).not.toBeInTheDocument();
});

it("processes a replacement document into manageable chunks", async () => {
  const view = render(<TapProductPrototype />);
  fireEvent.click(screen.getByRole("button", { name: "Library" }));
  fireEvent.click(screen.getByRole("tab", { name: "Documents" }));
  fireEvent.click(
    screen.getByRole("button", {
      name: "Manage chunks Underwriting rules — scanned.pdf",
    }),
  );
  const replacement = new File(["# text"], "replacement.md", {
    type: "text/markdown",
  });
  fireEvent.change(screen.getByLabelText("Replace file"), {
    target: { files: [replacement] },
  });
  expect(screen.getByText("Processing document…")).toBeVisible();
  await waitFor(
    () =>
      expect(screen.getByRole("button", { name: "Add chunk" })).toBeVisible(),
    { timeout: 2_000 },
  );
  view.unmount();
  render(<TapProductPrototype />);
  fireEvent.click(screen.getByRole("button", { name: "Library" }));
  fireEvent.click(screen.getByRole("tab", { name: "Documents" }));
  expect(
    screen.getByRole("button", { name: "Manage chunks replacement.md" }),
  ).toBeVisible();
});
