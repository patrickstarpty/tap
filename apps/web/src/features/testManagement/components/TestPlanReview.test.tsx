import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { usePublishTestPlan, useTestPlan } from "../api/queries";
import { TestPlanReview } from "./TestPlanReview";

vi.mock("../api/queries", () => ({
  usePublishTestPlan: vi.fn(),
  useTestPlan: vi.fn(),
}));

describe("TestPlanReview", () => {
  it("renders BDD evidence and publishes with the current row version", () => {
    const mutate = vi.fn();
    vi.mocked(usePublishTestPlan).mockReturnValue({
      mutate,
      isPending: false,
      isError: false,
    } as never);
    vi.mocked(useTestPlan).mockReturnValue({
      isPending: false,
      isError: false,
      data: {
        testPlanId: "tp_checkout",
        revisionId: "tpr_checkout",
        version: 1,
        rowVersion: 3,
        title: "Checkout",
        objective: "Verify checkout",
        scopeItems: [],
        prerequisites: [],
        risks: [],
        status: "DRAFT",
        origin: "VALIDATION",
        adoptedFromRevisionId: null,
        contentDigest: `sha256:${"a".repeat(64)}`,
        validationDigest: null,
        deepLink: "/test-management/tp_checkout/revisions/tpr_checkout",
        cases: [
          {
            caseId: "case_checkout",
            ordinal: 1,
            title: "Pay",
            objective: "Complete payment",
            critical: true,
            scenarios: [
              {
                scenarioId: "scenario_checkout",
                ordinal: 1,
                title: "Accepted card",
                steps: [
                  {
                    stepId: "step_given",
                    ordinal: 1,
                    keyword: "Given",
                    text: "a cart",
                    expectedResult: null,
                    critical: false,
                  },
                  {
                    stepId: "step_when",
                    ordinal: 2,
                    keyword: "When",
                    text: "payment is submitted",
                    expectedResult: null,
                    critical: false,
                  },
                  {
                    stepId: "step_then",
                    ordinal: 3,
                    keyword: "Then",
                    text: "the order is placed",
                    expectedResult: "order confirmed",
                    critical: true,
                  },
                ],
              },
            ],
          },
        ],
        citations: [
          {
            citationId: "citation_checkout",
            sourceRevisionId: "source_checkout",
            documentRevisionId: "document_checkout",
            chunkId: "chunk_checkout",
            contentDigest: `sha256:${"b".repeat(64)}`,
            claimText: "Cards are accepted",
            origin: "SOURCE",
          },
        ],
        assumptions: [],
        unknowns: [],
        coverageGaps: [],
      },
    } as never);
    render(
      <TestPlanReview
        projectId="tapper-demo"
        planId="tp_checkout"
        revisionId="tpr_checkout"
        onBack={vi.fn()}
      />,
    );
    expect(screen.getByText("Given")).toBeVisible();
    expect(screen.getByText(/1 条来源依据/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "批准并发布" }));
    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        plan: expect.objectContaining({ rowVersion: 3 }),
      }),
    );
  });
});
