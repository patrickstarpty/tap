import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useTeamSummary } from "../../../../../features/knowledge/api/agentTeam";
import { WORKSPACE_COPY } from "../../copy";
import { AgentsPage } from "./AgentsPage";

vi.mock(
  "../../../../../features/knowledge/api/agentTeam",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("../../../../../features/knowledge/api/agentTeam")
      >();
    return { ...actual, useTeamSummary: vi.fn() };
  },
);

function mockSummary(runtimeStatus: "ok" | "unavailable" = "ok") {
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
      data: {},
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
    expect(screen.getByText("运行数据暂不可用")).toBeInTheDocument();
  });
});
