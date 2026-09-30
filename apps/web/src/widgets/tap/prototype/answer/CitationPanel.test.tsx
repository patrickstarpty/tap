import { fireEvent, render, screen } from "@testing-library/react";
import { it, expect, vi } from "vitest";
import { CitationPanel } from "./CitationPanel";
import type { OpenCitation } from "./CitationPanel";

function citation(overrides: Partial<OpenCitation> = {}): OpenCitation {
  return {
    turnId: "turn-1",
    index: 1,
    source: {
      id: "underwriting-v12",
      name: "Life underwriting guide · v1.2.md",
      origin: "knowledge-base",
    },
    ...overrides,
  };
}

it("shows the cited passage with its location", () => {
  render(
    <CitationPanel
      citation={citation()}
      locale="en"
      onClose={vi.fn()}
      onOpenOriginal={vi.fn()}
    />,
  );
  expect(
    screen.getByRole("heading", { name: "Citation [1]" }),
  ).toBeVisible();
  expect(screen.getByText("Version v1.2 · Section 4")).toBeVisible();
  expect(document.querySelector("mark")).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Open original" }),
  ).toBeVisible();
});

it("warns when the cited source has a newer revision", () => {
  render(
    <CitationPanel
      citation={citation({
        source: {
          id: "underwriting-v12",
          name: "Life underwriting guide · v1.2.md",
          origin: "knowledge-base",
          hasNewerRevision: true,
        },
      })}
      locale="en"
      onClose={vi.fn()}
      onOpenOriginal={vi.fn()}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent(
    "This source has been updated",
  );
  expect(
    screen.getByRole("button", { name: "View latest version" }),
  ).toBeVisible();
});

it("retries a failed verification", () => {
  render(
    <CitationPanel
      citation={citation({ verificationFailed: true })}
      locale="en"
      onClose={vi.fn()}
      onOpenOriginal={vi.fn()}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent(
    "The citation could not be verified.",
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Retry verification" }),
  );
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByText(/HEALTH_DISCLOSURE_REQUIRED/)).toBeVisible();
});

it("shows the cited document's name in the header", () => {
  render(
    <CitationPanel
      citation={citation()}
      locale="en"
      onClose={vi.fn()}
      onOpenOriginal={vi.fn()}
    />,
  );
  expect(
    screen.getByText("Life underwriting guide · v1.2.md"),
  ).toBeVisible();
});

it("shows the beneficiary workflow passage for the workflow source", () => {
  render(
    <CitationPanel
      citation={citation({
        source: {
          id: "sample-beneficiary",
          name: "Beneficiary change workflow.docx",
          origin: "knowledge-base",
        },
      })}
      locale="en"
      onClose={vi.fn()}
      onOpenOriginal={vi.fn()}
    />,
  );
  expect(screen.getByText(/100%/)).toBeVisible();
  expect(screen.getByText(/immediately/)).toBeVisible();
});

it("shows a contradictory passage for the beneficiary test cases source", () => {
  render(
    <CitationPanel
      citation={citation({
        source: {
          id: "sample-test-cases",
          name: "Beneficiary test cases.xlsx",
          origin: "knowledge-base",
        },
      })}
      locale="en"
      onClose={vi.fn()}
      onOpenOriginal={vi.fn()}
    />,
  );
  expect(screen.getByText(/insurer confirms/)).toBeVisible();
});

it("shows a neutral passage built from the source description for other sources", () => {
  render(
    <CitationPanel
      citation={citation({
        source: {
          id: "sample-exploratory",
          name: "Exploratory testing checklist.md",
          origin: "knowledge-base",
          description:
            "Permissions, boundary values and recovery scenarios.",
        },
      })}
      locale="en"
      onClose={vi.fn()}
      onOpenOriginal={vi.fn()}
    />,
  );
  expect(
    screen.getByText(/Permissions, boundary values and recovery scenarios\./),
  ).toBeVisible();
});

it("hides Open original and shows an unavailable message when the source was removed", () => {
  render(
    <CitationPanel
      citation={citation({ sourceRemoved: true })}
      locale="en"
      onClose={vi.fn()}
      onOpenOriginal={vi.fn()}
    />,
  );
  expect(
    screen.getByText("This source is no longer available."),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Open original" }),
  ).not.toBeInTheDocument();
});
