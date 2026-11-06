import { useState } from "react";
import type { components } from "../../../shared/api/generated/schema";
import { useWorkflowLogs, useWorkflowNode } from "../api/queries";
import type { WorkflowCommandView, WorkflowTaskSnapshot } from "../api/client";
import { workflowEventLabel } from "./ActivityTimeline";

type Node = components["schemas"]["WorkflowNodeView"];
export function NodeDetails({
  projectId,
  snapshot,
  node,
  onBack,
  onCommand,
  selectedAttempt,
  onSelectAttempt,
}: {
  projectId: string;
  snapshot: WorkflowTaskSnapshot;
  node: Node;
  onBack: () => void;
  selectedAttempt?: number | null;
  onSelectAttempt?: (attempt: number) => void;
  onCommand?: (
    kind: "steer" | "retry" | "resume",
    nodeId: string,
    text?: string,
  ) => Promise<WorkflowCommandView | void>;
}) {
  const [localAttempt, setLocalAttempt] = useState(node.attempt);
  const attempt = onSelectAttempt
    ? (selectedAttempt ?? node.attempt)
    : localAttempt;
  const setAttempt = onSelectAttempt ?? setLocalAttempt;
  const [message, setMessage] = useState("");
  const [receipt, setReceipt] = useState<string | null>(null);
  const [error, setError] = useState(false);
  const detail = useWorkflowNode(projectId, snapshot.taskId, node.nodeId);
  const logs = useWorkflowLogs(
    projectId,
    snapshot.taskId,
    node.nodeId,
    attempt,
  );
  const value = detail.data
    ? { ...detail.data, ...node, artifacts: detail.data.artifacts }
    : node;
  const artifacts = detail.data?.artifacts ?? [];
  const attempts = value.attempts?.length
    ? value.attempts
    : [
        {
          attempt: node.attempt,
          status: node.status,
          createdAt: snapshot.createdAt,
        },
      ];
  const logItems = logs.data?.pages.flatMap((page) => page.items) ?? [];
  const canSteer =
    value.kind === "agent" &&
    snapshot.availableActions.includes("steer") &&
    !["succeeded", "failed", "canceled", "skipped"].includes(value.status);
  const canResume =
    snapshot.availableActions.includes("resume") && value.status === "paused";
  const canRetry =
    snapshot.availableActions.includes("retry") && value.status === "failed";
  const send = async (kind: "steer" | "retry" | "resume", text?: string) => {
    if (!onCommand) return;
    setError(false);
    try {
      const result = await onCommand(kind, node.nodeId, text);
      if (result) setReceipt(result.status);
      if (kind === "steer") setMessage("");
    } catch {
      setError(true);
    }
  };
  return (
    <div className="tap-workflow-node-detail">
      <button type="button" onClick={onBack}>
        Back to workflow
      </button>
      <h2 tabIndex={-1}>{value.displayName}</h2>
      <p className="tap-workflow-status" data-status={value.status}>
        {value.kind} ·{" "}
        {value.status === "unknown"
          ? "Outcome unknown"
          : value.status.replaceAll("_", " ")}
      </p>
      {value.status === "unknown" ? (
        <p>The source finished without a recorded result for this operation.</p>
      ) : null}
      {value.publicSummary ? <p>{value.publicSummary}</p> : null}
      {attempts.length > 1 ? (
        <label>
          Attempt{" "}
          <select
            value={attempt}
            onChange={(event) => setAttempt(Number(event.target.value))}
          >
            {attempts.map((item) => (
              <option key={item.attempt} value={item.attempt}>
                {item.attempt} · {item.status}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <p>Attempt {attempt}</p>
      )}
      {value.inputRefs?.length ? (
        <section>
          <h3>Input versions</h3>
          <ul>
            {value.inputRefs.map((ref) => (
              <li key={`${ref.sourceId}:${ref.revisionId}`}>
                {ref.sourceId} · {ref.revisionId}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {artifacts.length ? (
        <section>
          <h3>Artifacts</h3>
          <ul>
            {artifacts.map((item) => (
              <li key={item.artifactId}>
                {item.versionRef} · attempt {item.attempt}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {snapshot.origin === "native" &&
      onCommand &&
      (canSteer || canResume || canRetry || receipt || error) ? (
        <section className="tap-workflow-steer">
          <h3>Guide this node</h3>
          {canSteer ? (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void send("steer", message.trim());
              }}
            >
              <label htmlFor="tap-workflow-steer-text">
                Additional requirement
              </label>
              <textarea
                id="tap-workflow-steer-text"
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                maxLength={16000}
              />
              <button type="submit" disabled={!message.trim()}>
                Send to this node
              </button>
            </form>
          ) : null}
          {canResume ? (
            <button type="button" onClick={() => void send("resume")}>
              Resume node
            </button>
          ) : null}
          {canRetry ? (
            <button type="button" onClick={() => void send("retry")}>
              Retry node
            </button>
          ) : null}
          {receipt ? <p role="status">Request {receipt}</p> : null}
          {error ? (
            <p role="alert">Request failed. Retry with the same requirement.</p>
          ) : null}
        </section>
      ) : null}
      <section className="tap-workflow-logs" aria-label="Node log">
        <h3>Node log</h3>
        {logs.isPending ? (
          <p>Loading log…</p>
        ) : logs.isError ? (
          <p role="alert">Log unavailable.</p>
        ) : logItems.length === 0 ? (
          <p>No public log entries yet.</p>
        ) : (
          <ol>
            {logItems.map((item) => (
              <li key={item.eventId}>
                <time dateTime={item.occurredAt}>
                  {new Date(item.occurredAt).toLocaleTimeString()}
                </time>
                <span>{workflowEventLabel(item.kind, item.payload)}</span>
                {typeof item.payload.summary === "string" ? (
                  <p>{item.payload.summary}</p>
                ) : null}
              </li>
            ))}
          </ol>
        )}
        {logs.hasNextPage ? (
          <button
            type="button"
            onClick={() => void logs.fetchNextPage()}
            disabled={logs.isFetchingNextPage}
          >
            {logs.isFetchingNextPage ? "Loading…" : "Load more log"}
          </button>
        ) : null}
      </section>
    </div>
  );
}
