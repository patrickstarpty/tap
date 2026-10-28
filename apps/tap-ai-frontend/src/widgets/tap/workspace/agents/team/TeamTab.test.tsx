import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useOrgChart,
  useTeamMembers,
  useTeamPeople,
  useTeamReport,
  useTeamSummary,
} from "../../../../../features/knowledge/api/agentTeam";
import { WORKSPACE_COPY } from "../../copy";
import { TeamTab } from "./TeamTab";

vi.mock(
  "../../../../../features/knowledge/api/agentTeam",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("../../../../../features/knowledge/api/agentTeam")
      >();
    return {
      ...actual,
      useTeamSummary: vi.fn(),
      useTeamMembers: vi.fn(),
      useTeamPeople: vi.fn(),
      useOrgChart: vi.fn(),
      useTeamReport: vi.fn(),
    };
  },
);

const period = { from: "2026-09-24", to: "2026-10-23" };

function row(over: Record<string, unknown>) {
  return {
    agentKey: "a1",
    name: "Reviewer",
    kind: "custom",
    jobTitle: "Test reviewer",
    ownerActorId: "alice",
    status: "active",
    runs: 12,
    failures: 1,
    abstained: 0,
    canceled: 0,
    latencyTotalMs: 90_000,
    latencyP50Ms: 800,
    costUsd: "1.5",
    satisfaction: 0.8,
    satisfactionSample: "ok",
    servedCount: 3,
    topServed: [
      { actorId: "u1", runs: 5 },
      { actorId: "u2", runs: 4 },
      { actorId: "u3", runs: 2 },
      { actorId: "u4", runs: 1 },
    ],
    lastRunAt: "2026-10-22T10:30:00Z",
    lastMaintenanceAt: "2026-10-20T09:00:00Z",
    joinedAt: "2026-09-01T00:00:00Z",
    pendingFeedback: 2,
    maintenance: {},
    trend: [
      {
        agentKey: "a1",
        bucketStart: "2026-10-21",
        runs: 3,
        failures: 0,
        latencyTotalMs: 0,
        costUsd: "0",
      },
      {
        agentKey: "a1",
        bucketStart: "2026-10-22",
        runs: 9,
        failures: 1,
        latencyTotalMs: 0,
        costUsd: "0",
      },
    ],
    ...over,
  };
}

const base = {
  runtimeStatus: "ok",
  computedAt: "2026-10-23T08:00:00Z",
  timezone: "UTC",
  period,
  granularity: "day",
};

function ready(data: unknown) {
  return { isPending: false, isError: false, refetch: vi.fn(), data };
}

// The cards' sparkline comes from a separate last-30-days query.
function sparkRows(members: unknown[]) {
  return members.map((m) => ({
    ...(m as Record<string, unknown>),
    trend: [1, 5, 2].map((runs, i) => ({
      agentKey: (m as { agentKey: string }).agentKey,
      bucketStart: `2026-10-0${i + 1}`,
      runs,
    })),
  }));
}

function periodCalls() {
  return vi
    .mocked(useTeamMembers)
    .mock.calls.filter((call) => call[1] === period);
}

const SUMMARY = {
  agentTotal: 4,
  active: 2,
  idle: 1,
  ownersAvg: 1.5,
  servedAvg: 2,
  runs: 120,
  latencyTotalMs: 3_600_000,
  costUsd: "12.5",
  maintenanceTotal: 7,
};

/** The report supplies the previous period behind the headline deltas. */
function mockReport(previous: Record<string, unknown> = {}, extra = {}) {
  vi.mocked(useTeamReport).mockReturnValue({
    ...ready({
      ...base,
      data: {
        current: { summary: SUMMARY, members: [], people: [] },
        previous: {
          summary: {
            ...SUMMARY,
            runs: 100,
            costUsd: "10",
            active: 2,
            ...previous,
          },
          members: [],
          people: [],
        },
        previousPeriod: { from: "2026-08-25", to: "2026-09-23" },
      },
    }),
    isPlaceholderData: false,
    ...extra,
  } as never);
}

function setup(
  members: unknown[],
  runtimeStatus: "ok" | "syncing" | "unavailable" = "ok",
  summary: Record<string, unknown> = {},
) {
  const b = { ...base, runtimeStatus };
  mockReport();
  vi.mocked(useTeamSummary).mockReturnValue(
    ready({
      ...b,
      data: { ...SUMMARY, ...summary },
    }) as never,
  );
  vi.mocked(useTeamMembers).mockImplementation(((_id: string, per: unknown) =>
    ready({
      ...b,
      data: per === period ? members : sparkRows(members),
    })) as never);
  vi.mocked(useOrgChart).mockReturnValue(
    ready({
      ...b,
      data: { people: [], agents: [], edges: [] },
    }) as never,
  );
  vi.mocked(useTeamPeople).mockReturnValue(
    ready({
      ...b,
      data: [
        {
          actorId: "bob",
          ownedAgents: 1,
          ownedRuns: 5,
          servedByAgents: 1,
          runs: 9,
          maintenance: 0,
        },
        {
          actorId: "alice",
          ownedAgents: 3,
          ownedRuns: 50,
          servedByAgents: 2,
          runs: 4,
          maintenance: 6,
        },
      ],
    }) as never,
  );
}

function renderTab(
  lang: "en" | "zh" = "zh",
  handlers: { open?: () => void; create?: () => void } = {},
) {
  return render(
    <TeamTab
      projectId="p1"
      copy={WORKSPACE_COPY[lang]}
      period={period}
      preset="last30"
      onOpenMember={handlers.open ?? vi.fn()}
      onCreateAgent={handlers.create ?? vi.fn()}
    />,
  );
}

const showCards = (lang: "en" | "zh" = "zh") =>
  userEvent.click(
    screen.getByRole("button", { name: lang === "zh" ? "卡片" : "Cards" }),
  );

beforeEach(() => vi.clearAllMocks());

describe("TeamTab", () => {
  it("shows the summary numbers and freshness line", () => {
    setup([row({})]);
    renderTab();
    expect(
      screen.getByText("运行次数", { selector: "dt" }).nextSibling,
    ).toHaveTextContent("120");
    expect(screen.getByText("Agent 数").nextSibling).toHaveTextContent("4");
    expect(
      screen.getByText("每位负责人管理 Agent 数").nextSibling,
    ).toHaveTextContent("1.5");
    expect(screen.getByText("维护动作").nextSibling).toHaveTextContent("7");
    expect(screen.getByText("数据更新于 08:00（UTC）")).toBeVisible();
  });

  it("renders a leaderboard row with every field", () => {
    setup([row({ servedCount: 5 })]);
    renderTab();
    const tableRow = screen
      .getByRole("button", { name: "Reviewer" })
      .closest("tr")!;
    expect(
      within(tableRow).getByText("R", { selector: ".tap-team-avatar" }),
    ).toBeVisible();
    expect(within(tableRow).getByText("Test reviewer")).toBeVisible();
    expect(within(tableRow).getByText("alice")).toBeVisible();
    expect(
      within(tableRow).getByText("活跃", { selector: ".tap-team-status-pill" }),
    ).toBeVisible();
    expect(within(tableRow).getByText("80%")).toBeVisible();
    // First three people served as initials, then the rest as "+n".
    expect(within(tableRow).getByText("u1, u2, u3")).toBeInTheDocument();
    expect(within(tableRow).getByText("+2")).toBeVisible();
    expect(
      within(tableRow).getByRole("img", { name: /Reviewer/ }),
    ).toBeVisible();
    // Relative last run, the exact time on hover.
    expect(within(tableRow).getByTitle("2026/10/22 10:30")).toBeVisible();
    expect(tableRow).toHaveTextContent(/周期内运行\s*12(?!\d)/u);
  });

  it("renders a member card with every field", async () => {
    setup([row({})]);
    renderTab();
    await showCards();
    const card = screen
      .getByRole("button", { name: "Reviewer" })
      .closest("li") as HTMLElement;
    expect(
      within(card).getByText("R", { selector: ".tap-team-avatar" }),
    ).toBeVisible();
    expect(within(card).getByText("Test reviewer")).toBeVisible();
    expect(within(card).getByText("alice", { selector: "dd" })).toBeVisible();
    expect(
      within(card).getByText("活跃", { selector: ".tap-team-status-pill" }),
    ).toBeVisible();
    expect(within(card).getByRole("img", { name: /Reviewer/ })).toBeVisible();
    expect(within(card).getByText("80%")).toBeVisible();
    expect(within(card).getByText("u1, u2, u3")).toBeInTheDocument();
    expect(within(card).getByTitle("2026/10/22 10:30")).toBeVisible();
    expect(within(card).getByTitle("2026/10/20 09:00")).toBeVisible();
  });

  it("labels the two averages by owner and by caller", () => {
    setup([row({})]);
    renderTab();
    expect(screen.getByText("每位负责人管理 Agent 数")).toBeVisible();
    expect(screen.getByText("每位使用者用到的 Agent 数")).toBeVisible();
  });

  it("labels built-in members and insufficient satisfaction samples", () => {
    setup([
      row({
        kind: "built_in",
        ownerActorId: null,
        satisfaction: null,
        satisfactionSample: "insufficient",
      }),
    ]);
    renderTab();
    expect(
      screen.getByText("平台维护", { selector: ".tap-team-owner" }),
    ).toBeVisible();
    expect(screen.getByText("样本不足")).toBeVisible();
  });

  it("opens a member from the leaderboard", async () => {
    setup([row({})]);
    const open = vi.fn();
    renderTab("zh", { open });
    await userEvent.click(screen.getByRole("button", { name: "Reviewer" }));
    expect(open).toHaveBeenCalledWith("a1");
  });

  it("shows the Tapper default member as Tapper on English screens", async () => {
    const tapper = row({
      agentKey: "tapper_default",
      name: "Tapper 默认",
      kind: "tapper_default",
      ownerActorId: null,
    });
    setup([tapper]);
    const open = vi.fn();
    renderTab("en", { open });
    expect(screen.queryByText("Tapper 默认")).toBeNull();
    const tableRow = screen
      .getByRole("button", { name: "Tapper" })
      .closest("tr")!;
    expect(
      within(tableRow).getByText("T", { selector: ".tap-team-avatar" }),
    ).toBeVisible();
    expect(
      within(tableRow).getByRole("img", { name: /^Tapper /u }),
    ).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Tapper" }));
    expect(open).toHaveBeenCalledWith("tapper_default");
    // The trend legend names the same member.
    expect(
      within(
        screen.getByRole("list", { name: "Show or hide Agents" }),
      ).getByRole("button", { name: /^Tapper \d/u }),
    ).toBeVisible();
    await showCards("en");
    const card = screen
      .getByRole("button", { name: "Tapper" })
      .closest("li") as HTMLElement;
    expect(within(card).getByRole("img", { name: /^Tapper /u })).toBeVisible();
    expect(screen.queryByText("Tapper 默认")).toBeNull();
  });

  it("keeps the Tapper default member's Chinese name on Chinese screens", () => {
    setup([
      row({
        agentKey: "tapper_default",
        name: "Tapper 默认",
        kind: "tapper_default",
        ownerActorId: null,
      }),
    ]);
    renderTab("zh");
    expect(screen.getByRole("button", { name: "Tapper 默认" })).toBeVisible();
  });

  it("passes owner, status and kind filters to the request", async () => {
    setup([
      row({}),
      row({ agentKey: "a2", name: "Other", ownerActorId: "bob" }),
    ]);
    renderTab();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "负责人" }),
      "bob",
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "状态" }),
      "idle",
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "类型" }),
      "built_in",
    );
    const last = periodCalls().at(-1)!;
    expect(last[2]).toMatchObject({
      owner: "bob",
      status: "idle",
      kind: "built_in",
    });
  });

  it("keeps agent names in the org chart when a roster filter applies", async () => {
    const all = [
      row({}),
      row({ agentKey: "a2", name: "Other", ownerActorId: "bob" }),
    ];
    setup(all);
    const b = { ...base, runtimeStatus: "ok" };
    vi.mocked(useTeamMembers).mockImplementation(((
      _id: string,
      per: unknown,
      f: { owner?: string | null },
    ) =>
      ready({
        ...b,
        data:
          per === period
            ? all.filter((m) => !f.owner || m.ownerActorId === f.owner)
            : sparkRows(all),
      })) as never);
    vi.mocked(useOrgChart).mockReturnValue(
      ready({
        ...b,
        data: {
          people: ["alice", "bob"],
          agents: ["a1", "a2"],
          edges: [
            { from: "alice", to: "a1", kind: "owns" },
            { from: "bob", to: "a2", kind: "owns" },
          ],
        },
      }) as never,
    );
    renderTab();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "负责人" }),
      "alice",
    );
    expect(screen.getByRole("button", { name: "Other" })).toBeTruthy();
  });

  it("sorts the leaderboard through request parameters", async () => {
    setup([row({})]);
    renderTab("en");
    const runsHeader = () =>
      screen.getAllByText("Runs in period", {
        selector: ".ant-table-column-title",
      })[0];
    await userEvent.click(runsHeader());
    expect(periodCalls().at(-1)![2]).toMatchObject({
      sort: "runs",
      order: "asc",
    });
    await userEvent.click(runsHeader());
    expect(periodCalls().at(-1)![2]).toMatchObject({
      sort: "runs",
      order: "desc",
    });
    // Keys without a column sort from the menu, largest first for metrics.
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Sort" }),
      "latency_total_ms",
    );
    expect(periodCalls().at(-1)![2]).toMatchObject({
      sort: "latency_total_ms",
      order: "desc",
    });
    await userEvent.click(screen.getByRole("button", { name: "Descending" }));
    expect(periodCalls().at(-1)![2]).toMatchObject({
      sort: "latency_total_ms",
      order: "asc",
    });
  });

  it("ranks people by calls and sorts the details table by column", async () => {
    setup([row({})]);
    renderTab("en");
    const ranking = screen
      .getByText("bob", { selector: "strong" })
      .closest("ol")!;
    expect(
      [...ranking.querySelectorAll("strong")].map((e) => e.textContent),
    ).toEqual(["bob", "alice"]);
    expect(within(ranking).getByText("Owns 3")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Details" }));
    // A sideways-scrolling table with a pinned first column renders its
    // header twice (sticky header + body measure); read the body rows.
    const title = (text: string) =>
      screen.getAllByText(text, { selector: ".ant-table-column-title" })[0];
    const table = screen
      .getAllByText("Person")
      .at(-1)!
      .closest(".ant-table-wrapper")!;
    const names = () =>
      [...table.querySelectorAll("tbody tr.ant-table-row")].map(
        (r) => r.firstElementChild!.textContent,
      );
    expect(names()).toEqual(["bob", "alice"]);
    await userEvent.click(title("Agents owned"));
    await userEvent.click(title("Agents owned"));
    expect(names()).toEqual(["alice", "bob"]);
    await userEvent.click(title("Person"));
    expect(names()).toEqual(["alice", "bob"]);
  });

  it("keeps the roster and marks run data unavailable instead of zero", async () => {
    setup(
      [
        row({
          runs: null,
          topServed: [],
          status: null,
          failures: null,
          trend: [],
          lastRunAt: null,
          satisfaction: null,
        }),
      ],
      "unavailable",
      {
        runs: null,
        active: null,
        idle: null,
        latencyTotalMs: null,
        costUsd: null,
      },
    );
    renderTab();
    expect(screen.getByRole("button", { name: "Reviewer" })).toBeVisible();
    expect(screen.getAllByText("运行数据暂不可用").length).toBeGreaterThan(1);
    expect(
      screen.queryByText("0", { selector: ".tap-team-card *" }),
    ).not.toBeInTheDocument();
    for (const label of ["运行次数", "活跃", "闲置", "总耗时", "成本"])
      expect(
        screen.getByText(label, { selector: "dt" }).nextSibling,
      ).toHaveTextContent("—");
    // Top served is unknown, not "none yet", during an outage.
    await showCards();
    expect(
      screen.getByText("主要服务对象", { selector: ".tap-team-card dt" })
        .nextSibling,
    ).toHaveTextContent("—");
  });

  it("keeps the filter focused and shows an inline indicator while the filtered query has no data", async () => {
    setup([row({})]);
    renderTab();
    const select = screen.getByRole("combobox", { name: "负责人" });
    const initial = vi.mocked(useTeamMembers).getMockImplementation()!;
    // A changed filter is a new query key: no data at all until it answers.
    vi.mocked(useTeamMembers).mockImplementation(((...args: unknown[]) => {
      const result = (initial as (...a: unknown[]) => object)(...args);
      return args[1] === period
        ? { ...result, data: undefined, isPending: true }
        : result;
    }) as never);
    select.focus();
    await userEvent.selectOptions(select, "alice");
    expect(screen.getByRole("combobox", { name: "负责人" })).toBe(select);
    expect(select).toHaveFocus();
    expect(screen.getByRole("status")).toHaveTextContent("正在加载团队数据…");
    // The previous rows stay visible underneath the indicator.
    expect(screen.getByRole("button", { name: "Reviewer" })).toBeVisible();
  });

  it("shows no refresh indicator when nothing is refetching", () => {
    setup([row({})]);
    renderTab();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it.each([
    ["Owner", "owner_actor_id"],
    ["Status", "status"],
  ])("sorts the leaderboard by %s through the request", async (title, key) => {
    setup([row({})]);
    renderTab("en");
    await userEvent.click(
      screen.getAllByText(title, { selector: ".ant-table-column-title" })[0],
    );
    expect(periodCalls().at(-1)![2]).toMatchObject({ sort: key, order: "asc" });
  });

  it.each([["job_title"], ["kind"]])(
    "sorts by %s from the sort menu, A to Z first",
    async (key) => {
      setup([row({})]);
      renderTab("en");
      await userEvent.selectOptions(
        screen.getByRole("combobox", { name: "Sort" }),
        key,
      );
      expect(periodCalls().at(-1)![2]).toMatchObject({
        sort: key,
        order: "asc",
      });
    },
  );

  it("draws the 30-day trend from the last-30-days query", async () => {
    setup([row({})]);
    renderTab();
    const calls = vi.mocked(useTeamMembers).mock.calls;
    expect(calls.some((call) => call[1] !== period)).toBe(true);
    const spark = calls.find((call) => call[1] !== period)!;
    expect(spark[2]).toEqual({});
    const points = () =>
      screen
        .getByRole("img", { name: /Reviewer.*近 30 天/ })
        .querySelector("polyline")!
        .getAttribute("points")!
        .split(" ");
    // One value per day of the 30-day window, empty days as zero.
    expect(points()).toHaveLength(30);
    await showCards();
    expect(screen.getByText("周期内运行", { selector: "dt" })).toBeVisible();
    expect(
      screen.getByText("12", { selector: ".tap-team-card dd" }),
    ).toBeVisible();
    expect(points()).toHaveLength(30);
  });

  it("says so when filters match no Agent", async () => {
    setup([row({})]);
    renderTab();
    const original = vi.mocked(useTeamMembers).getMockImplementation()!;
    vi.mocked(useTeamMembers).mockImplementation(((...args: unknown[]) => {
      const filters = args[2] as { status?: string | null };
      return filters.status
        ? ready({ ...base, data: [] })
        : (original as (...a: unknown[]) => unknown)(...args);
    }) as never);
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "状态" }),
      "idle",
    );
    expect(screen.getByText("没有符合条件的 Agent")).toBeVisible();
  });

  it("annotates syncing numbers but not a legitimately empty value", async () => {
    setup([row({ lastRunAt: null })], "syncing");
    renderTab();
    await showCards();
    expect(
      screen.getByText("运行次数", { selector: "dt" }).nextSibling,
    ).toHaveTextContent("120运行数据同步中");
    const lastRun = screen.getByText("最近运行", {
      selector: ".tap-team-card dt",
    }).nextSibling;
    expect(lastRun).toHaveTextContent(/^—$/);
  });

  it("dashes the per-caller average when run data is unavailable", () => {
    setup([row({})], "unavailable", { servedAvg: null });
    renderTab();
    expect(
      screen.getByText("每位使用者用到的 Agent 数").nextSibling,
    ).toHaveTextContent("—运行数据暂不可用");
  });

  it("captions the card sparkline as the last 30 days", async () => {
    setup([row({})]);
    renderTab();
    await showCards();
    expect(
      screen.getByText("近 30 天趋势", { selector: ".tap-team-card *" }),
    ).toBeVisible();
  });

  it("keeps the filters mounted when a filtered query fails", async () => {
    setup([row({})]);
    renderTab();
    const initial = vi.mocked(useTeamMembers).getMockImplementation()!;
    const refetch = vi.fn();
    vi.mocked(useTeamMembers).mockImplementation(((...args: unknown[]) => {
      const filters = args[2] as { status?: string | null };
      return filters.status
        ? { isPending: false, isError: true, data: undefined, refetch }
        : (initial as (...a: unknown[]) => unknown)(...args);
    }) as never);
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "状态" }),
      "idle",
    );
    expect(screen.getByRole("combobox", { name: "状态" })).toHaveValue("idle");
    expect(screen.getByRole("combobox", { name: "负责人" })).toBeVisible();
    const alert = screen.getByRole("alert");
    await userEvent.click(within(alert).getByRole("button", { name: "重试" }));
    expect(refetch).toHaveBeenCalled();
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "状态" }),
      "",
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reviewer" })).toBeVisible();
  });

  it("guides to creating an Agent when the roster is empty", async () => {
    setup([]);
    const create = vi.fn();
    renderTab("zh", { create });
    expect(screen.getByText("还没有 Agent，新建一个")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "新建 Agent" }));
    expect(create).toHaveBeenCalled();
  });

  it("compares the headline numbers with the previous period", () => {
    setup([row({})]);
    renderTab();
    expect(vi.mocked(useTeamReport)).toHaveBeenCalledWith(
      "p1",
      period,
      "last30",
    );
    const card = (label: string) =>
      screen.getByText(label, { selector: "dt" }).nextSibling as HTMLElement;
    // More runs is good news, more cost is not.
    expect(card("运行次数")).toHaveTextContent("▲+20%");
    expect(card("运行次数").querySelector(".tap-delta")).toHaveAttribute(
      "data-tone",
      "good",
    );
    expect(card("成本")).toHaveTextContent("▲+25%");
    expect(card("成本").querySelector(".tap-delta")).toHaveAttribute(
      "data-tone",
      "bad",
    );
    expect(card("活跃")).toHaveTextContent("▬0%");
  });

  it("shows a dash when the previous period has no baseline", () => {
    setup([row({})]);
    mockReport({ runs: 0 });
    renderTab();
    const runs = screen.getByText("运行次数", { selector: "dt" })
      .nextSibling as HTMLElement;
    expect(runs.querySelector(".tap-delta")).toHaveTextContent(/^—$/);
    expect(runs).toHaveTextContent("无可比基线");
  });

  it("shows no deltas until the report answers for this period", () => {
    setup([row({})]);
    mockReport({}, { isPlaceholderData: true });
    renderTab();
    expect(document.querySelector(".tap-delta")).toBeNull();
  });

  it("ranks Agents by runs and leaves Agents without runs unranked", () => {
    setup([
      row({ agentKey: "a1", name: "Busy", runs: 30 }),
      row({ agentKey: "a2", name: "Tied", runs: 30 }),
      row({ agentKey: "a3", name: "Quiet", runs: 5 }),
      row({ agentKey: "a4", name: "Silent", runs: 0 }),
    ]);
    renderTab();
    const rank = (name: string) =>
      screen
        .getByRole("button", { name })
        .closest("tr")!
        .querySelector(".tap-team-rank")!.textContent;
    expect(rank("Busy")).toBe("排名 1");
    expect(rank("Tied")).toBe("排名 1");
    expect(rank("Quiet")).toBe("排名 3");
    expect(rank("Silent")).toBe("排名 —");
  });
});
