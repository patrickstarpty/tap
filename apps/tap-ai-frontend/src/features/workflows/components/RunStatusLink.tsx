import { ExecutionSteps } from "./ExecutionSteps";
import type { WorkflowTaskSnapshot } from "../api/client";

export function RunStatusLink({
  snapshot,
  onOpen,
}: {
  snapshot: WorkflowTaskSnapshot;
  onOpen: () => void;
}) {
  const latest = snapshot.events.at(-1);
  return (
    <div>
      <ExecutionSteps snapshot={snapshot} />
      <div className="tap-run-status-link" role="status">
        <span>Tapper · {snapshot.status.replaceAll("_", " ")}</span>
        {latest?.kind === "task.awaiting_input" ||
        latest?.kind === "node.waiting" ? (
          <span>
            {typeof latest.payload.question === "string"
              ? latest.payload.question
              : typeof latest.payload.reason === "string"
                ? latest.payload.reason
                : "Waiting for input"}
          </span>
        ) : null}
        <button type="button" onClick={onOpen}>
          View progress
        </button>
      </div>
    </div>
  );
}
