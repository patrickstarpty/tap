import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { WorkflowTaskSnapshot } from "../api/client";
import { RunDetail } from "./RunDetail";

const snapshot = {
  taskId: "task-1",
  projectId: "project-1",
  conversationId: "chat-1",
  status: "running",
  planRevision: 1,
  lastSequence: 0,
  origin: "native",
  displayName: "Tapper",
  goalSummary: "Review login",
  createdAt: "2026-11-06T00:00:00Z",
  availableActions: ["cancel"],
  nodes: [],
  edges: [],
  events: [],
  commands: [],
  artifacts: [],
  budgetLimit: {},
  budgetUsed: {},
} as WorkflowTaskSnapshot;

it("shows a truthful cancel request receipt and a recoverable command error", async () => {
  const command = vi
    .fn()
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce({ status: "accepted" });
  render(
    <RunDetail
      snapshot={snapshot}
      connection="live"
      onSelectNode={vi.fn()}
      onCommand={command}
    />,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Request cancel" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Request failed");
  await user.click(screen.getByRole("button", { name: "Request cancel" }));
  expect(await screen.findByRole("status")).toHaveTextContent(
    "Request accepted",
  );
  expect(screen.queryByText(/canceled/u)).toBeNull();
});

it("labels exhausted stream errors as disconnected and offers retry", async () => {
  const retry = vi.fn();
  render(
    <RunDetail
      snapshot={snapshot}
      connection="error"
      onSelectNode={vi.fn()}
      onRetryConnection={retry}
    />,
  );
  expect(screen.getByRole("status")).toHaveTextContent("Disconnected");
  expect(screen.queryByText(/Reconnecting/u)).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: "Retry connection" }),
  );
  expect(retry).toHaveBeenCalledOnce();
});

it("shows persisted browser values and evidence and offers frozen replay", async () => {
  const replay = vi.fn().mockResolvedValue({ taskId: "task-replay" });
  render(
    <RunDetail
      snapshot={{
        ...snapshot,
        status: "succeeded",
        taskKind: "browser",
        replayOfTaskId: "task-original",
        availableActions: ["replay"],
        browserRun: {
          executionId: "execution-1",
          bundleId: "bundle-1",
          inputSha256: "a".repeat(64),
          status: "passed",
          failureCode: null,
          replayModelCalls: 0,
          steps: [{ title: "Read quote", status: "passed", durationMs: 21 }],
          outputs: [{ customer: "Ada", quote: "FOUND" }],
          evidence: [
            {
              evidenceId: "evidence-1",
              kind: "screenshot",
              sha256: "b".repeat(64),
              url: "/api/v1/projects/project-1/workflows/task-1/browser-evidence/evidence-1",
            },
          ],
        },
      }}
      connection="idle"
      onSelectNode={vi.fn()}
      onReplay={replay}
    />,
  );
  expect(screen.getByText("Read quote").closest("details")).not.toHaveAttribute(
    "open",
  );
  await userEvent.click(screen.getByText("Browser result · Passed"));
  expect(screen.getByText(/Ada/u)).toBeVisible();
  expect(screen.getByText("Read quote")).toBeInTheDocument();
  expect(screen.queryByText("Tapper is handling this task.")).toBeNull();
  expect(screen.getByText("Subagents")).toBeInTheDocument();
  expect(screen.getByText("Sources")).toBeInTheDocument();
  expect(screen.getByText("Replay of run task-original")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Screenshot/u })).toHaveAttribute(
    "href",
    "/api/v1/projects/project-1/workflows/task-1/browser-evidence/evidence-1",
  );
  expect(
    screen.queryByRole("button", { name: /Pause|Resume|Retry node/u }),
  ).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: "Replay frozen run" }),
  );
  expect(replay).toHaveBeenCalledOnce();
});
