import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";

import { AnswerActivity, AnswerProgress } from "./TapProductPrototype";

it("shows a compact truthful activity summary before expanding the event details", async () => {
  render(
    <AnswerActivity
      locale="en"
      sourceCount={2}
      shownCitationCount={2}
      events={[
        { eventType: "context.assembled", payload: { sourceCount: 2 } },
        {
          eventType: "stage.completed",
          payload: { stage: "knowledge.answer" },
        },
        {
          eventType: "retrieval.hits_ready",
          payload: { authorizedHitCount: 4 },
        },
        { eventType: "citation.resolved", payload: {} },
        { eventType: "citation.resolved", payload: {} },
      ]}
    />,
  );
  const summary = screen.getByText(
    /Activity · 2 sources · Knowledge answer · 2 citations/u,
  );
  expect(summary).toBeVisible();
  expect(
    screen.getByText("Retrieval: 4 authorized evidence hits"),
  ).not.toBeVisible();
  await userEvent.click(summary);
  expect(
    screen.getByText("Retrieval: 4 authorized evidence hits"),
  ).toBeVisible();
});

it("distinguishes resolved citation records from references used in visible claims", async () => {
  render(
    <AnswerActivity
      locale="en"
      sourceCount={1}
      shownCitationCount={4}
      events={[
        { eventType: "answer.delta", payload: {} },
        ...Array.from({ length: 8 }, () => ({
          eventType: "citation.resolved",
          payload: {},
        })),
      ]}
    />,
  );
  const summary = screen.getByText(
    /Activity · 1 source · Answer recorded · 4 citations/u,
  );
  expect(summary).toBeVisible();
  await userEvent.click(summary);
  expect(
    screen.getByText("8 citation records resolved; 4 used by displayed claims"),
  ).toBeVisible();
});

it("describes a running answer using only its selected source snapshot", () => {
  render(<AnswerProgress locale="en" sourceCount={2} />);
  expect(screen.getByRole("status")).toHaveTextContent(
    "Using 2 selected sources · Generating answer…",
  );
});
