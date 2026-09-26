import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StrictMode } from "react";

import type { InsightsDataAdapter, MetricQueryResponse } from "../api/client";
import { InsightsWorkspace } from "./InsightsWorkspace";

const completeMetric = (
  metricId:
    | "first_pass_rate"
    | "final_pass_rate"
    | "retry_recovery_rate"
    | "recovery_contribution_rate"
    | "skipped_count"
    | "p95_duration_seconds",
  value: number,
  numerator: number | null = null,
  denominator: number | null = null,
) => ({
  metricId,
  value,
  numerator,
  denominator,
  completeness: "complete" as const,
  missingReasons: [],
  evidenceRefs: ["receipt-1"],
});

function query(queryId = "query-1"): MetricQueryResponse {
  return {
    queryId,
    metricVersion: "insights-metrics-v1",
    filters: {
      sourceIds: [],
      runIds: [],
      buildIds: [],
      branches: [],
      environments: [],
      configurations: [],
    },
    from: "2026-09-01",
    to: "2026-10-01",
    timezone: "Asia/Shanghai",
    asOf: "2026-09-26T08:00:00Z",
    createdAt: "2026-09-26T08:00:00Z",
    factWatermark: { projectionVersion: "insights-v1", visibleDataVersion: 4 },
    metrics: [
      completeMetric("first_pass_rate", 1 / 3, 1, 3),
      completeMetric("final_pass_rate", 2 / 3, 2, 3),
      completeMetric("retry_recovery_rate", 1 / 2, 1, 2),
      completeMetric("recovery_contribution_rate", 1 / 3, 1, 3),
      completeMetric("skipped_count", 1, 1, null),
      completeMetric("p95_duration_seconds", 4.2, null, 3),
    ],
    trends: [
      {
        localDate: "2026-09-24",
        metrics: [
          completeMetric("first_pass_rate", 1 / 3, 1, 3),
          completeMetric("final_pass_rate", 2 / 3, 2, 3),
          completeMetric("p95_duration_seconds", 4.2, null, 3),
        ],
      },
    ],
  };
}

function adapter(overrides: Partial<InsightsDataAdapter> = {}): InsightsDataAdapter {
  return {
    listMetrics: vi.fn().mockResolvedValue({
      items: [
        {
          metricId: "first_pass_rate",
          label: "First-pass rate",
          unit: "ratio",
          definition: "First terminal pass divided by D.",
          metricVersion: "insights-metrics-v1",
        },
      ],
    }),
    createQuery: vi.fn().mockResolvedValue(query()),
    getQuery: vi.fn().mockResolvedValue(query()),
    listRuns: vi.fn().mockResolvedValue({
      queryId: "query-1",
      nextCursor: null,
      items: [
        {
          runId: "opaque-run-1",
          externalRunId: "RUN-1042",
          sourceId: "github-actions",
          buildId: "BUILD-1042",
          branch: "main",
          environment: "qa",
          configuration: "browser=chromium",
          startedAt: "2026-09-24T08:00:00Z",
          instanceCount: 4,
          evidenceRefs: ["receipt-1"],
        },
      ],
    }),
    listFailures: vi.fn().mockResolvedValue({
      queryId: "query-1",
      nextCursor: null,
      items: [
        {
          factKey: "fact-c",
          runId: "opaque-run-1",
          externalRunId: "RUN-1042",
          sourceId: "github-actions",
          stableTestId: "case-c",
          sourceTestIdentity: "suite::case-c",
          dataRow: "premium=100",
          result: "error",
          configuration: "browser=chromium",
          evidenceRefs: ["receipt-1"],
        },
      ],
    }),
    listAttempts: vi.fn().mockResolvedValue({
      queryId: "query-1",
      runId: "opaque-run-1",
      nextCursor: null,
      items: [
        {
          factKey: "fact-b1",
          externalRunId: "RUN-1042",
          stableTestId: "case-b",
          sourceTestIdentity: "suite::case-b",
          dataRow: "premium=100",
          attempt: 1,
          result: "fail",
          durationSeconds: 4.2,
          evidenceRefs: ["receipt-1"],
        },
        {
          factKey: "fact-b2",
          externalRunId: "RUN-1042",
          stableTestId: "case-b",
          sourceTestIdentity: "suite::case-b",
          dataRow: "premium=100",
          attempt: 2,
          result: "pass",
          durationSeconds: 1.1,
          evidenceRefs: ["receipt-1"],
        },
      ],
    }),
    uploadReport: vi.fn(),
    getReceipt: vi.fn(),
    retryReceipt: vi.fn(),
    downloadEvidence: vi.fn().mockResolvedValue(new Blob(["<xml/>"])),
    exportQuery: vi.fn().mockResolvedValue(new Blob(["queryId,query-1"])),
    ...overrides,
  };
}

beforeEach(() => localStorage.clear());

describe("real TAP Insights workspace", () => {
  it("initializes one authoritative query under React strict effects", async () => {
    const data = adapter();
    render(
      <StrictMode>
        <InsightsWorkspace adapter={data} projectId="project-a" />
      </StrictMode>,
    );

    expect(await screen.findByText(/Query query-1/)).toBeVisible();
    expect(data.createQuery).toHaveBeenCalledTimes(1);
  });

  it("renders server MetricResult values without recomputing rates and keeps details on the same query", async () => {
    const data = adapter();
    render(<InsightsWorkspace adapter={data} projectId="project-a" />);

    expect(await screen.findByText("66.67%")).toBeVisible();
    const cards = screen.getByRole("region", { name: "Authorized metrics" });
    expect(within(cards).getAllByText("33.33%")[0]).toBeVisible();
    expect(within(cards).getByText("50.00%")).toBeVisible();
    expect(within(cards).getByText("4.20 s")).toBeVisible();
    expect(screen.getByText(/Query query-1/)).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Open RUN-1042" }));
    expect((await screen.findAllByText("suite::case-b"))[0]).toBeVisible();
    expect(data.listAttempts).toHaveBeenCalledWith(
      "project-a",
      "query-1",
      "opaque-run-1",
    );
    expect(screen.getAllByText("premium=100")[0]).toBeVisible();
    expect(screen.getAllByText("Step details were not provided by this report.")[0]).toBeVisible();
    expect(screen.getAllByText("No screenshot was attached to this attempt.")[0]).toBeVisible();
  });

  it("applies Build, time, environment and branch as one server query scope while table search stays local", async () => {
    const data = adapter();
    const user = userEvent.setup();
    render(<InsightsWorkspace adapter={data} projectId="project-a" />);
    await screen.findByText("RUN-1042");

    await user.type(screen.getByLabelText("Build"), "BUILD-1042");
    await user.type(screen.getByLabelText("Branch"), "main");
    await user.selectOptions(screen.getByLabelText("Environment"), "qa");
    await user.click(screen.getByRole("button", { name: "Apply filters" }));

    await waitFor(() =>
      expect(data.createQuery).toHaveBeenLastCalledWith(
        "project-a",
        expect.objectContaining({
          filters: expect.objectContaining({
            buildIds: ["BUILD-1042"],
            branches: ["main"],
            environments: ["qa"],
          }),
        }),
      ),
    );
    await user.type(screen.getByLabelText("Filter this table only"), "absent");
    expect(screen.getByText("No runs match this table-only filter.")).toBeVisible();
    expect(data.createQuery).toHaveBeenCalledTimes(2);
  });

  it("recovers a persisted server query after remount instead of silently creating a new scope", async () => {
    localStorage.setItem("tap.insights.query.project-a", "query-restored");
    localStorage.setItem("tap.insights.receipts.project-a", '["receipt-1"]');
    const data = adapter({
      getQuery: vi.fn().mockResolvedValue(query("query-restored")),
      getReceipt: vi.fn().mockResolvedValue({
        receiptId: "receipt-1",
        projectId: "project-a",
        sourceId: "ci-a",
        externalRunId: "run-a",
        batchId: "batch-a",
        shardId: "1",
        checksum: "checksum",
        parserVersion: "junit-v1",
        correctionNo: 0,
        state: "ready",
        completeness: "complete",
        sizeBytes: 10,
        conflictWithReceiptId: null,
        failureReason: null,
        evidenceUrl: "/evidence/receipt-1",
      }),
      listRuns: vi.fn().mockResolvedValue({
        queryId: "query-restored",
        items: [],
        nextCursor: null,
      }),
      listFailures: vi.fn().mockResolvedValue({
        queryId: "query-restored",
        items: [],
        nextCursor: null,
      }),
    });
    render(<InsightsWorkspace adapter={data} projectId="project-a" />);
    expect(await screen.findByText(/Query query-restored/)).toBeVisible();
    expect(data.getQuery).toHaveBeenCalledWith("project-a", "query-restored");
    expect(data.createQuery).not.toHaveBeenCalled();
  });

  it.each([
    [401, "Sign in to view project insights."],
    [403, "You do not have access to this project's insights."],
    [503, "Insights are temporarily unavailable."],
  ])("distinguishes HTTP %s without fixture fallback", async (status, message) => {
    const data = adapter({
      listMetrics: vi.fn().mockRejectedValue(
        Object.assign(new Error(message), { status }),
      ),
    });
    render(<InsightsWorkspace adapter={data} projectId="project-a" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByText("RUN-1042")).not.toBeInTheDocument();
  });

  it("shows empty and missing-history states as unavailable, never zero", async () => {
    const unavailable = query();
    unavailable.metrics = unavailable.metrics.map((metric) =>
      metric.metricId === "first_pass_rate"
        ? {
            ...metric,
            numerator: null,
            denominator: null,
            value: null,
            completeness: "unavailable" as const,
            missingReasons: ["missing-first-attempt-history"],
          }
        : metric,
    );
    const data = adapter({
      createQuery: vi.fn().mockResolvedValue(unavailable),
      listRuns: vi.fn().mockResolvedValue({ queryId: "query-1", items: [], nextCursor: null }),
      listFailures: vi.fn().mockResolvedValue({ queryId: "query-1", items: [], nextCursor: null }),
    });
    render(<InsightsWorkspace adapter={data} projectId="project-a" />);
    expect(await screen.findByText("First-attempt history is unavailable.")).toBeVisible();
    expect(screen.getByText("No runs match this authorized query scope.")).toBeVisible();
    expect(screen.queryByText("0.00%")) .not.toBeInTheDocument();
  });

  it("exports and shares only the persisted project query", async () => {
    const data = adapter();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(<InsightsWorkspace adapter={data} projectId="project-a" />);
    await screen.findByText(/Query query-1/);
    fireEvent.click(screen.getByRole("button", { name: "Export current query" }));
    await waitFor(() => expect(data.exportQuery).toHaveBeenCalledWith("project-a", "query-1"));
    fireEvent.click(screen.getByRole("button", { name: "Copy project link" }));
    expect(writeText).toHaveBeenCalledWith(expect.stringMatching(/queryId=query-1/));
    expect(screen.queryByText(/public link/i)).not.toBeInTheDocument();
  });

  it("groups final failures from the authorized page without inventing evidence", async () => {
    render(<InsightsWorkspace adapter={adapter()} projectId="project-a" />);
    const table = await screen.findByRole("table", { name: "Failure groups" });
    expect(within(table).getByText("error")).toBeVisible();
    expect(within(table).getByText("1")).toBeVisible();
    expect(screen.queryByText(/example log|synthetic execution/i)).not.toBeInTheDocument();
  });
});
