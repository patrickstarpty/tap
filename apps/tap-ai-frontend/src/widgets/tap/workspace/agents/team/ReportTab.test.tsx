import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useTeamReport } from "../../../../../features/knowledge/api/agentTeam";
import { WORKSPACE_COPY } from "../../copy";
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

  it("sums member trends per bucket", () => {
    const series = teamTrend(
      [
        member("a", [
          ["d1", 1],
          ["d2", 2],
        ]),
        member("b", [["d1", 4]]),
      ] as never,
      { runs: "Runs", failures: "Failures" },
    );
    expect(series[0].points).toEqual([
      { bucket: "d1", value: 5 },
      { bucket: "d2", value: 2 },
    ]);
  });
});
