import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  useForkTestPlan,
  usePublishTestPlan,
  useReviewTestPlan,
  useTestPlan,
  useUpdateTestPlan,
} from "../api/queries";
import { TestPlanReview } from "./TestPlanReview";

vi.mock("../api/queries", () => ({
  usePublishTestPlan: vi.fn(),
  useReviewTestPlan: vi.fn(),
  useUpdateTestPlan: vi.fn(),
  useForkTestPlan: vi.fn(),
  useTestPlan: vi.fn(),
}));

describe("TestPlanReview", () => {
  it("renders BDD evidence and publishes with the current row version", () => {
    const mutate = vi.fn();
    const updateDraft = vi.fn().mockResolvedValue(undefined);
    const reviewDraft = vi.fn().mockResolvedValue(undefined);
    vi.mocked(usePublishTestPlan).mockReturnValue({
      mutate,
      isPending: false,
      isError: false,
    } as never);
    vi.mocked(useReviewTestPlan).mockReturnValue({
      mutateAsync: reviewDraft,
      isPending: false,
      isError: false,
    } as never);
    vi.mocked(useUpdateTestPlan).mockReturnValue({
      mutateAsync: updateDraft,
      isPending: false,
      isError: false,
      error: null,
    } as never);
    vi.mocked(useForkTestPlan).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
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
        assumptions: [
          {
            factId: "assumption_checkout",
            graphEdgeId: "edge_payment",
            text: "The payment provider is available",
          },
        ],
        unknowns: [
          {
            factId: "unknown_checkout",
            graphEdgeId: null,
            text: "Whether retry is supported",
          },
        ],
        coverageGaps: [
          {
            gapId: "gap_checkout",
            requirementRef: "REQ-PAY-02",
            reason: "The refund policy is missing",
            severity: "HIGH",
          },
        ],
        requirementScopeId: "checkout_scope_v1",
        requirementScopeVersion: 1,
        requirementScopeDigest: `sha256:${"c".repeat(64)}`,
        requirementIds: ["requirement_checkout"],
        coveredRequirementIds: ["requirement_checkout"],
        coverageDenominator: 1,
        coveredRequirementCount: 1,
        approvedKnowledgeRevisionIds: ["source_checkout"],
        modelRevisionId: "tapper-chat-2026-09",
        agentRevisionId: "agent_checkout",
        skillRevisionIds: ["skill_checkout"],
        authorActorId: "draft_author",
        strictReviewRequired: false,
        generatedContentDigest: `sha256:${"a".repeat(64)}`,
        reviewDecisions: [
          {
            decisionId: "review_checkout",
            disposition: "ACCEPTED_UNCHANGED",
            reason: "Verified against the approved policy.",
            actorId: "business_reviewer",
            reviewedContentDigest: `sha256:${"a".repeat(64)}`,
            createdAt: "2026-09-26T00:00:00Z",
          },
        ],
        needsReview: false,
        needsReviewReason: null,
      },
    } as never);
    render(
      <TestPlanReview
        projectId="tapper-demo"
        planId="tp_checkout"
        revisionId="tpr_checkout"
        locale="zh"
        onBack={vi.fn()}
      />,
    );
    expect(screen.getByText("Given")).toBeVisible();
    expect(screen.getByText("来源依据")).toBeVisible();
    expect(screen.getByText(/Cards are accepted/)).toBeVisible();
    fireEvent.click(screen.getByText("查看证据标识"));
    expect(screen.getByText(/source_checkout/)).toBeVisible();
    expect(screen.getByText("The payment provider is available")).toBeVisible();
    expect(screen.getByText("Whether retry is supported")).toBeVisible();
    expect(screen.getByText(/The refund policy is missing/)).toBeVisible();
    fireEvent.change(screen.getByLabelText("计划目标"), {
      target: { value: "Verify checkout and review failures" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    expect(updateDraft).toHaveBeenCalledWith(
      expect.objectContaining({
        plan: expect.objectContaining({
          objective: "Verify checkout and review failures",
        }),
      }),
    );
    fireEvent.change(screen.getByLabelText("评审理由"), {
      target: { value: "已逐项核对需求和证据。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "修改后采纳" }));
    expect(reviewDraft).toHaveBeenCalledWith(
      expect.objectContaining({
        disposition: "ACCEPTED_MODIFIED",
        reason: "已逐项核对需求和证据。",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "批准并发布" }));
    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        plan: expect.objectContaining({ rowVersion: 3 }),
      }),
    );
  });
});
