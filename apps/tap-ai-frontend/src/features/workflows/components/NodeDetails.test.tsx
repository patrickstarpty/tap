import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type { WorkflowTaskSnapshot } from "../api/client";
import { useWorkflowLogs, useWorkflowNode } from "../api/queries";
import { NodeDetails } from "./NodeDetails";

vi.mock("../api/queries", () => ({
  useWorkflowLogs: vi.fn(),
  useWorkflowNode: vi.fn(),
}));
const node: WorkflowTaskSnapshot["nodes"][number] = {
  nodeId: "node-1",
  parentNodeId: "root",
  agentId: "sdet",
  agentRevisionId: "rev-1",
  displayName: "SDET",
  kind: "agent",
  status: "running",
  attempt: 2,
  planRevision: 2,
  sourceRuntime: "native",
};
const snapshot = {
  taskId: "task-1",
  projectId: "project-1",
  origin: "native",
  status: "running",
  availableActions: ["steer", "retry"],
  createdAt: "2026-11-06T00:00:00Z",
} as WorkflowTaskSnapshot;

it("keeps failed attempts available, pages public logs, and reports a steer receipt", async () => {
  const fetchNextPage = vi.fn();
  vi.mocked(useWorkflowNode).mockReturnValue({
    data: {
      ...node,
      artifacts: [],
      attempts: [
        { attempt: 1, status: "failed", createdAt: "2026-11-06T00:00:00Z" },
        { attempt: 2, status: "running", createdAt: "2026-11-06T00:01:00Z" },
      ],
      inputRefs: [],
    },
    isPending: false,
  } as never);
  vi.mocked(useWorkflowLogs).mockReturnValue({
    data: { pages: [{ items: [], nextCursor: 2 }] },
    isPending: false,
    isError: false,
    hasNextPage: true,
    isFetchingNextPage: false,
    fetchNextPage,
  } as never);
  const command = vi.fn().mockResolvedValue({ status: "accepted" });
  render(
    <NodeDetails
      projectId="project-1"
      snapshot={snapshot}
      node={node}
      onBack={vi.fn()}
      onCommand={command}
    />,
  );
  const user = userEvent.setup();
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Attempt" }),
    "1",
  );
  expect(useWorkflowLogs).toHaveBeenLastCalledWith(
    "project-1",
    "task-1",
    "node-1",
    1,
  );
  await user.click(screen.getByRole("button", { name: "Load more log" }));
  expect(fetchNextPage).toHaveBeenCalledOnce();
  await user.type(
    screen.getByRole("textbox", { name: "Additional requirement" }),
    "Check Safari",
  );
  await user.click(screen.getByRole("button", { name: "Send to this node" }));
  expect(command).toHaveBeenCalledWith("steer", "node-1", "Check Safari");
  expect(await screen.findByRole("status")).toHaveTextContent(
    "Request accepted",
  );
});

it("shows only safe actual tool identity and outcome in node logs", () => {
  vi.mocked(useWorkflowNode).mockReturnValue({
    data: null,
    isPending: false,
  } as never);
  vi.mocked(useWorkflowLogs).mockReturnValue({
    data: {
      pages: [
        {
          items: [
            {
              eventId: "event-tool",
              kind: "node.tool_finished",
              occurredAt: "2026-11-06T00:00:00Z",
              payload: {
                tool_id: "knowledge.search",
                outcome: "succeeded",
                secret: "private-query",
              },
            },
          ],
          nextCursor: null,
        },
      ],
    },
    isPending: false,
    isError: false,
    hasNextPage: false,
  } as never);
  render(
    <NodeDetails
      projectId="project-1"
      snapshot={snapshot}
      node={node}
      onBack={vi.fn()}
    />,
  );
  expect(screen.getByText("Tool succeeded: knowledge.search")).toBeVisible();
  expect(screen.queryByText("private-query")).toBeNull();
});
