import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

import { TestPlanApiError, type TestPlanRevision } from "../api/client";
const fixture = {
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
  modelRevisionId: "qwen-plus-2026-09",
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
} as TestPlanRevision;

const updateDraft = vi.fn();
const reviewDraft = vi.fn();
const publishDraft = vi.fn();
const refetch = vi.fn();
let plan: TestPlanRevision;

beforeEach(() => {
  vi.clearAllMocks();
  plan = { ...structuredClone(fixture), unknowns: [] };
  updateDraft.mockImplementation(async ({ plan: submitted }) => ({
    ...submitted,
    rowVersion: 4,
    contentDigest: `sha256:${"d".repeat(64)}`,
    reviewDecisions: [],
  }));
  reviewDraft.mockImplementation(async ({ plan: submitted, disposition }) => ({
    ...submitted,
    rowVersion: 5,
    reviewDecisions: [
      {
        ...fixture.reviewDecisions![0],
        disposition,
        reviewedContentDigest: submitted.contentDigest,
      },
    ],
  }));
  vi.mocked(usePublishTestPlan).mockReturnValue({
    mutate: publishDraft,
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
  } as never);
  vi.mocked(useForkTestPlan).mockReturnValue({
    mutateAsync: vi.fn(),
    isPending: false,
  } as never);
  vi.mocked(useTestPlan).mockImplementation(
    () => ({ isPending: false, isError: false, data: plan, refetch }) as never,
  );
});

function renderReview() {
  render(
    <TestPlanReview
      projectId="tapper-demo"
      planId="tp_checkout"
      revisionId="tpr_checkout"
      locale="zh"
      onBack={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText("评审理由"), {
    target: { value: "已逐项核对需求和证据。" },
  });
}

function expectReviewDisabled() {
  for (const name of ["原样采纳", "修改后采纳", /拒\s*绝/u, "批准并发布"]) {
    expect(screen.getByRole("button", { name })).toBeDisabled();
  }
}

describe("TestPlanReview", () => {
  it("prevents editing or reviewing while a save is pending", () => {
    vi.mocked(useUpdateTestPlan).mockReturnValue({
      mutateAsync: updateDraft,
      isPending: true,
      isError: false,
    } as never);
    renderReview();
    expectReviewDisabled();
    expect(screen.getByLabelText("计划目标")).toBeDisabled();
    expect(screen.getByLabelText("预期结果 step_then")).toBeDisabled();
  });

  it("renders BDD evidence and publishes a saved reviewed revision", async () => {
    renderReview();
    expect(screen.getByText("Given")).toBeVisible();
    expect(screen.getByText(/Cards are accepted/)).toBeVisible();
    fireEvent.click(screen.getByText("查看证据标识"));
    expect(screen.getByText(/source_checkout/)).toBeVisible();
    fireEvent.change(screen.getByLabelText("计划目标"), {
      target: { value: "Verify checkout and review failures" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(screen.getByText("草稿已保存。")).toBeVisible());
    expect(screen.getByRole("button", { name: "批准并发布" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "修改后采纳" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "批准并发布" })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "批准并发布" }));
    expect(publishDraft).toHaveBeenCalledWith(
      expect.objectContaining({
        plan: expect.objectContaining({
          rowVersion: 5,
          objective: "Verify checkout and review failures",
        }),
      }),
    );
  });

  it.each(["计划目标", "预期结果 step_then", "假设 assumption_checkout"])(
    "blocks review and publication with unsaved %s",
    (label) => {
      renderReview();
      expect(screen.getByRole("button", { name: "批准并发布" })).toBeEnabled();
      fireEvent.change(screen.getByLabelText(label), {
        target: { value: "Changed business requirement" },
      });
      expectReviewDisabled();
      fireEvent.click(screen.getByRole("button", { name: "修改后采纳" }));
      expect(reviewDraft).not.toHaveBeenCalled();
      expect(screen.getByLabelText(label)).toHaveValue(
        "Changed business requirement",
      );
    },
  );

  it("blocks review when a resolved unknown has not been saved", () => {
    plan = structuredClone(fixture);
    renderReview();
    fireEvent.click(screen.getByRole("button", { name: "标记已解决" }));
    expectReviewDisabled();
  });

  it("allows review again when the user reverts an unsaved edit", () => {
    renderReview();
    fireEvent.change(screen.getByLabelText("计划目标"), {
      target: { value: "Unsaved" },
    });
    expectReviewDisabled();
    fireEvent.change(screen.getByLabelText("计划目标"), {
      target: { value: "Verify checkout" },
    });
    expect(screen.getByRole("button", { name: "批准并发布" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "修改后采纳" })).toBeEnabled();
  });

  it("keeps unsaved edits locked after a save fails", async () => {
    updateDraft.mockRejectedValueOnce(new Error("offline"));
    renderReview();
    fireEvent.change(screen.getByLabelText("计划目标"), {
      target: { value: "Unsaved" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await screen.findByText("草稿保存失败。");
    expectReviewDisabled();
    expect(screen.getByLabelText("计划目标")).toHaveValue("Unsaved");
  });

  it("keeps preserved edits dirty after reloading a conflicting version", async () => {
    updateDraft.mockRejectedValueOnce(
      new TestPlanApiError("conflict", 409, null),
    );
    refetch.mockResolvedValue({ data: { ...plan, rowVersion: 8 } });
    renderReview();
    fireEvent.change(screen.getByLabelText("计划目标"), {
      target: { value: "Unsaved" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "重新加载版本" }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "重新加载版本" }),
      ).not.toBeInTheDocument(),
    );
    expectReviewDisabled();
    expect(screen.getByLabelText("计划目标")).toHaveValue("Unsaved");
  });
});
