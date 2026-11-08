export function advanceCursor(
  current: number,
  incoming: number,
): "ignore" | "apply" | "resync" {
  if (incoming <= current) return "ignore";
  return incoming === current + 1 ? "apply" : "resync";
}

import type { components } from "../../../shared/api/generated/schema";

type Snapshot = components["schemas"]["WorkflowTaskSnapshot"];
type Event = components["schemas"]["WorkflowEventEnvelope"];
type Status = components["schemas"]["WorkflowStatus"];
const STATUSES = new Set<Status>([
  "queued",
  "running",
  "waiting_dependency",
  "awaiting_input",
  "pausing",
  "paused",
  "recovering",
  "succeeded",
  "failed",
  "canceling",
  "canceled",
  "skipped",
]);
const capabilityTransitions = new Set([
  "task.status",
  "task.yielded",
  "task.completed",
  "task.canceled",
  "task.failed",
  "task.awaiting_input",
  "node.waiting",
  "node.finished",
  "node.failed",
  "node.canceled",
]);
const structural = new Set([
  "task.route_decided",
  "node.created",
  "node.delegated",
  "node.unblocked",
  "plan.dependency_added",
  "command.received",
  "command.receipted",
]);

/** Returns null when the durable snapshot must be fetched again. */
export function applyWorkflowEvent(
  snapshot: Snapshot,
  event: Event,
): Snapshot | null {
  if (
    event.taskId !== snapshot.taskId ||
    (event.projectId != null && event.projectId !== snapshot.projectId)
  )
    return null;
  const cursor = advanceCursor(snapshot.lastSequence, event.sequence);
  if (cursor === "ignore") return snapshot;
  if (
    cursor === "resync" ||
    structural.has(event.kind) ||
    (capabilityTransitions.has(event.kind) &&
      (event.nodeId === null ||
        event.attempt >=
          (snapshot.nodes.find((node) => node.nodeId === event.nodeId)
            ?.attempt ?? 0))) ||
    (event.kind === "node.started" &&
      snapshot.availableActions.includes("retry"))
  )
    return null;
  const status = STATUSES.has(event.status as Status)
    ? (event.status as Status)
    : null;
  const nodes = snapshot.nodes.map((node) => {
    if (node.nodeId !== event.nodeId || status === null || event.attempt < 1)
      return node;
    if (event.attempt < node.attempt) {
      return {
        ...node,
        attempts: node.attempts?.map((item) =>
          item.attempt === event.attempt ? { ...item, status } : item,
        ),
      };
    }
    const attempts = node.attempts ?? [];
    return {
      ...node,
      attempt: event.attempt,
      status,
      attempts: attempts.some((item) => item.attempt === event.attempt)
        ? attempts.map((item) =>
            item.attempt === event.attempt ? { ...item, status } : item,
          )
        : [
            ...attempts,
            { attempt: event.attempt, status, createdAt: event.occurredAt },
          ],
    };
  });
  return {
    ...snapshot,
    lastSequence: event.sequence,
    status: event.nodeId === null && status !== null ? status : snapshot.status,
    nodes,
    events: [...snapshot.events, event],
  };
}
