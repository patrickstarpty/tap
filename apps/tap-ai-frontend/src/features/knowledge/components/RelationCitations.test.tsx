import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import {
  answerResponse,
  retrievalCitation,
} from "../testing/fakeKnowledgeClient";
import { GroundedAnswer } from "./GroundedAnswer";

const relation = {
  citationId: "relation-1",
  snapshotId: "snapshot-old",
  edgeId: "edge-a",
  sourceNodeId: "application",
  targetNodeId: "health",
  sourceLabel: "Application",
  targetLabel: "Health disclosure",
  relationType: "REQUIRES",
  origin: "EXTRACTED" as const,
  claimIndex: 0,
  pathEdgeIds: ["edge-a", "edge-b"],
};

it("places a relation chip beside the claim's source citation and opens its immutable path", () => {
  const onOpenRelation = vi.fn();
  const response = {
    ...answerResponse({
      answer: "Disclosure is required.",
      claims: [
        {
          claimId: "claim-1",
          text: "Disclosure is required.",
          answerStart: 0,
          answerEnd: 23,
          citationIds: ["citation-a"],
        },
      ],
      citations: [retrievalCitation("citation-a")],
    }),
    relationCitations: [relation],
  };
  render(
    <GroundedAnswer
      response={response}
      onOpenCitation={vi.fn()}
      onOpenRelation={onOpenRelation}
      locale="en"
    />,
  );
  const chip = screen.getByRole("button", {
    name: "Application —REQUIRES→ Health disclosure",
  });
  expect(
    within(chip.closest(".tapper-grounded-claim")!).getByRole("button", {
      name: "Open source citation 1",
    }),
  ).toBeVisible();
  fireEvent.click(chip);
  expect(onOpenRelation).toHaveBeenCalledWith(relation);
});
