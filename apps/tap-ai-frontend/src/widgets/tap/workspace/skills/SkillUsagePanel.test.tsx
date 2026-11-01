import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WORKSPACE_COPY } from "../copy";
import { SkillUsagePanel } from "./SkillUsagePanel";

const period = { from: "2026-10-01", to: "2026-10-30" };

function envelope(data: unknown, runtimeStatus = "ok") {
  return {
    runtimeStatus,
    computedAt: "2026-10-30T08:00:00Z",
    timezone: "UTC",
    period,
    granularity: "week",
    data,
  };
}

const USAGE = {
  byMode: { manual: 3, pinned: 4, auto: 5 },
  users: 6,
  satisfaction: 0.8,
  satisfactionSample: "ok",
  lastUsedAt: "2026-10-29T10:15:00Z",
  pinnedByAgents: 2,
  runtime: { runs: 12, failures: 1, latencyTotalMs: 4200, costUsd: "1.25" },
};

function detail(usage: Record<string, unknown> = {}) {
  return {
    skill: { skillId: "skill_1", name: "BDD Scenario Design" },
    usage: { ...USAGE, ...usage },
    revisions: [],
    files: [],
    pinnedBy: ["agent_qa"],
    topAgents: [
      { key: "agent_qa", runs: 7 },
      { key: "tapper_default", runs: 4 },
      { key: "unknown", runs: 1 },
    ],
    topCallers: [
      { key: "alice", runs: 9 },
      { key: "bob", runs: 3 },
    ],
  };
}

const SERIES = {
  byMode: { manual: 3, pinned: 4, auto: 5 },
  points: [
    { bucketStart: "2026-09-28", mode: "manual", runs: 1 },
    { bucketStart: "2026-09-28", mode: "auto", runs: 2 },
    { bucketStart: "2026-10-05", mode: "pinned", runs: 4 },
    { bucketStart: "2026-10-05", mode: "auto", runs: 3 },
    { bucketStart: "2026-10-12", mode: "manual", runs: 2 },
  ],
};

function stubApi(
  opts: {
    usage?: Record<string, unknown>;
    runtimeStatus?: string;
    series?: typeof SERIES;
  } = {},
) {
  const fetchMock = vi.fn((input: string) => {
    const url = String(input);
    const body = url.includes("/skill-hub/catalog/skill_1/usage")
      ? envelope(opts.series ?? SERIES, opts.runtimeStatus)
      : url.includes("/skill-hub/catalog/skill_1")
        ? envelope(detail(opts.usage), opts.runtimeStatus)
        : url.endsWith("/ai/agents")
          ? {
              items: [
                {
                  agentId: "agent_qa",
                  displayName: "QA Reviewer",
                  isLatest: true,
                },
              ],
            }
          : {};
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderPanel(opts: Parameters<typeof stubApi>[0] = {}) {
  const fetchMock = stubApi(opts);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    createElement(
      QueryClientProvider,
      { client },
      createElement(SkillUsagePanel, {
        projectId: "project-1",
        skillId: "skill_1",
        period,
        copy: WORKSPACE_COPY.en,
      }),
    ),
  );
  return fetchMock;
}

function renderPanelWithContainer(opts: Parameters<typeof stubApi>[0] = {}) {
  stubApi(opts);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    createElement(
      QueryClientProvider,
      { client },
      createElement(SkillUsagePanel, {
        projectId: "project-1",
        skillId: "skill_1",
        period,
        copy: WORKSPACE_COPY.en,
      }),
    ),
  );
}

function tile(label: string) {
  const term = screen.getByText(label, { selector: "dt" });
  return term.parentElement as HTMLElement;
}

afterEach(() => vi.unstubAllGlobals());

describe("SkillUsagePanel", () => {
  it("shows usage tiles, the per-mode trend and rankings for the period", async () => {
    const fetchMock = renderPanel();
    expect(
      await screen.findByText("Data updated at 08:00 (UTC)"),
    ).toBeVisible();

    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls).toContain(
      "/api/v1/projects/project-1/ai/skill-hub/catalog/skill_1?from=2026-10-01&to=2026-10-30",
    );
    expect(urls).toContain(
      "/api/v1/projects/project-1/ai/skill-hub/catalog/skill_1/usage?from=2026-10-01&to=2026-10-30",
    );

    expect(within(tile("Manual")).getByText("3")).toBeVisible();
    expect(within(tile("Pinned by an Agent")).getByText("4")).toBeVisible();
    expect(within(tile("Automatic")).getByText("5")).toBeVisible();
    expect(
      screen.getByText(
        "A conversation that uses several skills counts once for each skill, so activations don't add up across skills.",
      ),
    ).toBeVisible();
    expect(within(tile("Users")).getByText("6")).toBeVisible();
    expect(within(tile("Satisfaction")).getByText("80%")).toBeVisible();
    expect(
      within(tile("Last used")).getByText("10/29/2026, 10:15"),
    ).toBeVisible();
    expect(within(tile("Agents pinning it")).getByText("2")).toBeVisible();
    expect(within(tile("Cost")).getByText("$1.25")).toBeVisible();
    expect(within(tile("Total latency")).getByText("4.2 s")).toBeVisible();
    expect(within(tile("Failures")).getByText("1")).toBeVisible();

    expect(
      await screen.findByRole("group", {
        name: "Manual, Pinned by an Agent, Automatic",
      }),
    ).toBeVisible();

    const agents = screen.getByRole("list", { name: "Agents using it" });
    const agentRows = within(agents).getAllByRole("listitem");
    expect(agentRows.map((row) => row.textContent)).toEqual([
      "QA Reviewer7 runs",
      "Tapper default4 runs",
      "Removed Agent1 run",
    ]);
    const callers = screen.getByRole("list", { name: "People using it" });
    expect(within(callers).getAllByRole("listitem")[0]).toHaveTextContent(
      "alice9 runs",
    );
  });

  it("shows 'Insufficient sample' and 'Run data unavailable' when the data is not there", async () => {
    renderPanel({
      usage: {
        satisfaction: null,
        satisfactionSample: "insufficient",
        runtime: null,
      },
      runtimeStatus: "unavailable",
    });
    expect(await screen.findByText("Insufficient sample")).toBeVisible();
    expect(screen.getByText("Run data unavailable")).toBeVisible();
    expect(screen.queryByText("Cost", { selector: "dt" })).toBeNull();
    // Freshness badge comes from the detail response's runtime status.
    expect(
      screen.getByText("Run data is temporarily unavailable"),
    ).toBeVisible();
  });
  it("draws every week of the period, with empty weeks at zero and the first week clamped to the period", async () => {
    // 2026-10-01 is a Thursday: the first ISO week starts 2026-09-28. The
    // server omits the empty week of 2026-10-05.
    const { container } = renderPanelWithContainer({
      series: {
        byMode: { manual: 3, pinned: 0, auto: 0 },
        points: [
          { bucketStart: "2026-09-28", mode: "manual", runs: 1 },
          { bucketStart: "2026-10-12", mode: "manual", runs: 2 },
        ],
      },
    });
    await screen.findByRole("group", {
      name: "Manual, Pinned by an Agent, Automatic",
    });
    const hits = within(container).getAllByTestId("trend-hit");
    // Weeks of 09-28, 10-05, 10-12, 10-19 and 10-26.
    expect(hits).toHaveLength(5);

    fireEvent.mouseEnter(hits[0]!);
    expect(
      screen.getByText("2026-10-01 – 2026-10-04 · Manual: 1 activations"),
    ).toBeInTheDocument();

    fireEvent.mouseEnter(hits[1]!);
    expect(
      screen.getByText("2026-10-05 – 2026-10-11 · Manual: 0 activations"),
    ).toBeInTheDocument();
  });
});
