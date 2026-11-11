import { useEffect, useRef, useState } from "react";
import { SourceAcceptanceReview } from "./SourceAcceptanceReview";
import type { AcceptanceContract, LcaDefinition } from "./client";
import {
  boundedId,
  isAgentSourceReference,
  sameAgentSource,
  type AgentSourceReference,
} from "./assistantProtocol";
interface Preview {
  schema_version: "aa-lca-draft-v1";
  complete: boolean;
  acceptance_contract?: AcceptanceContract | null;
  contract_review_status?: "reviewed" | "legacy_unreviewed" | "invalid";
  definition: LcaDefinition | null;
  issues: {
    action_id: string;
    sequence: number;
    operation: string;
    reason: string;
  }[];
  source: {
    task_id: string;
    execution_id: string;
    bundle_id: string;
    input_sha256: string;
  };
}
/** Read-only source review. Loading here never creates, publishes or runs a flow. */
export function AgentDraftImport({
  projectId,
  disabled,
  onLoad,
  sourceReference,
  onSourceClear,
}: {
  projectId: string;
  disabled: boolean;
  sourceReference?: AgentSourceReference | null;
  onSourceClear?: () => void;
  onLoad: (definition: LcaDefinition, title: string) => void;
}) {
  const [manualTask, setTask] = useState(
    () => new URLSearchParams(window.location.search).get("aa-task") ?? "",
  );
  const task = sourceReference?.task_id ?? manualTask;
  const scope = JSON.stringify([projectId, task, sourceReference ?? null]);
  const [review, setReview] = useState<{
    value: Preview;
    scope: string;
  } | null>(null);
  const preview = review?.scope === scope && !disabled ? review.value : null;
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const active = useRef<AbortController | null>(null);
  useEffect(() => {
    active.current?.abort();
    setReview(null);
    setError("");
    setLoading(false);
    return () => active.current?.abort();
  }, [scope, disabled]);
  async function inspect() {
    if (
      disabled ||
      loading ||
      !boundedId(task) ||
      (sourceReference && !isAgentSourceReference(sourceReference))
    )
      return;
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setLoading(true);
    setError("");
    setReview(null);
    try {
      const response = await fetch(
        `/api/projects/${encodeURIComponent(projectId)}/lca/agent-tasks/${encodeURIComponent(task)}/preview`,
        {
          credentials: "same-origin",
          cache: "no-store",
          signal: controller.signal,
        },
      );
      if (!response.ok)
        throw new Error(
          "AA source is unavailable or not a completed live run.",
        );
      const value = (await response.json()) as Preview;
      if (
        value.schema_version !== "aa-lca-draft-v1" ||
        !isAgentSourceReference(value.source) ||
        value.source.task_id !== task ||
        (sourceReference && !sameAgentSource(value.source, sourceReference)) ||
        !Array.isArray(value.issues) ||
        typeof value.complete !== "boolean" ||
        (value.complete && (!value.definition || value.issues.length)) ||
        (value.complete &&
          value.contract_review_status === "reviewed" &&
          (value.definition?.source_acceptance?.review_status !== "reviewed" ||
            value.definition.source_acceptance.source.input_sha256 !==
              value.source.input_sha256 ||
            value.definition.source_acceptance.source.execution_id !==
              value.source.execution_id))
      )
        throw new Error("AA source could not be verified.");
      if (!controller.signal.aborted) setReview({ value, scope });
    } catch (cause) {
      if (!controller.signal.aborted)
        setError(
          cause instanceof Error
            ? cause.message
            : "AA source could not be verified.",
        );
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }
  return (
    <details
      className="lca-recording-disclosure"
      open={Boolean(
        sourceReference ||
        new URLSearchParams(window.location.search).get("aa-task"),
      )}
    >
      <summary>Import an Agentic Automation result</summary>
      <p>
        Preview the original AA actions and business steps. Review targets
        before saving a new draft. This does not execute or publish it. Read
        assertions and named outputs retain their original meaning; the original
        script and evidence remain attached to the AA task.
      </p>
      <label>
        AA task ID
        <input
          value={task}
          readOnly={Boolean(sourceReference)}
          disabled={disabled || loading}
          onChange={(event) => {
            setTask(event.target.value);
            setReview(null);
            setError("");
          }}
        />
      </label>
      <button
        type="button"
        disabled={disabled || loading || !boundedId(task)}
        onClick={() => void inspect()}
      >
        {loading ? "Loading AA source…" : "Preview AA task"}
      </button>
      {sourceReference && onSourceClear ? (
        <button type="button" onClick={onSourceClear}>
          Clear Tapper source
        </button>
      ) : null}
      {disabled && sourceReference ? (
        <p role="status">
          Finish recording, publication, or pending operations and save or
          discard your current edits before importing.
        </p>
      ) : null}
      {error ? <p role="alert">{error}</p> : null}
      {preview ? (
        <section aria-label="AA import review">
          <p>
            Source task: {preview.source.task_id} · execution:{" "}
            {preview.source.execution_id} · original script bundle:{" "}
            {preview.source.bundle_id}
          </p>
          <SourceAcceptanceReview
            status={preview.contract_review_status ?? "legacy_unreviewed"}
            contract={preview.acceptance_contract ?? null}
          />
          {preview.issues.length ? (
            <>
              <p role="alert">
                Not all source actions can be represented without changing their
                meaning. No executable draft will be created. The original
                script and evidence remain available in the AA task.
              </p>
              <ul>
                {preview.issues.map((issue, index) => (
                  <li key={`${issue.action_id}-${index}`}>
                    Step {issue.sequence} {issue.operation}: {issue.reason}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {preview.complete && preview.definition ? (
            <>
              <p>
                {preview.definition.steps.length} actions ·{" "}
                {preview.definition.bdd_steps?.length ?? 0} BDD steps. Imported
                targets require review; AA success does not certify this new
                draft.
              </p>
              <button
                type="button"
                disabled={disabled}
                onClick={() => {
                  if (disabled || review?.scope !== scope) return;
                  onLoad(
                    preview.definition!,
                    `AA task ${preview.source.task_id}`,
                  );
                  setReview(null);
                }}
              >
                Use as unsaved draft
              </button>
            </>
          ) : null}
          <button type="button" onClick={() => setReview(null)}>
            Close preview
          </button>
        </section>
      ) : null}
    </details>
  );
}
