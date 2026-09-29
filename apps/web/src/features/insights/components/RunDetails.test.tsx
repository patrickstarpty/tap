import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import type { InsightsDataAdapter } from "../api/client";
import { RunDetails } from "./RunDetails";

it("hands Tapper only an opaque authorized reference and a number-free draft", () => {
  render(
    <RunDetails
      adapter={{} as InsightsDataAdapter}
      projectId="project-a"
      queryId="query-a"
      tapperBaseUrl="https://tap-ai.example/chat"
      run={{
        runId: "opaque-run-1",
        externalRunId: "RUN-1042",
        sourceId: "ci",
        buildId: "BUILD-1042",
        branch: "main",
        environment: "qa",
        configuration: "browser=chromium",
        startedAt: "2026-09-24T08:00:00Z",
        instanceCount: 1,
        evidenceRefs: ["receipt-authorized"],
      }}
      attempts={[{
        factKey: "fact-1",
        externalRunId: "RUN-1042",
        stableTestId: "case-1",
        sourceTestIdentity: "suite::case-1",
        dataRow: null,
        attempt: 9,
        result: "fail",
        durationSeconds: 987.65,
        evidenceRefs: ["receipt-authorized"],
      }]}
      onClose={vi.fn()}
    />,
  );

  const link = screen.getByRole("link", { name: "Open draft in Tapper" });
  expect(screen.getByText(/not a verified Insights explanation/u)).toBeVisible();
  const url = new URL(link.getAttribute("href")!);
  expect(url.origin + url.pathname).toBe("https://tap-ai.example/chat");
  expect(url.searchParams.get("projectId")).toBe("project-a");
  expect(url.searchParams.get("queryId")).toBe("query-a");
  expect(url.searchParams.getAll("resourceRef")).toEqual(["receipt-authorized"]);
  expect(url.searchParams.get("draft")).toBe("Explain this failed test using authorized Insights and knowledge evidence.");
  expect(url.search).not.toContain("987.65");
  expect(url.search).not.toContain("attempt=9");
  expect(link).toHaveAttribute("rel", "noopener noreferrer");
});

it("marks expired evidence separately from a missing attachment", async () => {
  const adapter = {
    downloadEvidence: vi.fn().mockRejectedValue(Object.assign(new Error("gone"), { status: 410 })),
  } as unknown as InsightsDataAdapter;
  render(
    <RunDetails
      adapter={adapter}
      projectId="project-a"
      queryId="query-a"
      tapperBaseUrl="https://tap-ai.example/chat"
      run={{
        runId: "opaque-run-1",
        externalRunId: "RUN-1042",
        sourceId: "ci",
        buildId: "BUILD-1042",
        branch: "main",
        environment: "qa",
        configuration: "browser=chromium",
        startedAt: "2026-09-24T08:00:00Z",
        instanceCount: 1,
        evidenceRefs: ["receipt-expired"],
      }}
      attempts={[{
        factKey: "fact-1",
        externalRunId: "RUN-1042",
        stableTestId: "case-1",
        sourceTestIdentity: "suite::case-1",
        dataRow: null,
        attempt: 1,
        result: "fail",
        durationSeconds: 2,
        evidenceRefs: ["receipt-expired"],
      }]}
      onClose={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Download raw report" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("This evidence reference has expired or was removed.");
  expect(screen.getByRole("button", { name: "View report evidence" })).toBeVisible();
});

it.each([
  [401, "Sign in to access this evidence."],
  [403, "You no longer have access to this evidence."],
  [503, "Evidence is temporarily unavailable."],
])("distinguishes evidence HTTP %s", async (status, message) => {
  const adapter = {
    downloadEvidence: vi.fn().mockRejectedValue(
      Object.assign(new Error("request failed"), { status }),
    ),
  } as unknown as InsightsDataAdapter;
  render(
    <RunDetails
      adapter={adapter}
      projectId="project-a"
      queryId="query-a"
      tapperBaseUrl="https://tap-ai.example/chat"
      run={{
        runId: "opaque-run-1",
        externalRunId: "RUN-1042",
        sourceId: "ci",
        buildId: "BUILD-1042",
        branch: "main",
        environment: "qa",
        configuration: "browser=chromium",
        startedAt: "2026-09-24T08:00:00Z",
        instanceCount: 1,
        evidenceRefs: ["receipt-1"],
      }}
      attempts={[
        {
          factKey: "fact-1",
          externalRunId: "RUN-1042",
          stableTestId: "case-1",
          sourceTestIdentity: "suite::case-1",
          dataRow: "row-a",
          attempt: 1,
          result: "fail",
          durationSeconds: 2,
          evidenceRefs: ["receipt-1"],
        },
      ]}
      onClose={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Download raw report" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(message);
});

it("loads one receipt's evidence once for every attempt that references it", async () => {
  const adapter = {
    getReportEvidence: vi.fn().mockResolvedValue({
      reportFormat: "allure",
      attempts: ["fact-1", "fact-2"].map((factKey) => ({
        factKey,
        name: factKey,
        status: "failed",
        message: `message ${factKey}`,
        trace: "",
        steps: [],
        attachments: [],
      })),
    }),
  } as unknown as InsightsDataAdapter;
  const attempt = {
    externalRunId: "RUN-1042",
    stableTestId: "case-1",
    sourceTestIdentity: "suite::case-1",
    dataRow: null,
    result: "fail",
    durationSeconds: 1,
    evidenceRefs: ["receipt-shared"],
  };
  render(
    <RunDetails
      adapter={adapter}
      projectId="project-a"
      queryId="query-a"
      tapperBaseUrl="https://tap-ai.example/chat"
      run={{
        runId: "opaque-run-1",
        externalRunId: "RUN-1042",
        sourceId: "ci",
        buildId: "BUILD-1042",
        branch: "main",
        environment: "qa",
        configuration: "browser=chromium",
        startedAt: "2026-09-24T08:00:00Z",
        instanceCount: 1,
        evidenceRefs: ["receipt-shared"],
      }}
      attempts={[
        { ...attempt, factKey: "fact-1", attempt: 1 },
        { ...attempt, factKey: "fact-2", attempt: 2 },
      ]}
      onClose={vi.fn()}
    />,
  );
  for (const button of screen.getAllByRole("button", { name: "View report evidence" })) {
    fireEvent.click(button);
  }
  expect(await screen.findByText("message fact-1")).toBeVisible();
  expect(await screen.findByText("message fact-2")).toBeVisible();
  expect(adapter.getReportEvidence).toHaveBeenCalledTimes(1);
});
