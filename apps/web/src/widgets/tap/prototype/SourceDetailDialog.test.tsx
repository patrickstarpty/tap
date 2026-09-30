import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { SourceDetailDialog } from "./SourceDetailDialog";
import { PROTOTYPE_COPY } from "./copy";
import type { LibrarySource } from "./model";

const copy = PROTOTYPE_COPY.en;

const failedReviewSource: LibrarySource = {
  id: "underwriting-scan",
  name: "Underwriting rules — scanned.pdf",
  origin: "knowledge-base",
  type: "PDF",
  status: "failed",
  reviewState: "failed",
  description: "Text extraction failed",
};

const readySampleSource: LibrarySource = {
  id: "sample-underwriting",
  name: "Underwriting test rules.pdf",
  origin: "page-local",
  type: "PDF",
  status: "ready",
  description: "Decision boundaries, review triggers and expected outcomes.",
  isExample: true,
};

it("shows document status and a retry action for a failed review document", () => {
  const onRetry = vi.fn();
  render(
    <SourceDetailDialog
      copy={copy}
      source={failedReviewSource}
      opener={null}
      onClose={() => {}}
      onRetry={onRetry}
      onDelete={() => {}}
    />,
  );
  expect(screen.getByText("Failed")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(onRetry).toHaveBeenCalledWith("underwriting-scan");
});

it("does not show retry for a non-review source even when ready", () => {
  render(
    <SourceDetailDialog
      copy={copy}
      source={readySampleSource}
      opener={null}
      onClose={() => {}}
      onRetry={() => {}}
      onDelete={() => {}}
    />,
  );
  expect(screen.getByText("Ready")).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Retry" }),
  ).not.toBeInTheDocument();
});

it("deletes a source after confirmation", () => {
  const onDelete = vi.fn();
  const onClose = vi.fn();
  render(
    <SourceDetailDialog
      copy={copy}
      source={readySampleSource}
      opener={null}
      onClose={onClose}
      onRetry={() => {}}
      onDelete={onDelete}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Delete source" }));
  expect(screen.getByText(copy.library.deleteSourceConfirm)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(onDelete).toHaveBeenCalledWith("sample-underwriting");
  expect(onClose).toHaveBeenCalled();
});

it("cancels a delete confirmation without calling onDelete", () => {
  const onDelete = vi.fn();
  render(
    <SourceDetailDialog
      copy={copy}
      source={readySampleSource}
      opener={null}
      onClose={() => {}}
      onRetry={() => {}}
      onDelete={onDelete}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Delete source" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(
    screen.queryByText(copy.library.deleteSourceConfirm),
  ).not.toBeInTheDocument();
  expect(onDelete).not.toHaveBeenCalled();
});
