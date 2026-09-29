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
