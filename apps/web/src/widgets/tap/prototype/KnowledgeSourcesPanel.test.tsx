import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { KnowledgeSourcesPanel } from "./KnowledgeSourcesPanel";
import { PROTOTYPE_COPY } from "./copy";
import type { LibrarySource } from "./model";
import { setPrototypeFaults } from "./prototypeFaults";

const copy = PROTOTYPE_COPY.en;

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

const processingSource: LibrarySource = {
  id: "processing-1",
  name: "New claims policy.pdf",
  origin: "page-local",
  type: "PDF",
  status: "processing",
  description: "Claims policy",
};

function renderPanel(sources: readonly LibrarySource[]) {
  return render(
    <KnowledgeSourcesPanel
      copy={copy}
      isLoading={false}
      onCollapse={() => {}}
      onToggleSource={() => {}}
      selectedSourceIds={[]}
      sources={sources}
    />,
  );
}

it("retries loading knowledge sources", () => {
  setPrototypeFaults(["sources-load-failed"]);
  renderPanel([readySource]);

  expect(
    screen.getByText("Knowledge sources could not be loaded."),
  ).toBeVisible();
  expect(screen.getByRole("alert")).toBeVisible();
  expect(
    screen.queryByText("Underwriting rules.pdf"),
  ).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Retry" }));

  expect(
    screen.queryByText("Knowledge sources could not be loaded."),
  ).not.toBeInTheDocument();
  expect(screen.getByText("Underwriting rules.pdf")).toBeVisible();
});

it("explains when every source is processing", () => {
  renderPanel([processingSource]);

  expect(
    screen.getByText(
      "Sources are processing. They can be selected when ready.",
    ),
  ).toBeVisible();
});

it("still shows no ready sources when there is nothing processing either", () => {
  renderPanel([]);

  expect(screen.getByText("No ready sources")).toBeVisible();
});
