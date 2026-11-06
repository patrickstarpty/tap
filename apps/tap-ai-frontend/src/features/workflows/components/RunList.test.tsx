import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { useState } from "react";
import { useWorkflowRuns } from "../api/queries";
import { RunList } from "./RunList";

vi.mock("../api/queries", () => ({ useWorkflowRuns: vi.fn() }));
const task = {
  taskId: "task-1",
  conversationId: "chat-1",
  goalSummary: "Review login",
  displayName: "Tapper",
  createdAt: "2026-11-06T00:00:00Z",
  status: "running",
  matchingRuns: [
    {
      nodeId: "node-1",
      agentId: "sdet",
      agentRevisionId: "rev-1",
      attempt: 1,
      createdAt: "2026-11-06T00:00:00Z",
      status: "running",
    },
    {
      nodeId: "node-2",
      agentId: "sdet",
      agentRevisionId: "rev-1",
      attempt: 2,
      createdAt: "2026-11-06T00:01:00Z",
      status: "failed",
    },
  ],
};

it("shows each parallel profile run and opens the selected node", async () => {
  vi.mocked(useWorkflowRuns).mockReturnValue({
    data: { pages: [{ items: [task], nextCursor: null }] },
    isPending: false,
    isError: false,
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: vi.fn(),
  } as never);
  const open = vi.fn();
  render(<RunList projectId="project-1" agentId="sdet" onOpen={open} />);
  expect(screen.getAllByRole("listitem")).toHaveLength(2);
  await userEvent.click(screen.getByRole("button", { name: /Attempt 2/u }));
  expect(open).toHaveBeenCalledWith("task-1", "chat-1", "node-2");
});

it("continues pagination until an older status match is visible", async () => {
  const older = {
    ...task,
    taskId: "task-2",
    matchingRuns: [{ ...task.matchingRuns[1], nodeId: "node-old" }],
  };
  vi.mocked(useWorkflowRuns).mockImplementation(() => {
    const [loaded, setLoaded] = useState(false);
    return {
      data: {
        pages: [
          {
            items: [{ ...task, matchingRuns: [task.matchingRuns[0]] }],
            nextCursor: "next",
          },
          ...(loaded ? [{ items: [older], nextCursor: null }] : []),
        ],
      },
      isPending: false,
      isError: false,
      hasNextPage: !loaded,
      isFetchingNextPage: false,
      fetchNextPage: () => {
        setLoaded(true);
        return Promise.resolve();
      },
    } as never;
  });
  render(<RunList projectId="project-1" agentId="sdet" onOpen={vi.fn()} />);
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Show" }),
    "failed",
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: /node-old|Attempt 2/u }),
    ).toBeVisible(),
  );
});

it("distinguishes goal-empty tasks and exposes participants, activity and pending work", () => {
  vi.mocked(useWorkflowRuns).mockReturnValue({
    data: {
      pages: [
        {
          items: [
            {
              ...task,
              goalSummary: "",
              taskId: "task-alpha-123",
              recentEvent: {
                kind: "node.waiting",
                occurredAt: "2026-11-06T01:00:00Z",
              },
              status: "awaiting_input",
            },
            {
              ...task,
              goalSummary: "",
              taskId: "task-beta-456",
              recentEvent: null,
              matchingRuns: [],
              status: "running",
            },
          ],
          nextCursor: null,
        },
      ],
    },
    isPending: false,
    isError: false,
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: vi.fn(),
  } as never);
  render(<RunList projectId="project-1" onOpen={vi.fn()} />);
  expect(
    screen.getByRole("button", { name: /task-alpha-123/u }),
  ).toHaveTextContent("sdet");
  expect(
    screen.getByRole("button", { name: /task-alpha-123/u }),
  ).toHaveTextContent("Latest: node waiting");
  expect(
    screen
      .getByRole("button", { name: /task-alpha-123/u })
      .querySelector("time")?.dateTime,
  ).toBe("2026-11-06T01:00:00Z");
  expect(
    screen.getByRole("button", { name: /task-alpha-123/u }),
  ).toHaveTextContent("Input needed");
  expect(screen.getByRole("button", { name: /task-beta-456/u })).toBeVisible();
});
