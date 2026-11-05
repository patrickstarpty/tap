import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
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
  members: unknown[] = [member("a1", [["2026-09-01", 3]])],
  extra: Record<string, unknown> = {},
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
          members,
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
    isPlaceholderData: false,
    refetch: vi.fn(),
    ...extra,
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
    expect(vi.mocked(useTeamReport)).toHaveBeenCalledWith(
      "p1",
      { from: "2026-09-01", to: "2026-09-30" },
      "lastMonth",
    );
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

  it("shows period activity instead of the 30-day status in the roster", () => {
    mockReport({}, {}, "ok", [
      { ...member("ran", []), status: "idle", runs: 5 },
      { ...member("quiet", []), status: "active", runs: 0 },
      { ...member("off", []), status: "disabled", runs: 0 },
    ]);
    render(<ReportTab projectId="p1" copy={WORKSPACE_COPY.en} />);
    expect(screen.getByRole("row", { name: /^ran/ })).toHaveTextContent(
      "Ran this period",
    );
    expect(screen.getByRole("row", { name: /^quiet/ })).toHaveTextContent(
      "No runs this period",
    );
    expect(screen.getByRole("row", { name: /^off/ })).toHaveTextContent(
      "Disabled",
    );
    expect(screen.getByRole("row", { name: /^ran/ })).not.toHaveTextContent(
      "Idle",
    );
  });

  it("names platform-maintained owners in the roster", () => {
    mockReport({}, {}, "ok", [
      { ...member("default", []), kind: "tapper_default", ownerActorId: null },
    ]);
    render(<ReportTab projectId="p1" copy={WORKSPACE_COPY.zh} />);
    expect(screen.getByRole("row", { name: /^Tapper 默认/ })).toHaveTextContent(
      "平台维护",
    );
  });

  it("names the Tapper default member by language in the roster", () => {
    mockReport({}, {}, "ok", [
      {
        ...member("tapper_default", []),
        name: "Tapper 默认",
        kind: "tapper_default",
        ownerActorId: null,
      },
    ]);
    render(<ReportTab projectId="p1" copy={WORKSPACE_COPY.en} />);
    expect(screen.getByRole("row", { name: /^Tapper / })).toBeVisible();
    expect(screen.queryByText("Tapper 默认")).toBeNull();
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

  it("downloads the CSV export for the shown period", async () => {
    mockReport();
    const fetchMock = vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        blob: () => Promise.resolve(new Blob(["zip"])),
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    // jsdom has no object URLs.
    const urls = URL as unknown as Record<string, unknown>;
    urls.createObjectURL = vi.fn(() => "blob:x");
    urls.revokeObjectURL = vi.fn();
    const clicked: string[] = [];
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        clicked.push(this.download);
      });
    render(<ReportTab projectId="p1" copy={WORKSPACE_COPY.en} />);
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    const url = String((fetchMock.mock.calls[0] as unknown[])[0]);
    expect(url).toContain("agent-team/export");
    expect(url).toContain("from=2026-09-01");
    expect(url).toContain("to=2026-09-30");
    expect(clicked).toEqual(["agent-team-2026-09-01-2026-09-30.zip"]);
    expect(urls.revokeObjectURL).toHaveBeenCalledWith("blob:x");
    expect(screen.queryByRole("alert")).toBeNull();
    delete urls.createObjectURL;
    delete urls.revokeObjectURL;
    vi.unstubAllGlobals();
  });

  it("shows progress and an inline error when the CSV export fails", async () => {
    mockReport();
    let fail!: () => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            fail = () => resolve(new Response("{}", { status: 500 }));
          }),
      ),
    );
    render(<ReportTab projectId="p1" copy={WORKSPACE_COPY.zh} />);
    const button = screen.getByRole("button", { name: "导出 CSV" });
    await userEvent.click(button);
    await waitFor(() => expect(button).toHaveClass("ant-btn-loading"));
    fail();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "导出 CSV 失败，请重试。",
    );
    await waitFor(() => expect(button).not.toHaveClass("ant-btn-loading"));
    vi.unstubAllGlobals();
  });

  it("does not query an inverted custom range and keeps the last report", async () => {
    mockReport();
    render(<ReportTab projectId="p1" copy={WORKSPACE_COPY.zh} />);
    await userEvent.selectOptions(screen.getByLabelText("周期"), "custom");
    fireEvent.change(screen.getByLabelText("开始"), {
      target: { value: "2026-10-18" },
    });
    expect(screen.getByText("开始日期不能晚于结束日期")).toBeVisible();
    const periods = vi.mocked(useTeamReport).mock.calls.map((call) => call[1]);
    expect(periods).not.toContainEqual({
      from: "2026-10-18",
      to: "2026-09-30",
    });
    expect(periods.at(-1)).toEqual({ from: "2026-09-01", to: "2026-09-30" });
    expect(screen.getByText("Agent 团队汇报")).toBeVisible();
    expect(screen.getByRole("button", { name: "导出 CSV" })).toBeDisabled();
  });

  it("shows a refreshing line while a changed period loads", () => {
    mockReport({}, {}, "ok", undefined, { isPlaceholderData: true });
    render(<ReportTab projectId="p1" copy={WORKSPACE_COPY.zh} />);
    expect(screen.getByRole("status")).toHaveTextContent("正在加载团队数据…");
    // The header names the period on screen, not the one still loading.
    expect(screen.getByText(/周期: 2026-09-01 – 2026-09-30/)).toBeVisible();
    expect(screen.getByRole("button", { name: "导出 CSV" })).toBeDisabled();
  });

  it("says the trend is unavailable instead of empty during an outage", () => {
    mockReport({ runs: null }, {}, "unavailable", [
      { ...member("a1", []), runs: null },
    ]);
    render(<ReportTab projectId="p1" copy={WORKSPACE_COPY.zh} />);
    const section = screen
      .getByRole("heading", { name: "运行趋势" })
      .closest("section")!;
    expect(section).toHaveTextContent("运行数据暂不可用");
    expect(section).not.toHaveTextContent("本周期没有运行趋势");
  });

  it("labels the trend with a count unit, not a metric name", () => {
    mockReport();
    render(<ReportTab projectId="p1" copy={WORKSPACE_COPY.zh} />);
    expect(screen.getByText("3 次")).toBeInTheDocument();
    expect(screen.queryByText(/运行次数$/, { selector: "div" })).toBeNull();
  });

  it("marks summary values by runtime status", () => {
    mockReport({ servedAvg: null }, {}, "syncing");
    render(<ReportTab projectId="p1" copy={WORKSPACE_COPY.zh} />);
    const runs = within(
      screen.getByRole("row", { name: /^运行次数/ }),
    ).getAllByRole("cell");
    expect(runs[0]).toHaveTextContent("120运行数据同步中");
    const total = within(
      screen.getByRole("row", { name: /^Agent 数/ }),
    ).getAllByRole("cell");
    expect(total[0]).toHaveTextContent(/^3$/);
    // A legitimately empty value is a dash, not "syncing".
    const served = within(
      screen.getByRole("row", { name: /^每位使用者/ }),
    ).getAllByRole("cell");
    expect(served[0]).toHaveTextContent(/^—$/);
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
    for (const name of [/^Agents(?! (per|used))/, /^Idle/]) {
      const cells = within(screen.getByRole("row", { name })).getAllByRole(
        "cell",
      );
      expect(cells[0]).not.toHaveTextContent("—");
      expect(cells[1]).toHaveTextContent("—");
      expect(cells.at(-1)).toHaveTextContent("—");
    }
  });
});
