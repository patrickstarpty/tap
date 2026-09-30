import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { LibraryWorkspace } from "./LibraryWorkspace";
import { PROTOTYPE_COPY } from "./copy";
import type { LibrarySource } from "./model";
import { setPrototypeFaults } from "./prototypeFaults";

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
});

afterEach(() => {
  setPrototypeFaults([]);
});

const readySource: LibrarySource = {
  id: "ready-1",
  name: "Underwriting rules.pdf",
  origin: "knowledge-base",
  type: "PDF",
  status: "ready",
  description: "Underwriting rules",
};

function renderWorkspace(sources: readonly LibrarySource[]) {
  return render(
    <LibraryWorkspace
      copy={copy}
      sources={sources}
      onAddSource={() => {}}
      onRetrySource={() => {}}
      onDeleteSource={() => {}}
    />,
  );
}

it("retries loading Library", () => {
  setPrototypeFaults(["library-load-failed"]);
  renderWorkspace([readySource]);

  expect(screen.getByText("Library could not be loaded.")).toBeVisible();
  expect(screen.getByRole("alert")).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Add source" }),
  ).toBeVisible();

  fireEvent.click(
    screen.getAllByRole("button", { name: "Retry" })[0],
  );

  expect(
    screen.queryByText("Library could not be loaded."),
  ).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: "Documents" }));
  expect(screen.getByText("Underwriting rules.pdf")).toBeVisible();
});

it("distinguishes an empty Library from no search results", () => {
  const { rerender } = render(
    <LibraryWorkspace
      copy={copy}
      sources={[]}
      onAddSource={() => {}}
      onRetrySource={() => {}}
      onDeleteSource={() => {}}
    />,
  );

  expect(screen.getByText("No knowledge sources yet")).toBeVisible();
  expect(
    screen.getAllByRole("button", { name: "Add source" }).length,
  ).toBeGreaterThan(0);

  rerender(
    <LibraryWorkspace
      copy={copy}
      sources={[readySource]}
      onAddSource={() => {}}
      onRetrySource={() => {}}
      onDeleteSource={() => {}}
    />,
  );

  fireEvent.click(screen.getByRole("tab", { name: "Documents" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Search library" }), {
    target: { value: "no such source" },
  });

  expect(screen.getByText("No matching sources")).toBeVisible();
  expect(screen.queryByText("No knowledge sources yet")).not.toBeInTheDocument();
});
