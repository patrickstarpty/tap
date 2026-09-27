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
    "Test Analytics",
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
it("places document review inside the existing Library", () => {
  render(<TapProductPrototype />);
  fireEvent.click(screen.getByRole("button", { name: "Library" }));
  fireEvent.click(screen.getByRole("tab", { name: "Documents" }));
  expect(
    screen.getByRole("button", {
      name: "Review Life underwriting guide · v1.2.md",
    }),
  ).toBeVisible();
});

it("reviews a flowchart image with its nodes and directed branch", () => {
  render(<TapProductPrototype />);
  fireEvent.click(screen.getByRole("button", { name: "Library" }));
  fireEvent.click(screen.getByRole("tab", { name: "Documents" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Review Approval flowchart.png" }),
  );

  expect(screen.getByRole("img", { name: "Approval flowchart" })).toBeVisible();
  expect(screen.getByText("Submit request → Manager approval")).toBeVisible();
  expect(screen.getByText("Condition: Amount exceeds 1000")).toBeVisible();
  expect(
    screen.getByLabelText("Arrow direction matches the image"),
  ).toBeVisible();
});

it("hands submitted work to an independent reviewer and only publishes an approved fixture", () => {
  const view = render(<TapProductPrototype />);
  fireEvent.click(screen.getByRole("button", { name: "Library" }));
  fireEvent.click(screen.getByRole("tab", { name: "Documents" }));
  fireEvent.click(
    screen.getByRole("button", {
      name: "Review Life underwriting guide · v1.2.md",
    }),
  );
  expect(
    screen.getByRole("button", { name: "Submit for review" }),
  ).toBeDisabled();
  for (const label of [
    "Text and key values match the original",
    "Source locations are correct",
    "Version and scope are correct",
    "Conditions and exceptions match the original",
  ])
    fireEvent.click(screen.getByLabelText(label));
  fireEvent.click(screen.getByRole("button", { name: "Submit for review" }));
  expect(screen.getByRole("status")).toHaveTextContent(
    "Awaiting independent review",
  );
  expect(screen.queryByRole("button", { name: "Approve review" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  fireEvent.click(
    screen.getByRole("button", {
      name: "View Health disclosure policy · approved.md",
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Publish" }));
  expect(screen.getByRole("status")).toHaveTextContent(
    "Published to knowledge library",
  );
  fireEvent.click(screen.getByRole("button", { name: "Ask Tapper" }));
  expect(
    screen.getByRole("checkbox", {
      name: /Health disclosure policy · approved.md/,
    }),
  ).toBeChecked();
  view.unmount();
  render(<TapProductPrototype />);
  expect(
    screen.getByRole("checkbox", {
      name: /Health disclosure policy · approved.md/,
    }),
  ).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Library" }));
  fireEvent.click(screen.getByRole("tab", { name: "Documents" }));
  fireEvent.click(
    screen.getByRole("button", {
      name: "View Health disclosure policy · approved.md",
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Withdraw" }));
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  fireEvent.click(screen.getByRole("button", { name: "New chat" }));
  expect(
    screen.queryByRole("checkbox", {
      name: /Health disclosure policy · approved.md/,
    }),
  ).toBeNull();
}, 15_000);

it("keeps the full checklist when replacing a failed document", async () => {
  const view = render(<TapProductPrototype />);
  fireEvent.click(screen.getByRole("button", { name: "Library" }));
  fireEvent.click(screen.getByRole("tab", { name: "Documents" }));
  fireEvent.click(
    screen.getByRole("button", {
      name: "View Underwriting rules — scanned.pdf",
    }),
  );
  const replacement = new File(["# text"], "replacement.md", {
    type: "text/markdown",
  });
  fireEvent.change(screen.getByLabelText("Replace file"), {
    target: { files: [replacement] },
  });
  expect(
    screen.getByRole("heading", { name: "Processing document…" }),
  ).toBeVisible();
  await waitFor(
    () =>
      expect(
        screen.getByLabelText("Conditions and exceptions match the original"),
      ).toBeVisible(),
    { timeout: 2_000 },
  );
  view.unmount();
  render(<TapProductPrototype />);
  fireEvent.click(screen.getByRole("button", { name: "Library" }));
  fireEvent.click(screen.getByRole("tab", { name: "Documents" }));
  expect(
    screen.getByRole("button", { name: "Review replacement.md" }),
  ).toBeVisible();
});

it("corrects flow direction and condition with a fresh review revision", () => {
  render(<TapProductPrototype />);
  fireEvent.click(screen.getByRole("button", { name: "Library" }));
  fireEvent.click(screen.getByRole("tab", { name: "Documents" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Review Approval flowchart.png" }),
  );
  fireEvent.click(screen.getByLabelText("Arrow direction matches the image"));
  fireEvent.click(screen.getByRole("button", { name: "Edit flowchart" }));
  fireEvent.click(screen.getByRole("button", { name: "Reverse direction" }));
  fireEvent.change(screen.getByLabelText("Branch condition"), {
    target: { value: "Return for correction" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save corrections" }));
  expect(screen.getByText("Manager approval → Submit request")).toBeVisible();
  expect(screen.getByText("Condition: Return for correction")).toBeVisible();
  expect(
    screen.getByLabelText("Arrow direction matches the image"),
  ).not.toBeChecked();
  expect(
    screen.getByRole("button", { name: "Submit for review" }),
  ).toBeDisabled();
  expect(screen.getByText(/flowchart_corrected/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Review Approval flowchart.png" }),
  );
  expect(screen.getByText("Manager approval → Submit request")).toBeVisible();
});
