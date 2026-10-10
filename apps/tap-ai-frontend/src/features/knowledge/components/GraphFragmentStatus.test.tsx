import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { KnowledgeClientError } from "../api/client";
import type { GraphProjectView } from "../api/types";
import { fakeKnowledgeClient } from "../testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../testing/renderKnowledgeApp";
import { DocumentDetail } from "./DocumentDetail";
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
  it("shows an idle, non-asserting state when the revision is not listed as a fragment", async () => {
    const api = fakeKnowledgeClient().withGraphProject(graphProject());
    renderKnowledgeApp(<GraphFragmentStatus revisionId="rev_idle" />, {
      api,
    });

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("无进行中的抽取");
    expect(
      screen.queryByRole("button", { name: "重试失败批次" }),
    ).not.toBeInTheDocument();
  });

  it("shows extracting without a retry button", async () => {
    const api = fakeKnowledgeClient().withGraphProject(
      graphProject({ extractingRevisionIds: ["rev_x"] }),
    );
    renderKnowledgeApp(<GraphFragmentStatus revisionId="rev_x" />, { api });

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("抽取中");
    expect(
      screen.queryByRole("button", { name: "重试失败批次" }),
    ).not.toBeInTheDocument();
  });

  it("shows partial failure with a retry button and retries via the fragment route", async () => {
    const api = fakeKnowledgeClient().withGraphProject(
      graphProject({ partialRevisionIds: ["rev_x"] }),
    );
    renderKnowledgeApp(<GraphFragmentStatus revisionId="rev_x" />, { api });

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("部分失败");
    await userEvent.click(screen.getByRole("button", { name: "重试失败批次" }));

    expect(api.graphRetryCalls).toHaveLength(1);
    expect(api.graphRetryCalls[0]?.revisionId).toBe("rev_x");
    await waitFor(() => expect(status).toHaveTextContent("已重新排队"));
    expect(
      screen.queryByRole("button", { name: "重试失败批次" }),
    ).not.toBeInTheDocument();
    // The retry button it was on is removed from the DOM, so focus must be
    // moved deliberately to the (now updated) status region rather than
    // falling back to `<body>`.
    expect(status).toHaveFocus();
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

  it("resets the queued confirmation when DocumentDetail keys the component to a new revision", async () => {
    const api = fakeKnowledgeClient().withGraphProject(
      graphProject({ partialRevisionIds: ["rev_a", "rev_b"] }),
    );
    const { rerender } = renderKnowledgeApp(
      <GraphFragmentStatus key="rev_a" revisionId="rev_a" />,
      { api },
    );
    await userEvent.click(
      await screen.findByRole("button", { name: "重试失败批次" }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("已重新排队");

    // Mirrors `DocumentDetail`'s `<GraphFragmentStatus key={revisionId} …>`:
    // switching to a different document revision remounts the component,
    // so the previous revision's queued confirmation must not leak into
    // the new one.
    rerender(<GraphFragmentStatus key="rev_b" revisionId="rev_b" />);

    expect(
      await screen.findByRole("button", { name: "重试失败批次" }),
    ).toBeVisible();
    expect(screen.queryByText("已重新排队")).not.toBeInTheDocument();
  });

  it("follows the English locale when DocumentDetail renders it", async () => {
    const api = fakeKnowledgeClient().withGraphProject(
      graphProject({ partialRevisionIds: ["rev_01JABCDEF"] }),
    );
    renderKnowledgeApp(
      <DocumentDetail
        documentId="doc_01JABCDEF"
        filename="policy.md"
        locale="en"
        onClose={() => undefined}
        onAfterClose={() => undefined}
      />,
      { api },
    );

    expect(
      await screen.findByRole("heading", { name: "Graph extraction" }),
    ).toBeVisible();
    expect(await screen.findByText("Partially failed")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Retry failed batches" }),
    ).toBeVisible();
  });
});
