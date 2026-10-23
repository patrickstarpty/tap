import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  useTeamMembers,
  useTeamPeople,
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
      useTeamMembers: vi.fn(),
      useTeamPeople: vi.fn(),
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
  vi.mocked(useTeamMembers).mockReturnValue({
    isPending: false,
    isError: false,
    refetch: vi.fn(),
    data: { ...base, data: [] },
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
});
