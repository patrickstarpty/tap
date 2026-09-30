import {
  act,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { LibraryWorkspace } from "./LibraryWorkspace";
import { PROTOTYPE_COPY } from "./copy";
import type { LibrarySource } from "./model";
import { setPrototypeFaults } from "./prototypeFaults";
import type { ChunkSettings } from "./ChunkManager";

const copy = PROTOTYPE_COPY.en;

beforeEach(() => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  vi.useFakeTimers();
});

afterEach(() => {
  setPrototypeFaults([]);
  vi.useRealTimers();
});

const readySource: LibrarySource = {
  id: "ready-1",
  name: "Underwriting rules.pdf",
  origin: "knowledge-base",
  type: "PDF",
  status: "ready",
  description: "Underwriting rules",
};

function renderWorkspace(
  onAddSource: (
    source: Pick<LibrarySource, "name" | "type">,
    chunkSettings: ChunkSettings,
  ) => void,
) {
  return render(
    <LibraryWorkspace
      copy={copy}
      sources={[readySource]}
      onAddSource={onAddSource}
      onRetrySource={() => {}}
      onDeleteSource={() => {}}
    />,
  );
}

function openDialogWithFile() {
  fireEvent.click(screen.getAllByRole("button", { name: "Add source" })[0]!);
  const file = new File(["content"], "policy-guide.pdf", {
    type: "application/pdf",
  });
  fireEvent.change(screen.getByLabelText("Source file"), {
    target: { files: [file] },
  });
}

function dialog() {
  return within(screen.getByRole("dialog"));
}

it("accepts the unified source formats", () => {
  renderWorkspace(() => {});
  fireEvent.click(screen.getAllByRole("button", { name: "Add source" })[0]!);
  expect(screen.getByLabelText("Source file")).toHaveAttribute(
    "accept",
    ".pdf,.docx,.md,.markdown,.txt,.xlsx,.png,.jpg,.jpeg",
  );
  expect(
    screen.getByText("PDF, DOCX, Markdown, TXT, XLSX, PNG or JPG"),
  ).toBeVisible();
});

it("previews the first chunks for the chosen settings", () => {
  renderWorkspace(() => {});
  openDialogWithFile();
  fireEvent.click(screen.getByRole("button", { name: "Next" }));

  fireEvent.click(dialog().getByRole("button", { name: "Preview chunks" }));
  expect(dialog().getAllByRole("listitem")).toHaveLength(3);

  fireEvent.click(dialog().getByRole("radio", { name: "Parent-child" }));
  fireEvent.click(dialog().getByRole("button", { name: "Preview chunks" }));
  expect(dialog().getAllByText(/child chunks/).length).toBeGreaterThan(0);
});

it("disables Add source for invalid chunk settings", () => {
  const onAddSource = vi.fn();
  renderWorkspace(onAddSource);
  openDialogWithFile();
  fireEvent.click(screen.getByRole("button", { name: "Next" }));

  fireEvent.change(
    screen.getByRole("spinbutton", { name: "Maximum length" }),
    { target: { value: "0" } },
  );

  expect(dialog().getByRole("button", { name: "Add source" })).toBeDisabled();
});

it("uploads with the chosen chunk settings", () => {
  const onAddSource = vi.fn();
  renderWorkspace(onAddSource);
  openDialogWithFile();
  fireEvent.click(screen.getByRole("button", { name: "Next" }));

  fireEvent.change(
    screen.getByRole("spinbutton", { name: "Maximum length" }),
    { target: { value: "300" } },
  );

  fireEvent.click(dialog().getByRole("button", { name: "Add source" }));

  expect(
    dialog().getByRole("button", { name: "Add source" }),
  ).toHaveAttribute("aria-busy", "true");
  expect(dialog().getByRole("button", { name: "Cancel" })).toBeDisabled();
  expect(dialog().getByRole("button", { name: "Back" })).toBeDisabled();

  act(() => {
    vi.advanceTimersByTime(1000);
  });

  expect(onAddSource).toHaveBeenCalledWith(
    { name: "policy-guide.pdf", type: "PDF" },
    expect.objectContaining({ max: 300 }),
  );
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("keeps the dialog open when upload fails", () => {
  setPrototypeFaults(["upload-failed"]);
  const onAddSource = vi.fn();
  renderWorkspace(onAddSource);
  openDialogWithFile();
  fireEvent.click(screen.getByRole("button", { name: "Next" }));

  fireEvent.click(dialog().getByRole("button", { name: "Add source" }));
  act(() => {
    vi.advanceTimersByTime(1000);
  });

  expect(screen.getByRole("alert")).toHaveTextContent(
    "Upload failed. Please try again.",
  );
  expect(onAddSource).not.toHaveBeenCalled();
  expect(dialog().getByRole("button", { name: "Cancel" })).not.toBeDisabled();

  fireEvent.click(dialog().getByRole("button", { name: "Add source" }));
  act(() => {
    vi.advanceTimersByTime(1000);
  });

  expect(onAddSource).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
