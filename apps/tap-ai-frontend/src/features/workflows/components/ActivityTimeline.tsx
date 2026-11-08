import { ActionDetails } from "./ExecutionSteps";
import type { WorkflowEventEnvelope } from "../api/client";

const labels: Record<string, string> = {
  "task.created": "Task created",
  "task.claimed": "Tapper started",
  "task.completed": "Task completed",
  "task.failed": "Task failed",
  "task.canceled": "Task canceled",
  "task.awaiting_input": "Waiting for input",
  "node.created": "Work started",
  "node.delegated": "Work delegated",
  "node.started": "Node started",
  "node.finished": "Node finished",
  "node.failed": "Node failed",
  "node.outcome_unknown": "Node outcome unknown",
  "node.waiting": "Waiting for dependency",
  "command.received": "Request accepted",
  "command.receipted": "Request updated",
  "plan.dependency_added": "Plan updated",
};

export function workflowEventLabel(
  kind: string,
  payload: Record<string, unknown>,
): string {
  const identifierField = kind.startsWith("node.skill_")
    ? "skill_id"
    : "tool_id";
  const identifier = payload[identifierField];
  if (
    [
      "node.tool_started",
      "node.tool_finished",
      "node.skill_started",
      "node.skill_finished",
    ].includes(kind) &&
    typeof identifier === "string" &&
    /^[A-Za-z0-9._:-]{1,128}$/u.test(identifier)
  ) {
    const subject = kind.startsWith("node.skill_") ? "Skill" : "Tool";
    if (kind.endsWith("_started")) return `${subject} started: ${identifier}`;
    const outcome = payload.outcome;
    if (
      typeof outcome === "string" &&
      ["succeeded", "failed", "unknown", "rejected", "activated"].includes(
        outcome,
      )
    )
      return `${subject} ${outcome}: ${identifier}`;
    return `${subject} finished: ${identifier}`;
  }
  if (kind.startsWith("browser.action_"))
    return `${typeof payload.step_title === "string" ? payload.step_title : "Browser action"} · ${payload.outcome ?? "in progress"}`;
  return labels[kind] ?? "";
}
export function ActivityTimeline({
  events,
  limit = 20,
}: {
  events: readonly WorkflowEventEnvelope[];
  limit?: number;
}) {
  const visible = events
    .filter((event) => workflowEventLabel(event.kind, event.payload))
    .slice(-limit)
    .reverse();
  return (
    <section className="tap-workflow-timeline" aria-label="Activity timeline">
      <h3>Activity</h3>
      {visible.length === 0 ? (
        <p>No activity yet.</p>
      ) : (
        <ol>
          {visible.map((event) => {
            const detail =
              typeof event.payload.question === "string"
                ? event.payload.question
                : typeof event.payload.summary === "string"
                  ? event.payload.summary
                  : null;
            return (
              <li key={event.eventId}>
                <span>{workflowEventLabel(event.kind, event.payload)}</span>
                <time dateTime={event.occurredAt}>
                  {new Date(event.occurredAt).toLocaleTimeString()}
                </time>
                {detail ? <p>{detail}</p> : null}
                <ActionDetails event={event} />
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
