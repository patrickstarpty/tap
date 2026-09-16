import { useState } from "react";
import type { ArtifactState } from "./legacy/artifacts/model";
import {
  backupAndRestoreAutomationState,
  loadLegacyAutomationState,
  loadPreviousAutomationState,
  readAutomationSnapshot,
} from "./legacy/artifacts/persistence";

function downloadUrl(state: ArtifactState): string {
  return `data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify({ version: 1, state }))}`;
}

export function WorkspaceTransfer({
  state,
  onRestore,
}: {
  state: ArtifactState;
  onRestore: (state: ArtifactState) => void;
}) {
  const [pending, setPending] = useState<ArtifactState | null>(null);
  const [previous, setPrevious] = useState(loadPreviousAutomationState);
  const [legacy] = useState(loadLegacyAutomationState);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  return (
    <details className="tap-workspace-transfer">
      <summary>Local workspace</summary>
      <p>
        Transfer browser-local prototype automations and simulated runs. This
        does not transfer production data.
      </p>
      <a download="tap-local-workspace.json" href={downloadUrl(state)}>
        Export workspace
      </a>
      {legacy ? (
        <button
          type="button"
          disabled={reading}
          onClick={() => {
            setPending(legacy);
            setError("");
            setNotice("");
          }}
        >
          Review pre-split workspace
        </button>
      ) : null}
      <label>
        Import workspace
        <input
          type="file"
          accept=".json,application/json"
          disabled={reading}
          onChange={async (event) => {
            const file = event.currentTarget.files?.[0];
            event.currentTarget.value = "";
            setPending(null);
            setError("");
            setNotice("");
            if (!file) return;
            setReading(true);
            try {
              const imported = readAutomationSnapshot(await file.text());
              if (imported === null)
                setError("File is not a valid TAP local workspace.");
              else setPending(imported);
            } catch {
              setError(
                "File could not be read. Choose the workspace file again.",
              );
            } finally {
              setReading(false);
            }
          }}
        />
      </label>
      {pending ? (
        <div>
          <p>
            {pending.automations.length} automations and {pending.runs.length}{" "}
            simulated runs. Restoring replaces this browser's local workspace
            after saving a backup.
          </p>
          <button
            type="button"
            onClick={() => {
              if (!backupAndRestoreAutomationState(state, pending)) {
                setError(
                  "Could not save the workspace and its backup. Nothing was replaced.",
                );
                return;
              }
              setPrevious(state);
              onRestore(pending);
              setPending(null);
              setError("");
              setNotice(
                "Local workspace restored. The previous workspace is available below.",
              );
            }}
          >
            Restore imported workspace
          </button>
          <button type="button" onClick={() => setPending(null)}>
            Cancel
          </button>
        </div>
      ) : null}
      {previous ? (
        <a
          download="tap-previous-local-workspace.json"
          href={downloadUrl(previous)}
        >
          Export previous workspace
        </a>
      ) : null}
      {error ? <p role="alert">{error}</p> : null}
      {notice ? <p role="status">{notice}</p> : null}
    </details>
  );
}
