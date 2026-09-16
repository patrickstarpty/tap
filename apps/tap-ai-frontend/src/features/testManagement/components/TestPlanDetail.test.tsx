import { render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";

import { TestPlanDetail } from "./TestPlanDetail";

it("presents a grounded draft as readable cases and evidence", () => {
  render(
    <TestPlanDetail
      locale="en"
      plan={
        {
          title: "claims-control-01-evidence-approval-test-plan",
          objective: "Check evidence before approval.",
          status: "DRAFT",
          scopeItems: ["Evidence check"],
          prerequisites: ["A claim is submitted"],
          risks: ["Unverified approval"],
          cases: [
            {
              caseId: "case_1",
              ordinal: 1,
              title: "evidence-before-approval",
              objective: "Approval requires evidence.",
              critical: true,
              scenarios: [
                {
                  scenarioId: "scenario_1",
                  ordinal: 1,
                  title: "approval-after-check",
                  steps: [
                    {
                      stepId: "step_1",
                      ordinal: 1,
                      keyword: "Given",
                      text: "Evidence is checked",
                      expectedResult: null,
                      critical: false,
                    },
                    {
                      stepId: "step_2",
                      ordinal: 2,
                      keyword: "When",
                      text: "Approval is requested",
                      expectedResult: null,
                      critical: false,
                    },
                    {
                      stepId: "step_3",
                      ordinal: 3,
                      keyword: "Then",
                      text: "Approval is allowed",
                      expectedResult: "Decision is recorded",
                      critical: true,
                    },
                  ],
                },
              ],
            },
          ],
          citations: [
            {
              citationId: "cite_1",
              sourceRevisionId: "rev_secret",
              documentRevisionId: "doc_secret",
              chunkId: "chunk_secret",
              contentDigest: "sha256:secret",
              claimText: "Approval follows a successful evidence check.",
              origin: "GRAPH_EXTRACTED",
            },
          ],
          assumptions: [],
          unknowns: [],
          coverageGaps: [
            {
              gapId: "gap_1",
              requirementRef: "req_1",
              reason: "A boundary is missing",
              severity: "CRITICAL",
            },
          ],
        } as never
      }
    />,
  );

  expect(
    screen.getByRole("heading", {
      name: "claims control 01 evidence approval test plan",
    }),
  ).toBeVisible();
  expect(screen.getByText("Draft")).toBeVisible();
  expect(screen.getByRole("heading", { name: "Test cases" })).toBeVisible();
  expect(
    screen.getByRole("heading", { name: "Source evidence" }),
  ).toBeVisible();
  expect(
    screen.getByText("Expected result: Decision is recorded"),
  ).toBeVisible();
  expect(
    screen.getByText("Approval follows a successful evidence check."),
  ).toBeVisible();
  expect(screen.getByText("Graph extracted")).toBeVisible();
  expect(
    within(screen.getByRole("region", { name: "Coverage gaps" })).getByText(
      "Critical",
    ),
  ).toBeVisible();
  expect(screen.getByText(/rev_secret/)).not.toBeVisible();
});
