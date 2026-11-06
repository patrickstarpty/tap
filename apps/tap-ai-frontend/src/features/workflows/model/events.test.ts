import { expect, it } from "vitest";
import { advanceCursor } from "./events";

it("requests a snapshot when an event gap appears", () => {
  expect(advanceCursor(8, 8)).toBe("ignore");
  expect(advanceCursor(8, 9)).toBe("apply");
  expect(advanceCursor(8, 11)).toBe("resync");
});

import type { components } from "../../../shared/api/generated/schema";
import { applyWorkflowEvent } from "./events";

type Snapshot = components["schemas"]["WorkflowTaskSnapshot"];
type Event = components["schemas"]["WorkflowEventEnvelope"];
const snapshot: Snapshot = {
  taskId: "task-1",
  projectId: "project-1",
  conversationId: "conversation-1",
  status: "running",
  planRevision: 1,
  lastSequence: 8,
  origin: "native",
  availableActions: ["steer", "pause", "cancel"],
  displayName: "Tapper",
  goalSummary: "Review login",
  createdAt: "2026-11-06T00:00:00Z",
  nodes: [
    {
      nodeId: "node-1",
      parentNodeId: "root",
      agentId: "sdet",
      agentRevisionId: "sdet-v1",
      displayName: "SDET",
      kind: "agent",
      status: "failed",
      attempt: 1,
      planRevision: 1,
      sourceRuntime: "native",
      attempts: [
        { attempt: 1, status: "failed", createdAt: "2026-11-06T00:00:00Z" },
      ],
    },
  ],
  edges: [],
  commands: [],
  artifacts: [],
  events: [],
  budgetLimit: {},
  budgetUsed: {},
};
const started: Event = {
  eventId: "event-9",
  taskId: "task-1",
  sequence: 9,
  nodeId: "node-1",
  attempt: 2,
  kind: "node.started",
  status: "running",
  payload: { node_id: "node-1", attempt: 2 },
  occurredAt: "2026-11-06T00:01:00Z",
};

it("retains failed attempts when a retry starts and ignores duplicate replay", () => {
  const next = applyWorkflowEvent(snapshot, started);
  expect(next?.nodes[0]?.status).toBe("running");
  expect(
    next?.nodes[0]?.attempts?.map((item) => [item.attempt, item.status]),
  ).toEqual([
    [1, "failed"],
    [2, "running"],
  ]);
  expect(next?.lastSequence).toBe(9);
  expect(applyWorkflowEvent(next!, started)).toBe(next);
});

it("requests a snapshot on gaps or structural events", () => {
  expect(applyWorkflowEvent(snapshot, { ...started, sequence: 11 })).toBeNull();
  expect(
    applyWorkflowEvent(snapshot, { ...started, kind: "node.created" }),
  ).toBeNull();
});

it("does not project a prior attempt onto the current node", () => {
  const next = applyWorkflowEvent(snapshot, {
    ...started,
    kind: "node.failed",
    status: "failed",
    attempt: 0,
  });
  expect(next?.nodes[0]?.status).toBe("failed");
  expect(next?.nodes[0]?.attempt).toBe(1);
});
