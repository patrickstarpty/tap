import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TapProductPrototype } from "./TapProductPrototype";
import { setPrototypeFaults } from "./prototype/prototypeFaults";
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
afterEach(() => {
  setPrototypeFaults([]);
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
}, 15_000);

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

function selectUnderwritingSources() {
  fireEvent.click(
    screen.getByRole("checkbox", {
      name: /Life underwriting guide · v1\.2\.md/,
    }),
  );
  fireEvent.click(
    screen.getByRole("checkbox", { name: /Underwriting test rules\.pdf/ }),
  );
}

function askHealthDisclosureQuestion() {
  fireEvent.change(screen.getByLabelText("Message Tapper"), {
    target: { value: "What does the health disclosure rule require?" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}

it("opens citations in the sources panel instead of a dialog", async () => {
  render(<TapProductPrototype />);
  selectUnderwritingSources();
  askHealthDisclosureQuestion();
  await waitFor(
    () =>
      expect(
        screen.getByRole("button", {
          name: "[2] Underwriting test rules.pdf",
        }),
      ).toBeVisible(),
    { timeout: 2_000 },
  );
  fireEvent.click(
    screen.getByRole("button", { name: "[2] Underwriting test rules.pdf" }),
  );
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Citation [2]" })).toBeVisible();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "This source has been updated",
  );
  fireEvent.click(screen.getByRole("button", { name: "Back to sources" }));
  expect(
    screen.getByRole("heading", { name: "Knowledge sources" }),
  ).toBeVisible();
});

it("returns to sources when the conversation changes", async () => {
  render(<TapProductPrototype />);
  selectUnderwritingSources();
  askHealthDisclosureQuestion();
  await waitFor(
    () =>
      expect(
        screen.getByRole("button", {
          name: "[1] Life underwriting guide · v1.2.md",
        }),
      ).toBeVisible(),
    { timeout: 2_000 },
  );
  fireEvent.click(
    screen.getByRole("button", {
      name: "[1] Life underwriting guide · v1.2.md",
    }),
  );
  expect(screen.getByRole("heading", { name: "Citation [1]" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "New chat" }));
  expect(
    screen.queryByRole("heading", { name: "Citation [1]" }),
  ).not.toBeInTheDocument();
});

it("shows a verification failure alert when the citation fault is injected", async () => {
  setPrototypeFaults(["citation-verification-failed"]);
  render(<TapProductPrototype />);
  fireEvent.click(
    screen.getByRole("checkbox", {
      name: /Life underwriting guide · v1\.2\.md/,
    }),
  );
  askHealthDisclosureQuestion();
  await waitFor(
    () =>
      expect(
        screen.getByRole("button", {
          name: "[1] Life underwriting guide · v1.2.md",
        }),
      ).toBeVisible(),
    { timeout: 2_000 },
  );
  fireEvent.click(
    screen.getByRole("button", {
      name: "[1] Life underwriting guide · v1.2.md",
    }),
  );
  expect(screen.getByRole("alert")).toHaveTextContent(
    "The citation could not be verified.",
  );
});

it("re-seeds the citation panel when a different citation is opened without closing", async () => {
  setPrototypeFaults(["citation-verification-failed"]);
  render(<TapProductPrototype />);
  selectUnderwritingSources();
  askHealthDisclosureQuestion();
  await waitFor(
    () =>
      expect(
        screen.getByRole("button", {
          name: "[1] Life underwriting guide · v1.2.md",
        }),
      ).toBeVisible(),
    { timeout: 2_000 },
  );
  fireEvent.click(
    screen.getByRole("button", {
      name: "[1] Life underwriting guide · v1.2.md",
    }),
  );
  expect(screen.getByRole("alert")).toHaveTextContent(
    "The citation could not be verified.",
  );
  fireEvent.click(
    screen.getByRole("button", { name: "[2] Underwriting test rules.pdf" }),
  );
  expect(screen.getByRole("heading", { name: "Citation [2]" })).toBeVisible();
  expect(
    screen.queryByText("The citation could not be verified."),
  ).not.toBeInTheDocument();
});

it("opens the original review document from a knowledge-base citation", async () => {
  render(<TapProductPrototype />);
  selectUnderwritingSources();
  askHealthDisclosureQuestion();
  await waitFor(
    () =>
      expect(
        screen.getByRole("button", {
          name: "[1] Life underwriting guide · v1.2.md",
        }),
      ).toBeVisible(),
    { timeout: 2_000 },
  );
  fireEvent.click(
    screen.getByRole("button", {
      name: "[1] Life underwriting guide · v1.2.md",
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Open original" }));
  const dialog = screen.getByRole("dialog");
  expect(
    within(dialog).getByRole("heading", {
      name: "Life underwriting guide · v1.2.md",
    }),
  ).toBeVisible();
  expect(
    screen.getByRole("heading", { name: "Library", hidden: true }),
  ).toBeInTheDocument();
});

it("opens the Library without a review dialog from a sample-file citation", async () => {
  render(<TapProductPrototype />);
  selectUnderwritingSources();
  askHealthDisclosureQuestion();
  await waitFor(
    () =>
      expect(
        screen.getByRole("button", {
          name: "[2] Underwriting test rules.pdf",
        }),
      ).toBeVisible(),
    { timeout: 2_000 },
  );
  fireEvent.click(
    screen.getByRole("button", { name: "[2] Underwriting test rules.pdf" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Open original" }));
  expect(
    screen.getByRole("heading", { name: "Library" }),
  ).toBeVisible();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("expands the collapsed sources panel when a citation is opened", async () => {
  render(<TapProductPrototype />);
  selectUnderwritingSources();
  askHealthDisclosureQuestion();
  await waitFor(
    () =>
      expect(
        screen.getByRole("button", {
          name: "[1] Life underwriting guide · v1.2.md",
        }),
      ).toBeVisible(),
    { timeout: 2_000 },
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Collapse Knowledge sources" }),
  );
  expect(
    screen.queryByRole("heading", { name: "Knowledge sources" }),
  ).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", {
      name: "[1] Life underwriting guide · v1.2.md",
    }),
  );
  expect(screen.getByRole("heading", { name: "Citation [1]" })).toBeVisible();
});
