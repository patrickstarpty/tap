import { useMemo, useState } from "react";
import type { WorkflowCommandView, WorkflowTaskSnapshot } from "../api/client";
import { BrowserControl, type BrowserControlTransport } from "./BrowserControl";
import { createBrowserControlTransport } from "./browserControlTransport";
import { ActivityTimeline } from "./ActivityTimeline";
import { ExecutionSteps } from "./ExecutionSteps";
import { ProgressResources } from "./ProgressResources";

export interface LcaSourceReference {
  task_id: string;
  execution_id: string;
  bundle_id: string;
  input_sha256: string;
}

export function RunDetail({
  snapshot,
  connection,
  onSelectNode,
  onCommand,
  onRetryConnection,
  onReplay,
  onProvideInput,
  onReviewLca,
  browserControlTransport,
}: {
  browserControlTransport?: BrowserControlTransport;
  snapshot: WorkflowTaskSnapshot;
  connection: "idle" | "connecting" | "live" | "reconnecting" | "error";
  onSelectNode: (nodeId: string) => void;
  onRetryConnection?: () => void;
  onReplay?: () => Promise<void>;
  onProvideInput?: () => void;
  onReviewLca?: (source: LcaSourceReference) => void;
  onCommand?: (
    kind: "pause" | "resume" | "cancel",
    nodeId?: string,
  ) => Promise<WorkflowCommandView | void>;
}) {
  const [receipt, setReceipt] = useState<string | null>(null);
  const [error, setError] = useState(false);
  const [pending, setPending] = useState(false);
  const [replayError, setReplayError] = useState(false);
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
  const browser = snapshot.taskKind === "browser";
  const browserRun = snapshot.browserRun;
  const controlEvent = snapshot.events.findLast(
    (event) =>
      event.kind === "browser.session_started" &&
      event.payload.control_version === "browser-control-v1" &&
      typeof event.payload.session_id === "string",
  );
  const sessionId = controlEvent?.payload.session_id;
  const controlTransport = useMemo(
    () =>
      browserControlTransport ??
      (typeof sessionId === "string"
        ? createBrowserControlTransport(
            snapshot.projectId,
            snapshot.taskId,
            sessionId,
          )
        : null),
    [browserControlTransport, sessionId, snapshot.projectId, snapshot.taskId],
  );
  const recording = snapshot.events.findLast(
    (event) => event.kind === "browser.recording_saved",
  );
  const recordingId = recording?.payload.evidence_id;
  const scriptFailure = snapshot.events.findLast(
    (event) => event.kind === "browser.script_failed",
  );
  const replay = async () => {
    if (!onReplay || pending) return;
    setPending(true);
    setReplayError(false);
    try {
      await onReplay();
    } catch {
      setReplayError(true);
    } finally {
      setPending(false);
    }
  };
  return (
    <div className="tap-workflow-overview">
      {!browserRun && typeof recordingId === "string" ? (
        <section aria-label="Browser recording">
          <h3>Recording</h3>
          <video
            controls
            preload="metadata"
            src={`/api/v1/projects/${encodeURIComponent(snapshot.projectId)}/workflows/${snapshot.taskId}/browser-evidence/${recordingId}`}
          />
        </section>
      ) : null}
      <section className="tap-workflow-progress" aria-label="Task overview">
        <p className="tap-workflow-status" data-status={snapshot.status}>
          {snapshot.status.replaceAll("_", " ")}
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
        {browser && snapshot.availableActions.includes("replay") && onReplay ? (
          <button
            type="button"
            disabled={pending}
            onClick={() => void replay()}
          >
            Replay frozen run
          </button>
        ) : null}
        {browserRun?.status === "passed" &&
        snapshot.status === "succeeded" &&
        !snapshot.replayOfTaskId ? (
          onReviewLca ? (
            <button
              type="button"
              onClick={() =>
                onReviewLca({
                  task_id: snapshot.taskId,
                  execution_id: browserRun.executionId,
                  bundle_id: browserRun.bundleId,
                  input_sha256: browserRun.inputSha256,
                })
              }
            >
              Review as LCA draft
            </button>
          ) : (
            <a
              href={`${(import.meta.env.VITE_TAP_WEB_URL || "").replace(/\/$/, "")}/?module=low-code&aa-task=${encodeURIComponent(snapshot.taskId)}`}
            >
              Review as LCA draft
            </a>
          )
        ) : null}
        {replayError ? (
          <p role="alert">Replay could not start. Try again.</p>
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
      {browser && snapshot.browserInputRequest ? (
        <section
          aria-label="Input needed"
          className="tap-workflow-browser-section"
        >
          <h2>Input needed</h2>
          <p>{snapshot.browserInputRequest.question}</p>
          {onProvideInput ? (
            <button type="button" onClick={onProvideInput}>
              Provide input in a new run
            </button>
          ) : null}
        </section>
      ) : null}
      {browser && typeof sessionId === "string" && controlTransport ? (
        <BrowserControl
          key={`${snapshot.taskId}:${sessionId}`}
          transport={controlTransport}
        />
      ) : null}
      <ExecutionSteps snapshot={snapshot} />
      <div className="tap-progress-resources">
        <section aria-label="Outputs" className="tap-progress-resource">
          <h2>Outputs</h2>
          {scriptFailure ? (
            <div role="status">
              <p>Task completed. Replay script could not be saved.</p>
              <details>
                <summary>Script details</summary>
                <p>
                  {String(
                    scriptFailure.payload.error ?? "Script storage failed",
                  )}
                </p>
              </details>
            </div>
          ) : null}
          {!scriptFailure && !browserRun && !snapshot.artifacts.length ? (
            <p className="tap-workflow-empty">Results will appear here</p>
          ) : null}
          {snapshot.artifacts.length ? (
            <ul>
              {snapshot.artifacts.map((item) => (
                <li key={item.artifactId}>{item.versionRef}</li>
              ))}
            </ul>
          ) : null}
          {browser && browserRun ? (
            <details
              aria-label="Browser result"
              className="tap-workflow-browser-section"
            >
              <summary>
                Browser result ·{" "}
                {browserRun.status === "passed"
                  ? "Passed"
                  : browserRun.status === "unknown"
                    ? "Outcome unknown"
                    : browserRun.status}
              </summary>
              {snapshot.replayOfTaskId ? (
                <p className="tap-workflow-meta">
                  Replay of run {snapshot.replayOfTaskId}
                </p>
              ) : null}
              {browserRun.failureCode ? (
                <p>Reason: {browserRun.failureCode}</p>
              ) : null}
              {browserRun.outputs.length > 0 ? (
                <div className="tap-workflow-browser-values">
                  <h3>Extracted values</h3>
                  {browserRun.outputs.map((output, index) => (
                    <dl key={index}>
                      {Object.entries(output).map(([key, value]) => (
                        <div key={key}>
                          <dt>{key}</dt>
                          <dd>
                            {typeof value === "string"
                              ? value
                              : JSON.stringify(value)}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  ))}
                </div>
              ) : null}
              {browserRun.steps.length > 0 ? (
                <div>
                  <h3>Steps</h3>
                  <ol className="tap-workflow-browser-steps">
                    {browserRun.steps.map((step, index) => (
                      <li key={`${index}-${step.title}`}>
                        <span>{step.title}</span>
                        <span>
                          {step.status} · {step.durationMs} ms
                        </span>
                      </li>
                    ))}
                  </ol>
                </div>
              ) : null}
              {browserRun.evidence.length > 0 ? (
                <div>
                  <h3>Evidence</h3>
                  <ul className="tap-workflow-browser-evidence">
                    {browserRun.evidence.map((item) => (
                      <li key={item.evidenceId}>
                        <a
                          href={item.url}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {item.kind === "screenshot"
                            ? "Screenshot"
                            : item.kind === "trace"
                              ? "Trace"
                              : item.kind === "video"
                                ? "Recording"
                                : "Result file"}
                        </a>
                        {item.kind === "screenshot" ? (
                          <img
                            src={item.url}
                            alt="Browser run screenshot"
                            loading="lazy"
                          />
                        ) : null}
                        {item.kind === "video" ? (
                          <video
                            controls
                            preload="metadata"
                            src={item.url}
                            aria-label="Browser run recording"
                          />
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <p className="tap-workflow-meta">
                Bundle {browserRun.bundleId} · Input{" "}
                {browserRun.inputSha256.slice(0, 12)} · Replay model calls{" "}
                {browserRun.replayModelCalls}
              </p>
            </details>
          ) : browser && snapshot.browserRunUnavailable ? (
            <p role="status">
              Saved browser evidence is temporarily unavailable. Retry the
              connection to load it.
            </p>
          ) : null}
        </section>
        <ProgressResources snapshot={snapshot} onSelectNode={onSelectNode} />
      </div>
      <details className="tap-run-diagnostics">
        <summary>Run details</summary>
        <p>{snapshot.goalSummary}</p>
        <p className="tap-workflow-meta">Run {snapshot.taskId}</p>
        <p className="tap-workflow-meta">
          Started {new Date(snapshot.createdAt).toLocaleString()}
        </p>
        {snapshot.nodes.some((node) => node.kind !== "agent") ? (
          <section aria-label="Other operations">
            <h3>Other operations</h3>
            <ul>
              {snapshot.nodes
                .filter((node) => node.kind !== "agent")
                .map((node) => (
                  <li key={node.nodeId}>
                    <button
                      type="button"
                      onClick={() => onSelectNode(node.nodeId)}
                    >
                      {node.displayName} · {node.status.replaceAll("_", " ")}
                    </button>
                  </li>
                ))}
            </ul>
          </section>
        ) : null}
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
      </details>
    </div>
  );
}
