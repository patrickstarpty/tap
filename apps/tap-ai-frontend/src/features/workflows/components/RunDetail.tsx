import { useState } from "react";
import type { WorkflowCommandView, WorkflowTaskSnapshot } from "../api/client";
import { ActivityTimeline } from "./ActivityTimeline";
import { WorkflowGraph } from "./WorkflowGraph";

export function RunDetail({
  snapshot,
  connection,
  onSelectNode,
  onCommand,
  onRetryConnection,
}: {
  snapshot: WorkflowTaskSnapshot;
  connection: "idle" | "connecting" | "live" | "reconnecting" | "error";
  onSelectNode: (nodeId: string) => void;
  onRetryConnection?: () => void;
  onCommand?: (
    kind: "pause" | "resume" | "cancel",
    nodeId?: string,
  ) => Promise<WorkflowCommandView | void>;
}) {
  const [receipt, setReceipt] = useState<string | null>(null);
  const [error, setError] = useState(false);
  const [pending, setPending] = useState(false);
  const request = async (kind: "pause" | "resume" | "cancel") => {
    if (!onCommand || pending) return;
    setPending(true);
    setError(false);
    try {
      const result = await onCommand(kind);
      setReceipt(result?.status ?? "accepted");
    } catch {
      setError(true);
    } finally {
      setPending(false);
    }
  };
  const latest = snapshot.events.at(-1);
  const waiting = [...snapshot.events]
    .reverse()
    .find(
      (event) =>
        event.kind === "task.awaiting_input" || event.kind === "node.waiting",
    );
  const waitingText =
    typeof waiting?.payload.question === "string"
      ? waiting.payload.question
      : typeof waiting?.payload.reason === "string"
        ? waiting.payload.reason
        : null;
  const active = [...snapshot.events]
    .reverse()
    .find((event) => event.kind !== "activity");
  const root = snapshot.nodes.find((node) => node.agentId === "orchestrator");
  const completed = snapshot.nodes.filter(
    (node) => node.agentId !== "orchestrator" && node.status === "succeeded",
  ).length;
  return (
    <div className="tap-workflow-overview">
      <section className="tap-workflow-progress" aria-label="Task overview">
        <p className="tap-workflow-goal">
          {snapshot.goalSummary || "Current task"}
        </p>
        <p className="tap-workflow-status" data-status={snapshot.status}>
          {snapshot.status.replaceAll("_", " ")}
        </p>
        <p className="tap-workflow-meta">Run {snapshot.taskId}</p>
        <p className="tap-workflow-meta">
          Started {new Date(snapshot.createdAt).toLocaleString()}
        </p>
        <p className="tap-workflow-meta">
          Last updated{" "}
          {latest
            ? new Date(latest.occurredAt).toLocaleString()
            : new Date(snapshot.createdAt).toLocaleString()}
        </p>
        {connection === "reconnecting" ? (
          <p role="status">Reconnecting · showing last saved update</p>
        ) : null}
        {connection === "error" ? (
          <p role="status">
            Disconnected · showing last saved update.{" "}
            {onRetryConnection ? (
              <button type="button" onClick={onRetryConnection}>
                Retry connection
              </button>
            ) : null}
          </p>
        ) : null}
        {waitingText &&
        (waiting === latest ||
          ["awaiting_input", "waiting_dependency"].includes(
            snapshot.status,
          )) ? (
          <p>{waitingText}</p>
        ) : null}
        {active ? (
          <p>
            Tapper activity:{" "}
            {active.kind.replaceAll(".", " ").replaceAll("_", " ")}
          </p>
        ) : null}
        {root?.publicSummary ? <p>{root.publicSummary}</p> : null}
        {completed > 0 ? <p>{completed} specialist nodes completed</p> : null}
        {snapshot.origin === "native" && onCommand ? (
          <div className="tap-workflow-actions">
            {snapshot.availableActions
              .filter((action) =>
                ["pause", "resume", "cancel"].includes(action),
              )
              .map((action) => (
                <button
                  key={action}
                  type="button"
                  disabled={pending}
                  onClick={() =>
                    void request(action as "pause" | "resume" | "cancel")
                  }
                >
                  {action === "pause"
                    ? "Request pause"
                    : action === "cancel"
                      ? "Request cancel"
                      : "Resume"}
                </button>
              ))}
          </div>
        ) : null}
        {receipt ? (
          <p role="status">
            Request {receipt}. Task state will update when applied.
          </p>
        ) : null}
        {error ? <p role="alert">Request failed. Please retry.</p> : null}
        {snapshot.status === "pausing" ? (
          <p>Pause requested; work has not paused yet.</p>
        ) : null}
        {snapshot.status === "canceling" ? (
          <p>Cancellation requested; work may still be running.</p>
        ) : null}
      </section>
      <section aria-label="Workflow">
        <h2>Workflow</h2>
        <WorkflowGraph
          nodes={snapshot.nodes}
          edges={snapshot.edges}
          onSelect={onSelectNode}
        />
      </section>
      {snapshot.commands.length > 0 ? (
        <section aria-label="Requests">
          <h3>Requests</h3>
          <ul>
            {snapshot.commands.map((item) => (
              <li key={item.commandId}>
                {item.kind}: {item.status}
                {item.reason ? ` · ${item.reason}` : ""}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <ActivityTimeline events={snapshot.events} />
    </div>
  );
}
