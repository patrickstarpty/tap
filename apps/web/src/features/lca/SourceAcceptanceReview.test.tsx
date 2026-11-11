import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { SourceAcceptanceReview } from "./SourceAcceptanceReview";

it("shows exact original reviewed inputs and readback without approving edited flows", () => {
  render(
    <SourceAcceptanceReview
      status="reviewed"
      contract={{
        version: 1,
        reviewed: true,
        fields: [
          {
            id: "Age-Years",
            value: "30",
            readback_value: "Thirty",
            attribute: "text",
            target_texts: ["Age"],
            scope_text: "Applicant",
          },
        ],
      }}
    />,
  );
  expect(
    screen.getByText(/supplied value 30; required readback "Thirty"/),
  ).toBeVisible();
  expect(
    screen.getByText(/not approval of this edited LCA flow/),
  ).toBeVisible();
  expect(screen.queryByRole("button")).toBeNull();
});
it("labels legacy and invalid sources without inventing approval", () => {
  const view = render(
    <SourceAcceptanceReview status="legacy_unreviewed" contract={null} />,
  );
  expect(screen.getByText(/no reviewed structured requirements/)).toBeVisible();
  view.rerender(<SourceAcceptanceReview status="invalid" contract={null} />);
  expect(screen.getByText(/Executable import is blocked/)).toBeVisible();
});
