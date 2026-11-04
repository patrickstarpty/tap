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
  window.history.replaceState(null, "", "/");
});

const beneficiarySource: LibrarySource = {
  id: "sample-beneficiary",
  name: "Beneficiary change workflow.docx",
  origin: "page-local",
  type: "DOCX",
  status: "ready",
  description: "Approval steps, allocation rules and audit evidence.",
  hasPublishedGraph: true,
};

const underwritingSource: LibrarySource = {
  id: "sample-underwriting",
  name: "Underwriting test rules.pdf",
  origin: "page-local",
  type: "PDF",
  status: "ready",
  description: "Decision boundaries, review triggers and expected outcomes.",
  hasPublishedGraph: true,
};

const exploratorySource: LibrarySource = {
  id: "sample-exploratory",
  name: "Exploratory testing checklist.md",
  origin: "page-local",
  type: "MD",
  status: "ready",
  description: "Permissions, boundary values and recovery scenarios.",
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

it("switches to a published source graph", () => {
  renderWorkspace([
    beneficiarySource,
    underwritingSource,
    exploratorySource,
  ]);

  fireEvent.click(screen.getByText("Published source graph"));

  const select = screen.getByRole("combobox", {
    name: "Source",
  }) as HTMLSelectElement;
  expect(select.value).toBe("sample-beneficiary");

  fireEvent.change(select, { target: { value: "sample-underwriting" } });

  expect(select.value).toBe("sample-underwriting");
  expect(
    screen.getByLabelText("Life insurance knowledge graph"),
  ).toBeVisible();
  expect(
    screen.queryByText("This source has no published graph yet."),
  ).not.toBeInTheDocument();
});

it("shows an empty state for a source without a published graph", () => {
  renderWorkspace([underwritingSource, exploratorySource]);

  fireEvent.click(screen.getByText("Published source graph"));

  const select = screen.getByRole("combobox", { name: "Source" });
  fireEvent.change(select, { target: { value: "sample-exploratory" } });

  expect(
    screen.getByText("This source has no published graph yet."),
  ).toBeVisible();
  expect(
    screen.queryByLabelText("Life insurance knowledge graph"),
  ).not.toBeInTheDocument();
});

it("keeps showing a selected published graph when a filter excludes it from the facet list", () => {
  renderWorkspace([
    beneficiarySource,
    underwritingSource,
    exploratorySource,
  ]);

  fireEvent.click(screen.getByText("Published source graph"));
  const select = screen.getByRole("combobox", {
    name: "Source",
  }) as HTMLSelectElement;
  expect(select.value).toBe("sample-beneficiary");

  fireEvent.change(screen.getByRole("combobox", { name: "Type" }), {
    target: { value: "PDF" },
  });

  expect(
    screen.queryByText("This source has no published graph yet."),
  ).not.toBeInTheDocument();
  expect(
    screen.getByLabelText("Life insurance knowledge graph"),
  ).toBeVisible();
});

it("retries a failed graph load", () => {
  setPrototypeFaults(["graph-load-failed"]);
  renderWorkspace([underwritingSource]);

  expect(
    screen.getByText("The knowledge graph could not be loaded."),
  ).toBeVisible();
  expect(screen.getByRole("alert")).toBeVisible();

  fireEvent.click(screen.getByRole("button", { name: "Retry" }));

  expect(
    screen.queryByText("The knowledge graph could not be loaded."),
  ).not.toBeInTheDocument();
  expect(screen.getByText("Domain overview")).toBeVisible();
  expect(
    screen.getByLabelText("Life insurance knowledge graph"),
  ).toBeVisible();
});

it("opens the immutable cited path when the source has no current published graph", () => {
  window.history.replaceState(
    null,
    "",
    "/prototype?module=library&snapshot=prototype-health-disclosure&highlight=application-health&graphSource=sample-underwriting",
  );
  const { container } = render(
    <LibraryWorkspace
      copy={copy}
      sources={[{ ...underwritingSource, hasPublishedGraph: false }]}
      onAddSource={vi.fn()}
      onRetrySource={vi.fn()}
      onDeleteSource={vi.fn()}
    />,
  );
  expect(screen.getByLabelText("Life insurance knowledge graph")).toBeVisible();
  expect(
    container.querySelectorAll('.tap-graph-edge[data-highlighted="true"]'),
  ).toHaveLength(1);
  expect(
    container.querySelectorAll('.tap-graph-node[data-highlighted="true"]'),
  ).toHaveLength(2);
  window.history.replaceState(null, "", "/");
});
