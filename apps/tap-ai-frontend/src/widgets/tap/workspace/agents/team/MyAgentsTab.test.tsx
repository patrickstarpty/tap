import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  usePerson,
  useTeamMembers,
} from "../../../../../features/knowledge/api/agentTeam";
import { WORKSPACE_COPY } from "../../copy";
import { MyAgentsTab } from "./MyAgentsTab";

vi.mock(
  "../../../../../features/knowledge/api/agentTeam",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("../../../../../features/knowledge/api/agentTeam")
      >();
    return { ...actual, usePerson: vi.fn(), useTeamMembers: vi.fn() };
  },
);

const period = { from: "2026-09-24", to: "2026-10-23" };

function row(agentKey: string, name: string) {
  return {
    agentKey,
    name,
    kind: "custom",
    jobTitle: null,
    ownerActorId: "me",
    status: "active",
    runs: 4,
    failures: 0,
    abstained: 0,
    canceled: 0,
    latencyTotalMs: 1000,
    latencyP50Ms: 100,
    costUsd: "1",
    satisfaction: 0.9,
    satisfactionSample: "ok",
    servedCount: 1,
    topServed: [],
    lastRunAt: null,
    lastMaintenanceAt: null,
    joinedAt: "2026-09-01T00:00:00Z",
    pendingFeedback: 1,
    maintenance: {},
    trend: [],
  };
}

function setup(data: Record<string, unknown>, runtimeStatus = "ok") {
  vi.mocked(usePerson).mockReturnValue({
    isPending: false,
    isError: false,
    refetch: vi.fn(),
    data: {
      runtimeStatus,
      computedAt: "2026-10-23T08:00:00Z",
      timezone: "UTC",
      period,
      granularity: "day",
      data: { person: {}, owned: [], served: [], pendingFeedback: [], ...data },
    },
  } as never);
  vi.mocked(useTeamMembers).mockReturnValue({ data: undefined } as never);
}

describe("MyAgentsTab", () => {
  beforeEach(() => vi.clearAllMocks());

  it("requests me, pins feedback on top, renders both groups", async () => {
    const onOpen = vi.fn();
    setup({
      owned: [row("a1", "Reviewer")],
      served: [row("b1", "Planner")],
      pendingFeedback: [
        {
          feedbackId: "f1",
          agentKey: "a1",
          actorId: "u1",
          rating: "down",
          comment: "Too slow",
          conversationId: "c",
          turnId: "t",
          updatedAt: "2026-10-22T10:30:00Z",
        },
      ],
    });
    render(
      <MyAgentsTab
        projectId="p1"
        copy={WORKSPACE_COPY.en}
        period={period}
        onOpenMember={onOpen}
      />,
    );
    expect(usePerson).toHaveBeenCalledWith("p1", "me", period);
    const headings = screen
      .getAllByRole("heading", { level: 3 })
      .map((h) => h.textContent);
    expect(headings).toEqual([
      "Feedback waiting for me",
      "Agents I own",
      "Agents that serve me",
    ]);
    expect(screen.getByText("Too slow")).toBeTruthy();
    expect(screen.queryByText(/down/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Not helpful/ }));
    // Lands on the profile's pending-feedback section.
    expect(onOpen).toHaveBeenCalledWith("a1", "feedback");
    expect(screen.getByText("Planner")).toBeTruthy();
  });

  it("shows the Tapper default member as Tapper in cards and feedback", () => {
    const tapper = {
      ...row("tapper_default", "Tapper 默认"),
      kind: "tapper_default",
      ownerActorId: null,
    };
    setup({
      served: [tapper],
      pendingFeedback: [
        {
          feedbackId: "f1",
          agentKey: "tapper_default",
          actorId: "u1",
          rating: "down",
          comment: "Too slow",
          conversationId: "c",
          turnId: "t",
          updatedAt: "2026-10-22T10:30:00Z",
        },
      ],
    });
    const { unmount } = render(
      <MyAgentsTab
        projectId="p1"
        copy={WORKSPACE_COPY.en}
        period={period}
        onOpenMember={vi.fn()}
      />,
    );
    expect(screen.queryByText("Tapper 默认")).toBeNull();
    expect(screen.getAllByText("Tapper").length).toBeGreaterThanOrEqual(2);
    unmount();
    render(
      <MyAgentsTab
        projectId="p1"
        copy={WORKSPACE_COPY.zh}
        period={period}
        onOpenMember={vi.fn()}
      />,
    );
    expect(screen.getAllByText("Tapper 默认").length).toBeGreaterThanOrEqual(2);
  });

  it("shows an empty state per block", () => {
    setup({});
    render(
      <MyAgentsTab
        projectId="p1"
        copy={WORKSPACE_COPY.zh}
        period={period}
        onOpenMember={vi.fn()}
      />,
    );
    expect(screen.getByText("暂无待你处理的反馈")).toBeTruthy();
    expect(screen.getByText("你还没有负责的 Agent")).toBeTruthy();
    expect(screen.getByText("所选周期内没有 Agent 为你服务")).toBeTruthy();
  });

  it("marks served Agents unavailable instead of nobody during an outage", () => {
    setup({}, "unavailable");
    render(
      <MyAgentsTab
        projectId="p1"
        copy={WORKSPACE_COPY.zh}
        period={period}
        onOpenMember={vi.fn()}
      />,
    );
    expect(screen.queryByText("所选周期内没有 Agent 为你服务")).toBeNull();
    const served = screen
      .getByRole("heading", { name: "为我服务的 Agent" })
      .closest("section")!;
    expect(served).toHaveTextContent("运行数据暂不可用");
  });

  it("shows English empty states", () => {
    setup({});
    render(
      <MyAgentsTab
        projectId="p1"
        copy={WORKSPACE_COPY.en}
        period={period}
        onOpenMember={vi.fn()}
      />,
    );
    expect(screen.getByText("No feedback waiting for you")).toBeTruthy();
    expect(screen.getByText("You do not own any Agents yet")).toBeTruthy();
    expect(
      screen.getByText("No Agent has served you in this period"),
    ).toBeTruthy();
  });

  it("labels comment-only feedback and omits a missing comment", () => {
    setup({
      owned: [row("a1", "Reviewer")],
      pendingFeedback: [
        {
          feedbackId: "f1",
          agentKey: "a1",
          actorId: "u1",
          rating: null,
          comment: "Please fix",
          conversationId: "c",
          turnId: "t",
          updatedAt: "2026-10-22T10:30:00Z",
        },
        {
          feedbackId: "f2",
          agentKey: "a1",
          actorId: "u1",
          rating: "up",
          comment: null,
          conversationId: "c",
          turnId: "t2",
          updatedAt: "2026-10-22T11:30:00Z",
        },
      ],
    });
    render(
      <MyAgentsTab
        projectId="p1"
        copy={WORKSPACE_COPY.en}
        period={period}
        onOpenMember={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /Comment only/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Helpful/ })).toBeTruthy();
    expect(screen.getByText("Please fix")).toBeTruthy();
    expect(screen.getAllByRole("listitem").length).toBeGreaterThanOrEqual(2);
  });
});
