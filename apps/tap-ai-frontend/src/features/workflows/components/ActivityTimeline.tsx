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
  "node.waiting": "Waiting for dependency",
  "command.received": "Request accepted",
  "command.receipted": "Request updated",
  "plan.dependency_added": "Plan updated",
};
export function ActivityTimeline({
  events,
  limit = 20,
}: {
  events: readonly WorkflowEventEnvelope[];
  limit?: number;
}) {
  const visible = events.slice(-limit).reverse();
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
                <span>{labels[event.kind] ?? "Activity recorded"}</span>
                <time dateTime={event.occurredAt}>
                  {new Date(event.occurredAt).toLocaleTimeString()}
                </time>
                {detail ? <p>{detail}</p> : null}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
