import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { KnowledgeClientError } from "../api/client";
import type { GraphProjectView } from "../api/types";
import { fakeKnowledgeClient } from "../testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../testing/renderKnowledgeApp";
import { GraphFragmentStatus } from "./GraphFragmentStatus";

function graphProject(
  overrides: Partial<GraphProjectView> = {},
): GraphProjectView {
  return {
    graphVersion: 1,
    status: "READY",
    nodeCount: 10,
    edgeCount: 5,
    mergedAt: "2026-08-28T07:30:00Z",
    communities: [],
    extractingRevisionIds: [],
    partialRevisionIds: [],
    ...overrides,
  };
}

describe("GraphFragmentStatus", () => {
  it("shows ready when the revision is not listed as a fragment", async () => {
    const api = fakeKnowledgeClient().withGraphProject(graphProject());
    renderKnowledgeApp(<GraphFragmentStatus revisionId="rev_ready" />, {
      api,
    });

    expect(await screen.findByText("就绪")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "重试失败批次" }),
    ).not.toBeInTheDocument();
  });

  it("shows extracting without a retry button", async () => {
    const api = fakeKnowledgeClient().withGraphProject(
      graphProject({ extractingRevisionIds: ["rev_x"] }),
    );
    renderKnowledgeApp(<GraphFragmentStatus revisionId="rev_x" />, { api });

    expect(await screen.findByText("抽取中")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "重试失败批次" }),
    ).not.toBeInTheDocument();
  });

  it("shows partial failure with a retry button and retries via the fragment route", async () => {
    const api = fakeKnowledgeClient().withGraphProject(
      graphProject({ partialRevisionIds: ["rev_x"] }),
    );
    renderKnowledgeApp(<GraphFragmentStatus revisionId="rev_x" />, { api });

    expect(await screen.findByText("部分失败")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "重试失败批次" }));

    expect(api.graphRetryCalls).toHaveLength(1);
    expect(api.graphRetryCalls[0]?.revisionId).toBe("rev_x");
    expect(await screen.findByText("已重新排队")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "重试失败批次" }),
    ).not.toBeInTheDocument();
  });

  it("shows a localized error (not a version notice) when a retry is rejected as busy", async () => {
    const api = fakeKnowledgeClient()
      .withGraphProject(graphProject({ partialRevisionIds: ["rev_x"] }))
      .withGraphRetryProblem(
        new KnowledgeClientError({
          type: "https://tap.example/problems/graph-job-busy",
          title: "Graph job busy",
          status: 409,
          detail:
            "The graph extraction job is currently running under an active lease.",
          correlationId: "request-test",
          retryable: false,
          failureStage: "graph",
        }),
      );
    renderKnowledgeApp(<GraphFragmentStatus revisionId="rev_x" />, { api });

    await userEvent.click(
      await screen.findByRole("button", { name: "重试失败批次" }),
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("重试未完成，请稍后重试。");
    expect(alert.textContent).not.toMatch(/graph-job-busy|409/u);
    expect(screen.getByRole("button", { name: "重试失败批次" })).toBeVisible();
  });
});
