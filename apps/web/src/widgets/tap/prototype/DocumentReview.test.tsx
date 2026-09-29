import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, expect, it } from "vitest";
import { DocumentReview, useDocumentReview } from "./DocumentReview";
function Harness() {
  const review = useDocumentReview("en");
  return (
    <>
      <button onClick={() => review.inspect("underwriting-v12")}>Open</button>
      <button onClick={() => review.inspect("approval-flow-complex")}>
        Open flowchart
      </button>
      <DocumentReview review={review} onUse={() => {}} />
    </>
  );
}
beforeEach(() => localStorage.clear());
it("navigates sections and filters the outline without losing the source version", () => {
  render(<Harness />);
  fireEvent.click(screen.getByText("Open"));
  fireEvent.click(screen.getByRole("button", { name: "Original document" }));
  fireEvent.click(screen.getByRole("button", { name: "Next section" }));
  expect(
    screen.getByRole("heading", { name: "5. Submission and correction" }),
  ).toBeVisible();
  fireEvent.change(screen.getByRole("searchbox", { name: "Find a section" }), {
    target: { value: "health" },
  });
  expect(
    within(
      screen.getByRole("navigation", { name: "Document outline" }),
    ).getAllByRole("button"),
  ).toHaveLength(1);
  expect(screen.getByText(/v1.2 · Knowledge library/)).toBeVisible();
});
it("reviews a multi-branch flowchart in the shared workbench", () => {
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "Open flowchart" }));
  fireEvent.click(screen.getByRole("button", { name: "Original document" }));
  expect(
    screen.getByRole("heading", { name: "Underwriting approval flow.png" }),
  ).toBeVisible();
  expect(
    screen.getByRole("img", {
      name: "Original underwriting approval flowchart",
    }),
  ).toBeVisible();
  expect(screen.getByText(/High risk requires senior review/)).toBeVisible();
  fireEvent.click(
    screen.getByRole("button", { name: /Intake and completion/ }),
  );
  expect(
    screen.getByText(/Missing disclosure returns to applicant/),
  ).toBeVisible();
  expect(
    screen.getByRole("region", { name: "Section comments" }),
  ).toBeVisible();
});
it("saves a selected passage with a comment, restores it and resolves it from problem navigation", () => {
  const view = render(<Harness />);
  fireEvent.click(screen.getByText("Open"));
  fireEvent.click(screen.getByRole("button", { name: "Original document" }));
  const text = screen.getByText(
    "Keep entered information and allow the applicant to complete missing fields before resubmitting.",
  );
  const range = document.createRange();
  range.selectNodeContents(text);
  window.getSelection()!.removeAllRanges();
  window.getSelection()!.addRange(range);
  fireEvent.mouseUp(text);
  fireEvent.click(screen.getByRole("button", { name: "Mark selected text" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Review comment" }), {
    target: { value: "Confirm the error code with the policy owner." },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save comment" }));
  expect(
    screen
      .getByRole("article", { name: "Source passage" })
      .querySelector("mark[data-review-mark]"),
  ).toHaveTextContent("Keep entered information");
  fireEvent.click(screen.getByRole("button", { name: "Next section" }));
  expect(screen.getByRole("textbox", { name: "Review comment" })).toHaveValue(
    "",
  );
  view.unmount();
  render(<Harness />);
  fireEvent.click(screen.getByText("Open"));
  fireEvent.click(screen.getByRole("button", { name: "Original document" }));
  fireEvent.click(screen.getByRole("button", { name: "Open comments (1)" }));
  fireEvent.click(
    screen.getByRole("button", { name: /Confirm the error code/ }),
  );
  expect(
    screen.getByRole("heading", { name: "4. Health disclosure" }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Resolve comment" }));
  expect(screen.getByText("Resolved")).toBeVisible();
});

it("rejects damaged persisted comments referencing a missing section", () => {
  localStorage.setItem(
    "tap.prototype.document-reviews.v3",
    JSON.stringify([
      {
        id: "underwriting-v12",
        name: "Life underwriting guide · v1.2.md",
        version: "v1.2",
        state: "review",
        checks: [false, false, false, false],
        revision: 1,
        history: [],
        comments: [
          {
            id: "bad",
            section: 999,
            quote: "bad",
            text: "Unknown section",
            resolved: false,
          },
        ],
      },
    ]),
  );
  render(<Harness />);
  fireEvent.click(screen.getByText("Open"));
  fireEvent.click(screen.getByRole("button", { name: "Original document" }));
  expect(
    screen.getByRole("button", { name: "Open comments (0)" }),
  ).toBeVisible();
  expect(
    screen.getByRole("heading", { name: "4. Health disclosure" }),
  ).toBeVisible();
});
