import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useOrgChart,
  useTeamMembers,
  useTeamPeople,
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

function setup(
  members: unknown[],
  runtimeStatus: "ok" | "unavailable" = "ok",
  summary: Record<string, unknown> = {},
) {
  const b = { ...base, runtimeStatus };
  vi.mocked(useTeamSummary).mockReturnValue(
    ready({
      ...b,
      data: {
        agentTotal: 4,
        active: 2,
        idle: 1,
        ownersAvg: 1.5,
        servedAvg: 2,
        runs: 120,
        latencyTotalMs: 3_600_000,
        costUsd: "12.5",
        maintenanceTotal: 7,
        ...summary,
      },
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
      onOpenMember={handlers.open ?? vi.fn()}
      onCreateAgent={handlers.create ?? vi.fn()}
    />,
  );
}

beforeEach(() => vi.clearAllMocks());

describe("TeamTab", () => {
  it("shows the summary numbers and freshness line", () => {
    setup([row({})]);
    renderTab();
    expect(screen.getByText("运行次数").nextSibling).toHaveTextContent("120");
    expect(screen.getByText("Agent 数").nextSibling).toHaveTextContent("4");
    expect(screen.getByText("人均负责 Agent").nextSibling).toHaveTextContent(
      "1.5",
    );
    expect(screen.getByText("维护动作").nextSibling).toHaveTextContent("7");
    expect(screen.getByText("数据更新于 08:00（UTC）")).toBeVisible();
  });

  it("renders a member card with every field", () => {
    setup([row({})]);
    renderTab();
    expect(
      screen.getByText("R", { selector: ".tap-team-avatar" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Reviewer" })).toBeVisible();
    expect(screen.getByText("Test reviewer")).toBeVisible();
    expect(screen.getByText("alice", { selector: "dd" })).toBeVisible();
    expect(screen.getByText("活跃", { selector: "dd" })).toBeVisible();
    expect(screen.getByRole("img", { name: /Reviewer/ })).toBeVisible();
    expect(screen.getByText("80%")).toBeVisible();
    expect(screen.getByText("u1, u2, u3")).toBeVisible();
    expect(screen.getByText("2026/10/22 10:30")).toBeVisible();
    expect(screen.getByText("2026/10/20 09:00")).toBeVisible();
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
    expect(screen.getByText("Tapper 内置", { selector: "dd" })).toBeVisible();
    expect(screen.getByText("样本不足")).toBeVisible();
  });

  it("opens a member from its card", async () => {
    setup([row({})]);
    const open = vi.fn();
    renderTab("zh", { open });
    await userEvent.click(screen.getByRole("button", { name: "Reviewer" }));
    expect(open).toHaveBeenCalledWith("a1");
  });

  it("passes owner, status and kind filters to the request", async () => {
    setup([
      row({}),
      row({ agentKey: "a2", name: "Other", ownerActorId: "bob" }),
    ]);
    renderTab();
    await userEvent.selectOptions(screen.getByLabelText("负责人"), "bob");
    await userEvent.selectOptions(screen.getByLabelText("状态"), "idle");
    await userEvent.selectOptions(screen.getByLabelText("类型"), "built_in");
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
    await userEvent.selectOptions(screen.getByLabelText("负责人"), "alice");
    expect(screen.getByRole("button", { name: "Other" })).toBeTruthy();
  });

  it("switches to the table and sorts through request parameters", async () => {
    setup([row({})]);
    renderTab("en");
    const runsHeader = () =>
      screen.getAllByText("Runs", { selector: ".ant-table-column-title" })[0];
    await userEvent.click(screen.getByRole("button", { name: "Table" }));
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
    await userEvent.click(
      screen.getAllByText("Total latency", {
        selector: ".ant-table-column-title",
      })[0],
    );
    expect(periodCalls().at(-1)![2]).toMatchObject({
      sort: "latency_total_ms",
    });
  });

  it("sorts the people table by column", async () => {
    setup([row({})]);
    renderTab("en");
    const table = screen.getByText("Person").closest("table")!;
    const names = () =>
      within(table)
        .getAllByRole("row")
        .slice(1)
        .map((r) => r.firstElementChild!.textContent);
    expect(names()).toEqual(["bob", "alice"]);
    await userEvent.click(
      screen.getByText("Agents owned", { selector: ".ant-table-column-title" }),
    );
    await userEvent.click(
      screen.getByText("Agents owned", { selector: ".ant-table-column-title" }),
    );
    expect(names()).toEqual(["alice", "bob"]);
    await userEvent.click(
      screen.getByText("Person", { selector: ".ant-table-column-title" }),
    );
    expect(names()).toEqual(["alice", "bob"]);
  });

  it("keeps the roster and marks run data unavailable instead of zero", () => {
    setup(
      [
        row({
          runs: null,
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
  });

  it("keeps the filter focused while the filtered query is pending", async () => {
    setup([row({})]);
    renderTab();
    const select = screen.getByLabelText("负责人");
    const placeholder = vi.mocked(useTeamMembers).getMockImplementation()!;
    vi.mocked(useTeamMembers).mockImplementation(((...args: unknown[]) => {
      const result = (placeholder as (...a: unknown[]) => object)(...args);
      return args[1] === period
        ? { ...result, isPlaceholderData: true, isPending: false }
        : result;
    }) as never);
    select.focus();
    await userEvent.selectOptions(select, "alice");
    expect(screen.getByLabelText("负责人")).toBe(select);
    expect(select).toHaveFocus();
    expect(screen.queryByText("正在加载团队数据…")).not.toBeInTheDocument();
  });

  it("draws the card sparkline from the last-30-days query", () => {
    setup([row({})]);
    renderTab();
    const calls = vi.mocked(useTeamMembers).mock.calls;
    expect(calls.some((call) => call[1] !== period)).toBe(true);
    const spark = calls.find((call) => call[1] !== period)!;
    expect(spark[2]).toEqual({});
    expect(screen.getByText("周期内运行", { selector: "dt" })).toBeVisible();
    expect(
      screen.getByText("12", { selector: ".tap-team-card dd" }),
    ).toBeVisible();
    const polyline = screen
      .getByRole("img", { name: /Reviewer.*近 30 天/ })
      .querySelector("polyline")!;
    expect(polyline.getAttribute("points")!.split(" ")).toHaveLength(3);
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
    await userEvent.selectOptions(screen.getByLabelText("状态"), "idle");
    expect(screen.getByText("没有符合条件的 Agent")).toBeVisible();
  });

  it("guides to creating an Agent when the roster is empty", async () => {
    setup([]);
    const create = vi.fn();
    renderTab("zh", { create });
    expect(screen.getByText("还没有 Agent，新建一个")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "新建 Agent" }));
    expect(create).toHaveBeenCalled();
  });
});
