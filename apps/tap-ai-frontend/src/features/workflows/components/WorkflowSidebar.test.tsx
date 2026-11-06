import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { components } from "../../../shared/api/generated/schema";
import { createTestQueryClient } from "../../../shared/testing/renderApp";
import { WorkflowSidebar } from "./WorkflowSidebar";

type Snapshot = components["schemas"]["WorkflowTaskSnapshot"];
const snapshot: Snapshot = {
  taskId: "task-1",
  projectId: "project-1",
  conversationId: "conversation-1",
  status: "running",
  planRevision: 1,
  lastSequence: 3,
  origin: "native",
  availableActions: ["steer", "pause", "cancel"],
  displayName: "Tapper",
  goalSummary: "Review login",
  createdAt: "2026-11-06T00:00:00Z",
  nodes: [
    {
      nodeId: "root",
      parentNodeId: null,
      agentId: "orchestrator",
      agentRevisionId: "root-v1",
      displayName: "Tapper",
      kind: "agent",
      status: "running",
      attempt: 1,
      planRevision: 1,
      sourceRuntime: "native",
    },
    {
      nodeId: "sdet-1",
      parentNodeId: "root",
      agentId: "sdet",
      agentRevisionId: "sdet-v1",
      displayName: "SDET",
      kind: "agent",
      status: "running",
      attempt: 1,
      planRevision: 1,
      sourceRuntime: "native",
    },
    {
      nodeId: "approval-1",
      parentNodeId: "root",
      agentId: null,
      agentRevisionId: null,
      displayName: "Review",
      kind: "approval",
      status: "queued",
      attempt: 1,
      planRevision: 1,
      sourceRuntime: "native",
    },
  ],
  edges: [],
  commands: [],
  artifacts: [],
  budgetLimit: {},
  budgetUsed: {},
  events: [
    {
      eventId: "e-3",
      taskId: "task-1",
      sequence: 3,
      nodeId: "root",
      attempt: 1,
      kind: "task.awaiting_input",
      payload: { question: "Which browser?" },
      occurredAt: "2026-11-06T00:02:00Z",
      status: "awaiting_input",
    },
  ],
};

it("names dependency predecessors on each workflow node", () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    Response.json({ items: [], nextCursor: null }),
  );
  const withDependency = {
    ...snapshot,
    edges: [{ fromNodeId: "sdet-1", toNodeId: "approval-1", planRevision: 1 }],
  };
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <WorkflowSidebar
        projectId="project-1"
        snapshot={withDependency}
        connection="live"
        onClose={() => undefined}
      />
    </QueryClientProvider>,
  );
  expect(
    screen.getByRole("button", { name: /Review.*Depends on SDET/u }),
  ).toBeVisible();
  expect(
    document.querySelector('[data-workflow-edge="sdet-1:approval-1"]'),
  ).not.toBeNull();
});

it("shows Tapper progress without a callable root card and replaces overview with node details", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (request) => {
    const url = (request as Request).url;
    return Response.json(
      url.includes("/logs")
        ? { items: [], nextCursor: null }
        : { ...snapshot.nodes[1], artifacts: [] },
    );
  });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <WorkflowSidebar
        projectId="project-1"
        snapshot={snapshot}
        connection="live"
        onClose={() => undefined}
      />
    </QueryClientProvider>,
  );
  expect(screen.getByRole("heading", { name: "Task progress" })).toBeVisible();
  expect(screen.getAllByText("Which browser?")[0]).toBeVisible();
  expect(screen.queryByRole("button", { name: /Tapper.*agent/i })).toBeNull();
  expect(screen.getByText("Approval")).toBeVisible();
  await user.click(screen.getByRole("button", { name: /SDET/ }));
  expect(
    screen.getByRole("button", { name: "Back to workflow" }),
  ).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Workflow" })).toBeNull();
});

it("keeps selected node visible with authoritative status and connection retry", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (request) => {
    const url = (request as Request).url;
    return Response.json(
      url.includes("/logs")
        ? { items: [], nextCursor: null }
        : { ...snapshot.nodes[1], artifacts: [] },
    );
  });
  const retry = vi.fn();
  const user = userEvent.setup();
  const queryClient = createTestQueryClient();
  const view = (current: Snapshot, connection: "live" | "error") => (
    <QueryClientProvider client={queryClient}>
      <WorkflowSidebar
        projectId="project-1"
        snapshot={current}
        connection={connection}
        onRetryConnection={retry}
        onClose={() => undefined}
      />
    </QueryClientProvider>
  );
  const { rerender } = render(view(snapshot, "live"));
  await user.click(screen.getByRole("button", { name: /SDET/u }));
  rerender(
    view(
      {
        ...snapshot,
        availableActions: ["retry"],
        nodes: snapshot.nodes.map((node) =>
          node.nodeId === "sdet-1" ? { ...node, status: "failed" } : node,
        ),
      },
      "error",
    ),
  );
  expect(screen.getByRole("heading", { name: "Node details" })).toBeVisible();
  expect(screen.getByText("agent · failed")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Retry connection" }));
  expect(retry).toHaveBeenCalledOnce();
});
