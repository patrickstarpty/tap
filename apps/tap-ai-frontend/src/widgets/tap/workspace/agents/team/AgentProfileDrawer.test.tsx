import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WORKSPACE_COPY, type WorkspaceCopy } from "../../copy";
import { AgentProfileDrawer } from "./AgentProfileDrawer";

const period = { from: "2026-09-24", to: "2026-10-23" };
const copy = WORKSPACE_COPY.zh;

function member(over: Record<string, unknown> = {}) {
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
    latencyTotalMs: 90000,
    latencyP50Ms: 800,
    costUsd: "1.5",
    satisfaction: 0.8,
    satisfactionSample: "ok",
    servedCount: 1,
    topServed: [{ actorId: "bob", runs: 5 }],
    lastRunAt: null,
    lastMaintenanceAt: null,
    joinedAt: "2026-01-02T00:00:00Z",
    maintenance: {},
    pendingFeedback: 1,
    generationCalls: 30,
    toolCalls: null,
    callsPerRun: null,
    trend: [
      {
        agentKey: "a1",
        bucketStart: "2026-10-01",
        runs: 3,
        failures: 1,
        costUsd: "0",
        latencyTotalMs: 0,
      },
    ],
    ...over,
  };
}

function event(id: string, kind: string, revisionId: string | null = null) {
  return {
    eventId: id,
    agentId: "a1",
    actorId: "alice",
    kind,
    revisionId,
    feedbackId: null,
    note: null,
    occurredAt: "2026-10-01T00:00:00Z",
  };
}

interface Server {
  row: Record<string, unknown>;
  pending: Array<Record<string, unknown>>;
  history: Array<Record<string, unknown>>;
  calls: string[];
  failTransfer: boolean;
  runtimeStatus?: string;
  /** Holds feedback-resolve responses until released. */
  hold?: Promise<void>;
}

function install(server: Server) {
  const envelope = {
    runtimeStatus: server.runtimeStatus ?? "ok",
    computedAt: "2026-10-23T08:00:00Z",
    timezone: "UTC",
    period,
    granularity: "day",
  };
  const json = (body: unknown, status = 200) =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    );
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      server.calls.push(`${method} ${url}`);
      if (method === "POST" && url.includes("/feedback/")) {
        const id = url.split("/feedback/")[1].split("/")[0];
        return (server.hold ?? Promise.resolve()).then(() => {
          server.pending = server.pending.filter((f) => f.feedbackId !== id);
          server.row.pendingFeedback = server.pending.length;
          server.history = [
            event("h-new", "feedback_resolved"),
            ...server.history,
          ];
          return json({});
        });
      }
      if (method === "POST" && url.endsWith("/owner")) {
        return server.failTransfer
          ? json({ code: "actor_not_found", detail: "x" }, 400)
          : json({});
      }
      if (method === "PUT") return json({});
      if (url.includes("/history")) {
        const cursor = new URL(url, "http://x").searchParams.get("cursor");
        if (server.history.length > 1 && cursor === null)
          return json({ events: server.history.slice(0, 1), nextCursor: "c2" });
        if (cursor === "c2")
          return json({ events: server.history.slice(1), nextCursor: null });
        return json({ events: server.history, nextCursor: null });
      }
      if (url.includes("/agent-team/members/"))
        return json({
          ...envelope,
          data: {
            row: server.row,
            currentRevisionId:
              server.row.kind === "tapper_default" ? null : "rev-2",
            served: server.row.topServed,
            pendingFeedback: server.pending,
          },
        });
      return json({}, 404);
    }),
  );
}

function renderDrawer(
  server: Server,
  agentKey = "a1",
  initialTab?: "feedback",
  drawerCopy: WorkspaceCopy = copy,
) {
  install(server);
  const onViewConfig = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <AgentProfileDrawer
        projectId="p1"
        agentKey={agentKey}
        period={period}
        copy={drawerCopy}
        onClose={vi.fn()}
        onViewConfig={onViewConfig}
        initialTab={initialTab}
      />
    </QueryClientProvider>,
  );
  return { onViewConfig };
}

function newServer(over: Partial<Server> = {}): Server {
  return {
    row: member(),
    pending: [
      {
        feedbackId: "f1",
        agentKey: "a1",
        actorId: "bob",
        rating: "down",
        comment: "wrong answer",
        conversationId: "c",
        turnId: "t",
        updatedAt: "2026-10-02T00:00:00Z",
      },
    ],
    history: [event("h1", "revised", "rev-1"), event("h0", "enabled")],
    calls: [],
    failTransfer: false,
    ...over,
  };
}

const tab = (name: string) => screen.findByRole("tab", { name });

describe("AgentProfileDrawer", () => {
  beforeEach(() => vi.unstubAllGlobals());
  afterEach(() => vi.unstubAllGlobals());

  it("switches between the five sections", async () => {
    const user = userEvent.setup();
    renderDrawer(newServer());
    await user.click(await tab("基本信息"));
    expect(await screen.findByText("Test reviewer")).toBeTruthy();
    // Each person served: name, then their run count.
    expect(
      screen.getByText("bob", { selector: ".tap-profile-served-name" })
        .nextSibling?.nextSibling,
    ).toHaveTextContent("5");
    await user.click(await tab("工作量"));
    expect(await screen.findByText("模型调用")).toBeTruthy();
    expect(screen.getByText("平均每次运行调用数")).toBeTruthy();
    await user.click(await tab("履历"));
    expect(await screen.findByText("修订")).toBeTruthy();
    await user.click(await tab("待处理反馈"));
    expect(await screen.findByText("wrong answer")).toBeTruthy();
    await user.click(await tab("设置"));
    expect(await screen.findByLabelText("岗位")).toBeTruthy();
  });

  it("loads the next history page", async () => {
    const user = userEvent.setup();
    const server = newServer();
    renderDrawer(server);
    await user.click(await tab("履历"));
    expect(await screen.findByText("修订")).toBeTruthy();
    expect(screen.queryByText("启用")).toBeNull();
    await user.click(screen.getByRole("button", { name: "加载更多" }));
    expect(await screen.findByText("启用")).toBeTruthy();
    expect(server.calls.some((c) => c.includes("cursor=c2"))).toBe(true);
  });

  it("labels a pending rating with the localized text", async () => {
    const user = userEvent.setup();
    renderDrawer(newServer());
    await user.click(await tab("待处理反馈"));
    expect(await screen.findByText(/没帮助/)).toBeTruthy();
    expect(screen.queryByText(/ · down/)).toBeNull();
  });

  it("resolves feedback and shows the new history entry", async () => {
    const user = userEvent.setup();
    renderDrawer(newServer({ history: [event("h1", "revised")] }));
    await user.click(await tab("待处理反馈"));
    await user.click(await screen.findByRole("button", { name: "标记已处理" }));
    await waitFor(() => expect(screen.queryByText("wrong answer")).toBeNull());
    await user.click(await tab("履历"));
    expect(await screen.findByText("处理差评")).toBeTruthy();
  });

  it("disables saving a job title over 64 characters", async () => {
    const user = userEvent.setup();
    renderDrawer(newServer());
    await user.click(await tab("设置"));
    const input = await screen.findByLabelText("岗位");
    await user.clear(input);
    await user.click(input);
    await user.paste("x".repeat(65));
    expect(screen.getByRole("button", { name: "保存" })).toHaveProperty(
      "disabled",
      true,
    );
    await user.clear(input);
    await user.paste("x".repeat(64));
    expect(screen.getByRole("button", { name: "保存" })).toHaveProperty(
      "disabled",
      false,
    );
  });

  it("shows the error text when the new owner is unknown", async () => {
    const user = userEvent.setup();
    renderDrawer(newServer({ failTransfer: true }));
    await user.click(await tab("设置"));
    await user.type(await screen.findByLabelText("新负责人"), "ghost");
    await user.click(screen.getByRole("button", { name: "转交负责人" }));
    expect(await screen.findByText("在本项目中找不到该人员。")).toBeTruthy();
  });

  it("hides settings for built-in Agents and tapper_default has no history", async () => {
    const user = userEvent.setup();
    const server = newServer({
      row: member({
        agentKey: "tapper_default",
        kind: "tapper_default",
        ownerActorId: null,
      }),
    });
    renderDrawer(server, "tapper_default");
    await tab("基本信息");
    expect(screen.queryByRole("tab", { name: "设置" })).toBeNull();
    await user.click(screen.getByRole("tab", { name: "履历" }));
    expect(await screen.findByText("暂无维护履历。")).toBeTruthy();
    expect(server.calls.some((c) => c.includes("/history"))).toBe(false);
  });

  it("names the Tapper default member by language in the header and dialog", async () => {
    const server = () =>
      newServer({
        row: member({
          agentKey: "tapper_default",
          name: "Tapper 默认",
          kind: "tapper_default",
          ownerActorId: null,
        }),
      });
    const en = renderDrawer(
      server(),
      "tapper_default",
      undefined,
      WORKSPACE_COPY.en,
    );
    expect(
      await screen.findByRole("heading", { level: 2, name: "Tapper" }),
    ).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "Tapper" })).toBeTruthy();
    expect(en.onViewConfig).not.toHaveBeenCalled();
    cleanup();
    renderDrawer(server(), "tapper_default");
    expect(
      await screen.findByRole("heading", { level: 2, name: "Tapper 默认" }),
    ).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "Tapper 默认" })).toBeTruthy();
  });

  it("resolves pending feedback of tapper_default", async () => {
    const user = userEvent.setup();
    const server = newServer({
      row: member({
        agentKey: "tapper_default",
        kind: "tapper_default",
        ownerActorId: null,
      }),
    });
    server.pending[0].agentKey = "tapper_default";
    renderDrawer(server, "tapper_default");
    await user.click(await tab("待处理反馈"));
    await user.click(await screen.findByRole("button", { name: "标记已处理" }));
    await waitFor(() => expect(screen.queryByText("wrong answer")).toBeNull());
    expect(
      server.calls.some(
        (c) => c.startsWith("POST") && c.includes("/feedback/f1/resolve"),
      ),
    ).toBe(true);
  });

  it("view configuration passes the current revision id", async () => {
    const user = userEvent.setup();
    const { onViewConfig } = renderDrawer(newServer());
    await user.click(await screen.findByRole("button", { name: "查看配置" }));
    expect(onViewConfig).toHaveBeenCalledWith("rev-2");
  });

  it("opens the revision named by a history event", async () => {
    const user = userEvent.setup();
    const { onViewConfig } = renderDrawer(newServer());
    await user.click(await tab("履历"));
    const item = (await screen.findByText("修订")).closest("li") as HTMLElement;
    await user.click(within(item).getByRole("button", { name: "打开修订" }));
    expect(onViewConfig).toHaveBeenCalledWith("rev-1");
  });

  it("marks unavailable run data with a dash and the unavailable marker", async () => {
    const user = userEvent.setup();
    renderDrawer(
      newServer({
        runtimeStatus: "unavailable",
        row: member({
          runs: null,
          failures: null,
          generationCalls: null,
          toolCalls: null,
          callsPerRun: null,
          trend: [],
        }),
      }),
    );
    await user.click(await tab("工作量"));
    await screen.findByText("工具调用");
    expect(screen.getAllByText(/—/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("运行数据暂不可用").length).toBeGreaterThan(1);
  });

  it("shows the unavailable notice instead of a flat-zero chart or no one served", async () => {
    const user = userEvent.setup();
    renderDrawer(
      newServer({
        runtimeStatus: "unavailable",
        row: member({ runs: null, failures: null, trend: [], topServed: [] }),
      }),
    );
    await user.click(await tab("基本信息"));
    expect(screen.queryByText("本周期无服务对象")).toBeNull();
    expect(screen.getByText("服务对象").nextSibling).toHaveTextContent(
      "—运行数据暂不可用",
    );
    await user.click(await tab("工作量"));
    await screen.findByText("工具调用");
    expect(screen.queryByRole("group", { name: /运行次数/ })).toBeNull();
    expect(screen.queryByTestId("trend-hit")).toBeNull();
  });

  it("opens on the tab it was asked for", async () => {
    renderDrawer(newServer(), "a1", "feedback");
    expect(await tab("待处理反馈")).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("wrong answer")).toBeTruthy();
  });

  it("dates each pending feedback item", async () => {
    renderDrawer(newServer(), "a1", "feedback");
    const item = (await screen.findByText("wrong answer")).closest("li")!;
    expect(within(item).getByText("2026/10/02 00:00")).toBeTruthy();
  });

  it("confirms a saved job title until the next edit", async () => {
    const user = userEvent.setup();
    renderDrawer(newServer());
    await user.click(await tab("设置"));
    const input = await screen.findByLabelText("岗位");
    await user.clear(input);
    await user.type(input, "Lead");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByText("已保存")).toBeTruthy();
    await user.type(input, "!");
    expect(screen.queryByText("已保存")).toBeNull();
  });

  it("shows the current owner and confirms a transfer", async () => {
    const user = userEvent.setup();
    renderDrawer(newServer());
    await user.click(await tab("设置"));
    expect(await screen.findByText("当前负责人")).toBeTruthy();
    expect(screen.getByText("当前负责人").nextSibling).toHaveTextContent(
      "alice",
    );
    const input = screen.getByLabelText("新负责人");
    await user.type(input, "carol");
    await user.click(screen.getByRole("button", { name: "转交负责人" }));
    expect(await screen.findByText("已转交给 carol")).toBeTruthy();
    await user.type(input, "x");
    expect(screen.queryByText("已转交给 carol")).toBeNull();
  });

  it("moves between tabs with the arrow, Home and End keys", async () => {
    const user = userEvent.setup();
    renderDrawer(newServer());
    const basic = await tab("基本信息");
    expect(basic).toHaveAttribute("tabindex", "0");
    expect((await tab("工作量")).getAttribute("tabindex")).toBe("-1");
    basic.focus();
    await user.keyboard("{ArrowRight}");
    const workload = await tab("工作量");
    expect(workload).toHaveFocus();
    expect(workload).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{End}");
    expect(await tab("设置")).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(await tab("基本信息")).toHaveFocus();
    await user.keyboard("{ArrowLeft}");
    expect(await tab("设置")).toHaveFocus();
    await user.keyboard("{Home}");
    expect(await tab("基本信息")).toHaveFocus();
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveAccessibleName("基本信息");
    expect(basic.getAttribute("aria-controls")).toBe(panel.id);
    expect(workload.hasAttribute("aria-controls")).toBe(false);
  });

  it("shows the status in Basics, not in Workload", async () => {
    const user = userEvent.setup();
    renderDrawer(newServer());
    await user.click(await tab("基本信息"));
    expect(await screen.findByText("状态")).toBeTruthy();
    await user.click(await tab("工作量"));
    await screen.findByText("工具调用");
    expect(screen.queryByText("状态")).toBeNull();
  });

  it("hides the settings tab for built-in Agents", async () => {
    renderDrawer(newServer({ row: member({ kind: "built_in" }) }));
    await tab("基本信息");
    expect(screen.queryByRole("tab", { name: "设置" })).toBeNull();
  });

  it("disables only the feedback item being resolved", async () => {
    const user = userEvent.setup();
    let release!: () => void;
    const hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    const second = {
      feedbackId: "f2",
      agentKey: "a1",
      actorId: "carol",
      rating: "down",
      comment: "also wrong",
      conversationId: "c2",
      turnId: "t2",
      updatedAt: "2026-10-03T00:00:00Z",
    };
    const server = newServer({ hold });
    server.pending.push(second);
    renderDrawer(server);
    await user.click(await tab("待处理反馈"));
    const buttons = await screen.findAllByRole("button", {
      name: "标记已处理",
    });
    expect(buttons).toHaveLength(2);
    await user.click(buttons[0]);
    await waitFor(() => expect(buttons[0]).toBeDisabled());
    expect(buttons[1]).toBeEnabled();
    release();
    await waitFor(() => expect(screen.queryByText("wrong answer")).toBeNull());
    expect(screen.getByText("also wrong")).toBeTruthy();
  });
});
