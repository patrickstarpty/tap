import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
    reportCoverage: [],
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((fulfill, fail) => {
    resolve = fulfill;
    reject = fail;
  });
  return { promise, resolve, reject };
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
  it("keeps the latest receipt-ready query and all its details when an earlier Apply finishes late", async () => {
    const slowRuns = deferred<Awaited<ReturnType<InsightsDataAdapter["listRuns"]>>>();
    const a = query("query-a");
    const b = query("query-b");
    b.metrics[0] = completeMetric("first_pass_rate", 0.75, 3, 4);
    const run = (queryId: string) => ({
      queryId,
      nextCursor: null,
      items: [{
        runId: `run-${queryId}`,
        externalRunId: `RUN-${queryId}`,
        sourceId: "ci",
        buildId: null,
        branch: null,
        environment: "qa",
        configuration: "browser=chromium",
        startedAt: null,
        instanceCount: 1,
        evidenceRefs: [],
      }],
    });
    const failure = (queryId: string) => ({
      queryId,
      nextCursor: null,
      items: [{
        factKey: `failure-${queryId}`,
        runId: `run-${queryId}`,
        externalRunId: `RUN-${queryId}`,
        sourceId: "ci",
        stableTestId: "case-1",
        sourceTestIdentity: "suite::case-1",
        dataRow: null,
        result: queryId === "query-b" ? "fail" as const : "error" as const,
        configuration: "browser=chromium",
        evidenceRefs: [],
      }],
    });
    const receipt = {
      receiptId: "receipt-ready",
      projectId: "project-a",
      sourceId: "ci",
      externalRunId: "RUN-query-b",
      batchId: "batch-1",
      shardId: "1",
      checksum: "checksum",
      parserVersion: "junit-v1",
      correctionNo: 0,
      state: "ready" as const,
      completeness: "complete" as const,
      sizeBytes: 10,
      conflictWithReceiptId: null,
      failureReason: null,
      evidenceUrl: "/evidence/receipt-ready",
    };
    const data = adapter({
      createQuery: vi.fn()
        .mockResolvedValueOnce(query())
        .mockResolvedValueOnce(a)
        .mockResolvedValueOnce(b),
      listRuns: vi.fn((_: string, queryId: string) =>
        queryId === "query-a" ? slowRuns.promise : Promise.resolve(run(queryId))),
      listFailures: vi.fn((_: string, queryId: string) => Promise.resolve(failure(queryId))),
      listAttempts: vi.fn((_: string, queryId: string, runId: string) => Promise.resolve({
        queryId, runId, nextCursor: null,
        items: [{
          factKey: "attempt-b", externalRunId: "RUN-query-b", stableTestId: "case-b",
          sourceTestIdentity: "suite::case-b", dataRow: null,
          attempt: 1, result: "pass", durationSeconds: 1, evidenceRefs: [],
        }],
      })),
      uploadReport: vi.fn().mockResolvedValue(receipt),
      getReceipt: vi.fn().mockResolvedValue(receipt),
    });
    render(<InsightsWorkspace adapter={data} projectId="project-a" />);
    await screen.findByText("RUN-query-1");

    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    await waitFor(() => expect(data.listRuns).toHaveBeenCalledWith("project-a", "query-a"));

    const intake = screen.getByText("Upload JUnit report").closest("details")!;
    for (const [name, value] of Object.entries({
      sourceId: "ci", externalRunId: "RUN-query-b", batchId: "batch-1", shardId: "1",
      applicationCommit: "abc", scriptCommit: "def", environment: "qa",
      configuration: "browser=chromium", startedAt: "2026-09-24T08:00",
    })) {
      fireEvent.change(intake.querySelector<HTMLInputElement>(`[name="${name}"]`)!, {
        target: { value },
      });
    }
    fireEvent.change(intake.querySelector<HTMLInputElement>('input[type="file"]')!, {
      target: { files: [new File(["<testsuite/>"] , "report.xml", { type: "application/xml" })] },
    });
    fireEvent.click(within(intake).getByRole("button", { name: "Upload report" }));

    expect(await screen.findByText(/Query query-b/)).toBeVisible();
    expect(screen.getByText("75.00%")).toBeVisible();
    expect(within(screen.getByRole("table", { name: "Runs" })).getByText("RUN-query-b")).toBeVisible();
    expect(within(screen.getByRole("table", { name: "Failure groups" })).getByText("fail")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Open RUN-query-b" }));
    expect(await screen.findByRole("region", { name: "Run RUN-query-b details" })).toHaveTextContent("suite::case-b");
    await act(async () => slowRuns.resolve(run("query-a")));
    expect(screen.getByText(/Query query-b/)).toBeVisible();
    expect(screen.getByText("75.00%")).toBeVisible();
    expect(within(screen.getByRole("table", { name: "Runs" })).getByText("RUN-query-b")).toBeVisible();
    expect(screen.queryByText("RUN-query-a")).not.toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "Failure groups" })).getByText("fail")).toBeVisible();
    expect(screen.getByRole("region", { name: "Run RUN-query-b details" })).toHaveTextContent("suite::case-b");
    expect(localStorage.getItem("tap.insights.query.project-a")).toBe("query-b");
  });

  it("ignores an older run's attempts and failure after a newer run opens", async () => {
    const slowAttempts = deferred<Awaited<ReturnType<InsightsDataAdapter["listAttempts"]>>>();
    const failedAttempts = deferred<Awaited<ReturnType<InsightsDataAdapter["listAttempts"]>>>();
    let olderRequestCount = 0;
    const data = adapter({
      listRuns: vi.fn().mockResolvedValue({
        queryId: "query-1", nextCursor: null,
        items: ["a", "b"].map((id) => ({
          runId: `run-${id}`, externalRunId: `RUN-${id}`, sourceId: "ci",
          buildId: null, branch: null, environment: "qa",
          configuration: "browser=chromium", startedAt: null,
          instanceCount: 1, evidenceRefs: [],
        })),
      }),
      listAttempts: vi.fn((_: string, queryId: string, runId: string) =>
        runId === "run-a" ? (++olderRequestCount === 1 ? slowAttempts.promise : failedAttempts.promise) : Promise.resolve({
          queryId, runId, nextCursor: null,
          items: [{
            factKey: "attempt-b", externalRunId: "RUN-b", stableTestId: "case-b",
            sourceTestIdentity: "suite::case-b", dataRow: null,
            attempt: 1, result: "pass", durationSeconds: 1,
            evidenceRefs: [],
          }],
        })),
    });
    render(<InsightsWorkspace adapter={data} projectId="project-a" />);
    await screen.findByText("RUN-a");
    fireEvent.click(screen.getByRole("button", { name: "Open RUN-a" }));
    fireEvent.click(screen.getByRole("button", { name: "Open RUN-b" }));
    expect(await screen.findByRole("region", { name: "Run RUN-b details" })).toHaveTextContent("suite::case-b");
    await act(async () => slowAttempts.resolve({
      queryId: "query-1", runId: "run-a", nextCursor: null,
      items: [{
        factKey: "attempt-a", externalRunId: "RUN-a", stableTestId: "case-a",
        sourceTestIdentity: "suite::case-a", dataRow: null,
        attempt: 1, result: "fail", durationSeconds: 2, evidenceRefs: [],
      }],
    }));
    expect(screen.getByRole("region", { name: "Run RUN-b details" })).not.toHaveTextContent("suite::case-a");

    fireEvent.click(screen.getByRole("button", { name: "Open RUN-a" }));
    fireEvent.click(screen.getByRole("button", { name: "Open RUN-b" }));
    expect(await screen.findByRole("region", { name: "Run RUN-b details" })).toHaveTextContent("suite::case-b");
    await act(async () => failedAttempts.reject(new Error("older attempt failed")));
    expect(screen.getByRole("region", { name: "Run RUN-b details" })).toHaveTextContent("suite::case-b");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps the newer loading state when an older query fails", async () => {
    const aRuns = deferred<Awaited<ReturnType<InsightsDataAdapter["listRuns"]>>>();
    const bRuns = deferred<Awaited<ReturnType<InsightsDataAdapter["listRuns"]>>>();
    const data = adapter({
      createQuery: vi.fn()
        .mockResolvedValueOnce(query())
        .mockResolvedValueOnce(query("query-a"))
        .mockResolvedValueOnce(query("query-b")),
      listRuns: vi.fn((_: string, queryId: string) =>
        queryId === "query-a" ? aRuns.promise : queryId === "query-b" ? bRuns.promise :
          Promise.resolve({ queryId, nextCursor: null, items: [] })),
      listFailures: vi.fn((_: string, queryId: string) =>
        Promise.resolve({ queryId, nextCursor: null, items: [] })),
    });
    render(<InsightsWorkspace adapter={data} projectId="project-a" />);
    await screen.findByText(/Query query-1/);
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    await waitFor(() => expect(data.listRuns).toHaveBeenCalledWith("project-a", "query-a"));
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    await waitFor(() => expect(data.listRuns).toHaveBeenCalledWith("project-a", "query-b"));

    await act(async () => aRuns.reject(new Error("older query failed")));
    expect(screen.getByRole("region", { name: "Loading insights" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await act(async () => bRuns.resolve({ queryId: "query-b", nextCursor: null, items: [] }));
    expect(screen.getByText(/Query query-b/)).toBeVisible();
    expect(screen.queryByRole("region", { name: "Loading insights" })).not.toBeInTheDocument();
  });

  it("does not persist an unfinished query after unmount", async () => {
    const runs = deferred<Awaited<ReturnType<InsightsDataAdapter["listRuns"]>>>();
    const data = adapter({
      listRuns: vi.fn(() => runs.promise),
      listFailures: vi.fn().mockResolvedValue({ queryId: "query-1", nextCursor: null, items: [] }),
    });
    const view = render(<InsightsWorkspace adapter={data} projectId="project-a" />);
    await waitFor(() => expect(data.listRuns).toHaveBeenCalledWith("project-a", "query-1"));
    view.unmount();
    await act(async () => runs.resolve({ queryId: "query-1", nextCursor: null, items: [] }));
    expect(localStorage.getItem("tap.insights.query.project-a")).toBeNull();
  });

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

  it.each([
    ["partial", 2, "Partial report coverage: 1 of 2 shards received"],
    ["unknown", null, "Report coverage unknown: expected shard count is unavailable"],
  ] as const)("explains %s report coverage without showing a normal metric value", async (completeness, expectedShards, message) => {
    const incomplete = Object.assign(query("coverage-query"), {
      reportCoverage: [{
        sourceId: "ci", externalRunId: "RUN-42", reportBatchId: "batch-1",
        expectedShards, receivedShards: 1, completeness,
        missingReasons: [completeness === "partial" ? "report-shards-missing" : "expected-shards-unknown"],
      }],
    });
    incomplete.metrics = incomplete.metrics.map((value) => ({
      ...value, numerator: null, denominator: null, value: null,
      completeness: "unavailable" as const,
      missingReasons: incomplete.reportCoverage[0]!.missingReasons,
    }));
    incomplete.trends = [];
    const data = adapter({
      createQuery: vi.fn().mockResolvedValue(incomplete),
      listRuns: vi.fn().mockResolvedValue({ queryId: "coverage-query", items: [], nextCursor: null }),
      listFailures: vi.fn().mockResolvedValue({ queryId: "coverage-query", items: [], nextCursor: null }),
    });
    render(<InsightsWorkspace adapter={data} projectId="project-a" />);

    expect(await screen.findByRole("status")).toHaveTextContent(message);
    expect(screen.getByRole("status")).toHaveTextContent("ci / RUN-42 (batch batch-1)");
    const cards = screen.getByRole("region", { name: "Authorized metrics" });
    expect(within(cards).queryByText("0.00%")).not.toBeInTheDocument();
    expect(within(cards).queryByText("33.33%")).not.toBeInTheDocument();
    expect(within(cards).getAllByText("—")).toHaveLength(6);
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
