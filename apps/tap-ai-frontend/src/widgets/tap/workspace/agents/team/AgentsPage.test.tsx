import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  useTeamMember,
  useTeamMembers,
  useOrgChart,
  useTeamPeople,
  useTeamReport,
  useTeamSummary,
} from "../../../../../features/knowledge/api/agentTeam";
import { WORKSPACE_COPY } from "../../copy";
import { AgentsPage } from "./AgentsPage";

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
      useTeamMember: vi.fn(),
      useTeamMembers: vi.fn(),
      useTeamPeople: vi.fn(),
      useOrgChart: vi.fn(),
      useTeamReport: vi.fn(),
    };
  },
);

function mockSummary(runtimeStatus: "ok" | "unavailable" = "ok") {
  const base = {
    runtimeStatus,
    computedAt: "2026-10-23T08:00:00Z",
    timezone: "UTC",
    period: { from: "2026-09-24", to: "2026-10-23" },
    granularity: "day",
  };
  vi.mocked(useTeamReport).mockReturnValue({
    isPending: true,
    isError: false,
    refetch: vi.fn(),
    data: undefined,
  } as never);
  vi.mocked(useTeamMembers).mockReturnValue({
    isPending: false,
    isError: false,
    refetch: vi.fn(),
    data: { ...base, data: [] },
  } as never);
  vi.mocked(useOrgChart).mockReturnValue({
    isPending: false,
    isError: false,
    refetch: vi.fn(),
    data: { ...base, data: { people: [], agents: [], edges: [] } },
  } as never);
  vi.mocked(useTeamPeople).mockReturnValue({
    isPending: false,
    isError: false,
    refetch: vi.fn(),
    data: { ...base, data: [] },
  } as never);
  vi.mocked(useTeamSummary).mockReturnValue({
    isPending: false,
    isError: false,
    refetch: vi.fn(),
    data: {
      runtimeStatus,
      computedAt: "2026-10-23T08:00:00Z",
      timezone: "UTC",
      period: { from: "2026-09-24", to: "2026-10-23" },
      granularity: "day",
      data: {
        agentTotal: 0,
        ownersAvg: 0,
        servedAvg: 0,
        maintenanceTotal: 0,
        runs: null,
        active: null,
        idle: null,
        latencyTotalMs: null,
        costUsd: null,
      },
    },
  } as never);
}

const MEMBER = {
  agentKey: "a1",
  name: "Reviewer",
  kind: "custom",
  jobTitle: null,
  ownerActorId: "alice",
  status: "active",
  runs: 3,
  failures: 0,
  abstained: 0,
  canceled: 0,
  latencyTotalMs: 10,
  latencyP50Ms: 5,
  costUsd: "1",
  servedCount: 1,
  topServed: [],
  lastRunAt: null,
  satisfaction: null,
  satisfactionSample: "insufficient",
  lastMaintenanceAt: null,
  joinedAt: "2026-09-01T00:00:00Z",
  pendingFeedback: 0,
  maintenance: {},
  trend: [],
  generationCalls: 1,
  toolCalls: 1,
  callsPerRun: 2,
};

/** Team tab with one roster member whose profile can be opened. */
function mockRosterAndProfile() {
  mockSummary();
  const base = {
    runtimeStatus: "ok",
    computedAt: "2026-10-23T08:00:00Z",
    timezone: "UTC",
    period: { from: "2026-09-24", to: "2026-10-23" },
    granularity: "day",
  };
  vi.mocked(useTeamMembers).mockReturnValue({
    isPending: false,
    isError: false,
    refetch: vi.fn(),
    data: { ...base, data: [MEMBER] },
  } as never);
  vi.mocked(useTeamMember).mockReturnValue({
    isPending: false,
    isError: false,
    refetch: vi.fn(),
    data: {
      ...base,
      data: {
        row: MEMBER,
        currentRevisionId: "rev-7",
        served: [],
        pendingFeedback: [],
      },
    },
  } as never);
}

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("AgentsPage", () => {
  it("renders four tabs with Team selected by default", () => {
    mockSummary();
    render(
      <AgentsPage
        projectId="p1"
        copy={WORKSPACE_COPY.en}
        configTab={<p>config content</p>}
      />,
    );
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "My Agents",
      "Team",
      "Report",
      "Configuration",
    ]);
    expect(screen.getByRole("tab", { name: "Team" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.queryByText("config content")).not.toBeInTheDocument();
  });

  it("shows the config tab content and records the tab in the URL", async () => {
    mockSummary();
    render(
      <AgentsPage
        projectId="p1"
        copy={WORKSPACE_COPY.en}
        configTab={<p>config content</p>}
      />,
    );
    await userEvent.click(screen.getByRole("tab", { name: "Configuration" }));
    expect(screen.getByText("config content")).toBeInTheDocument();
    expect(window.location.search).toContain("agentsTab=config");
  });

  it("restores the tab from the URL", () => {
    mockSummary();
    window.history.replaceState(null, "", "/?agentsTab=report");
    render(
      <AgentsPage
        projectId="p1"
        copy={WORKSPACE_COPY.en}
        configTab={<p>config content</p>}
      />,
    );
    expect(screen.getByRole("tab", { name: "Report" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("renders Chinese copy and unavailable runtime notice", () => {
    mockSummary("unavailable");
    render(
      <AgentsPage
        projectId="p1"
        copy={WORKSPACE_COPY.zh}
        configTab={<p>config content</p>}
      />,
    );
    expect(screen.getByRole("tab", { name: "团队" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: "配置" })).toBeInTheDocument();
    expect(screen.getAllByText("运行数据暂不可用")[0]).toBeInTheDocument();
  });

  it("shows the update time in the server timezone, not the browser's", () => {
    mockSummary();
    const original = process.env.TZ;
    process.env.TZ = "Asia/Tokyo";
    try {
      render(
        <AgentsPage
          projectId="p1"
          copy={WORKSPACE_COPY.en}
          configTab={<p>config content</p>}
        />,
      );
      expect(screen.getByText("Data updated at 08:00 (UTC)")).toBeVisible();
    } finally {
      process.env.TZ = original;
    }
  });

  it("selects the config tab and records it when forced by a deep link", () => {
    mockSummary();
    window.history.replaceState(null, "", "/?agentsTab=team");
    render(
      <AgentsPage
        projectId="p1"
        copy={WORKSPACE_COPY.en}
        initialTab="config"
        configTab={<p>config content</p>}
      />,
    );
    expect(screen.getByRole("tab", { name: "Configuration" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText("config content")).toBeVisible();
    expect(window.location.search).toContain("agentsTab=config");
  });

  it("flags an inverted custom range and skips the query", async () => {
    mockSummary();
    render(
      <AgentsPage
        projectId="p1"
        copy={WORKSPACE_COPY.en}
        configTab={<p>config content</p>}
      />,
    );
    await userEvent.selectOptions(screen.getByLabelText("Period"), "custom");
    vi.mocked(useTeamSummary).mockClear();
    fireEvent.change(screen.getByLabelText("From"), {
      target: { value: "2099-01-01" },
    });
    expect(
      screen.getByText("Start date must be on or before end date"),
    ).toBeVisible();
    expect(useTeamSummary).not.toHaveBeenCalled();
  });

  it("heads each analytics panel like the Configuration tab", async () => {
    mockSummary();
    render(
      <AgentsPage
        projectId="p1"
        copy={WORKSPACE_COPY.en}
        configTab={<p>config content</p>}
      />,
    );
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Agent team",
    );
    await userEvent.click(screen.getByRole("tab", { name: "Report" }));
    expect(
      screen.getByText(WORKSPACE_COPY.en.agentTeam.descriptions.report),
    ).toBeVisible();
  });

  it("returns focus to the card that opened the profile", async () => {
    mockRosterAndProfile();
    render(
      <AgentsPage
        projectId="p1"
        copy={WORKSPACE_COPY.en}
        configTab={<p>config content</p>}
      />,
    );
    const opener = screen.getByRole("button", { name: "Reviewer" });
    await userEvent.click(opener);
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(
      within(dialog).getByRole("button", {
        name: WORKSPACE_COPY.en.agentTeam.profile.close,
      }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("View configuration opens the Configuration tab with the revision id", async () => {
    mockRosterAndProfile();
    const received: (string | null)[] = [];
    render(
      <AgentsPage
        projectId="p1"
        copy={WORKSPACE_COPY.en}
        configTab={(request) => {
          received.push(request.initialDetailRevisionId);
          return <p>config content</p>;
        }}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Reviewer" }));
    await userEvent.click(
      await screen.findByRole("button", {
        name: WORKSPACE_COPY.en.agentTeam.viewConfig,
      }),
    );
    expect(screen.getByRole("tab", { name: "Configuration" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText("config content")).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(received).toContain("rev-7");
    expect(window.location.search).toContain("agentsTab=config");
  });
});
