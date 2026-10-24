import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useTeamReport } from "../../../../../features/knowledge/api/agentTeam";
import { WORKSPACE_COPY } from "../../copy";
import { bucketAxis } from "./buckets";
import { ReportTab, teamTrend } from "./ReportTab";

vi.mock(
  "../../../../../features/knowledge/api/agentTeam",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("../../../../../features/knowledge/api/agentTeam")
      >();
    return { ...actual, useTeamReport: vi.fn() };
  },
);

function summary(over: Record<string, unknown> = {}) {
  return {
    active: 2,
    agentTotal: 3,
    costUsd: "1.5",
    idle: 1,
    latencyTotalMs: 1000,
    maintenanceTotal: 0,
    ownersAvg: 1,
    runs: 120,
    servedAvg: 2,
    ...over,
  };
}

function member(key: string, trend: [string, number][]) {
  return {
    agentKey: key,
    name: key,
    kind: "custom",
    jobTitle: null,
    ownerActorId: "alice",
    status: "active",
    runs: 5,
    failures: 0,
    abstained: 0,
    canceled: 0,
    latencyTotalMs: 10,
    latencyP50Ms: 5,
    costUsd: "0.1",
    satisfaction: null,
    satisfactionSample: "insufficient",
    servedCount: 1,
    topServed: [],
    lastRunAt: null,
    lastMaintenanceAt: null,
    joinedAt: null,
    pendingFeedback: 0,
    maintenance: {},
    callsPerRun: null,
    generationCalls: null,
    toolCalls: null,
    trend: trend.map(([bucketStart, runs]) => ({
      agentKey: key,
      bucketStart,
      runs,
      failures: 0,
      costUsd: "0",
      latencyTotalMs: 0,
    })),
  };
}

function mockReport(
  current: Record<string, unknown> = {},
  previous: Record<string, unknown> = {},
  runtimeStatus = "ok",
) {
  vi.mocked(useTeamReport).mockReturnValue({
    data: {
      computedAt: "2026-10-23T10:00:00Z",
      granularity: "day",
      period: { from: "2026-09-01", to: "2026-09-30" },
      runtimeStatus,
      timezone: "UTC",
      data: {
        current: {
          summary: summary(current),
          members: [member("a1", [["2026-09-01", 3]])],
          people: [],
        },
        previousPeriod: { from: "2026-08-01", to: "2026-08-31" },
        previous: {
          summary: summary(previous),
          members: [],
          people: [],
        },
      },
    },
    isError: false,
    refetch: vi.fn(),
  } as never);
}

describe("ReportTab", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-23T12:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("defaults to last month", () => {
    mockReport();
    render(<ReportTab projectId="p1" copy={WORKSPACE_COPY.en} />);
    expect(vi.mocked(useTeamReport)).toHaveBeenCalledWith("p1", {
      from: "2026-09-01",
      to: "2026-09-30",
    });
    expect(screen.getByLabelText("Period")).toHaveValue("lastMonth");
  });

  it("computes changes and shows a dash when the previous value is 0", () => {
    mockReport(
      { runs: 120, maintenanceTotal: 4 },
      { runs: 100, maintenanceTotal: 0 },
    );
    render(<ReportTab projectId="p1" copy={WORKSPACE_COPY.en} />);
    const runs = screen.getByRole("row", { name: /^Runs/ });
    expect(runs).toHaveTextContent("▲ +20 (+20%)");
    const maintenance = screen.getByRole("row", {
      name: /^Maintenance actions/,
    });
    const cells = within(maintenance).getAllByRole("cell");
    expect(cells[1]).toHaveTextContent(/^0$/);
    expect(cells.at(-1)).toHaveTextContent("—");
  });

  it("names the previous period it compares against", () => {
    mockReport();
    render(<ReportTab projectId="p1" copy={WORKSPACE_COPY.zh} />);
    expect(screen.getAllByText(/对比：2026-08-01 – 2026-08-31/).length).toBe(2);
  });

  it("shows a negative change and a dash for a null previous value", () => {
    mockReport({ runs: 90, cost: null }, { runs: 100, latencyTotalMs: null });
    render(<ReportTab projectId="p1" copy={WORKSPACE_COPY.en} />);
    expect(screen.getByRole("row", { name: /^Runs/ })).toHaveTextContent(
      "▼ −10 (−10%)",
    );
    const latency = screen.getByRole("row", { name: /^Total latency/ });
    expect(within(latency).getAllByRole("cell").at(-1)).toHaveTextContent("—");
  });

  it("never renders a minus-zero percent", () => {
    mockReport({ runs: 1001 }, { runs: 1000 });
    render(<ReportTab projectId="p1" copy={WORKSPACE_COPY.en} />);
    const text = screen.getByRole("row", { name: /^Runs/ }).textContent ?? "";
    expect(text).toContain("(0%)");
    expect(text).not.toContain("−0%");
  });

  it("keeps the class contract the print styles depend on", () => {
    mockReport();
    const { container } = render(
      <ReportTab projectId="p1" copy={WORKSPACE_COPY.en} />,
    );
    const root = container.querySelector(".tap-team-report");
    expect(root).not.toBeNull();
    expect(root?.querySelector(".tap-team-report-controls")).not.toBeNull();
    expect(root?.querySelector(".tap-team-report-header")).not.toBeNull();
    expect(root?.querySelector(".tap-team-report-table")).not.toBeNull();
    // Read-only roster: names are plain text, no sort controls.
    expect(root?.querySelector(".tap-team-card-name")).toBeNull();
    expect(root?.querySelector(".ant-table-column-has-sorters")).toBeNull();
  });

  it("prints through window.print", async () => {
    mockReport();
    const print = vi.fn();
    vi.stubGlobal("print", print);
    render(<ReportTab projectId="p1" copy={WORKSPACE_COPY.en} />);
    await userEvent.click(screen.getByRole("button", { name: "Export PDF" }));
    expect(print).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
  });

  it("links CSV export with the period", () => {
    mockReport();
    render(<ReportTab projectId="p1" copy={WORKSPACE_COPY.en} />);
    const href =
      screen.getByRole("link", { name: "Export CSV" }).getAttribute("href") ??
      "";
    expect(href).toContain("agent-team/export");
    expect(href).toContain("from=2026-09-01");
    expect(href).toContain("to=2026-09-30");
  });

  it("flags unavailable run data and dashes comparisons", () => {
    mockReport(
      {
        runs: null,
        active: null,
        idle: null,
        latencyTotalMs: null,
        costUsd: null,
      },
      { runs: 10 },
      "unavailable",
    );
    render(<ReportTab projectId="p1" copy={WORKSPACE_COPY.zh} />);
    expect(screen.getAllByText("运行数据暂不可用").length).toBeGreaterThan(0);
    const runs = screen.getByRole("row", { name: /^运行/ });
    expect(within(runs).getAllByRole("cell").at(-1)).toHaveTextContent("—");
  });

  it("sums member trends per bucket on a full time axis with zero fill", () => {
    const series = teamTrend(
      [
        member("a", [
          ["2026-10-01", 1],
          ["2026-10-03", 2],
        ]),
        member("b", [["2026-10-01", 4]]),
      ] as never,
      { runs: "Runs", failures: "Failures" },
      bucketAxis({ from: "2026-10-01", to: "2026-10-04" }, "day"),
    );
    expect(series[0].points.map((p) => [p.bucket, p.value])).toEqual([
      ["2026-10-01", 5],
      ["2026-10-02", 0],
      ["2026-10-03", 2],
      ["2026-10-04", 0],
    ]);
  });

  it("shows only the current value for point-in-time agent counts", () => {
    mockReport({ agentTotal: 7, idle: 3 }, { agentTotal: 5, idle: 1 });
    render(<ReportTab projectId="p1" copy={WORKSPACE_COPY.en} />);
    for (const name of [/^Agents/, /^Idle/]) {
      const cells = within(screen.getByRole("row", { name })).getAllByRole(
        "cell",
      );
      expect(cells[0]).not.toHaveTextContent("—");
      expect(cells[1]).toHaveTextContent("—");
      expect(cells.at(-1)).toHaveTextContent("—");
    }
  });
});
