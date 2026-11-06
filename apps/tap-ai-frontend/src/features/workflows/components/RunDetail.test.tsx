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
