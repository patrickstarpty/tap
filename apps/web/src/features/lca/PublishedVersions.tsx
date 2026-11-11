import { parseParameterValue } from "./DefinitionEditor";
import { useEffect, useRef, useState } from "react";
import {
  LcaHttpError,
  type LcaDefinition,
  type LcaClient,
  type LcaRelease,
  type LcaRun,
  type Scalar,
} from "./client";

type Intent = { key: string } & (
  | {
      kind: "publish";
      body: { expected_version: number; verified_run_id: string };
    }
  | {
      kind: "replay";
      release: string;
      body: { parameters: Record<string, Scalar> };
    }
);
export function PublishedVersions({
  client,
  projectId,
  flowId,
  version,
  verifiedRunId,
  dirty,
  locked,
  onRun,
  onActiveChange,
}: {
  client: LcaClient;
  projectId: string;
  flowId: string;
  version: number;
  verifiedRunId?: string;
  dirty: boolean;
  locked: boolean;
  onRun(run: LcaRun): void;
  onActiveChange?(active: boolean): void;
}) {
  const [releases, setReleases] = useState<LcaRelease[]>([]);
  const [selected, setSelected] = useState<
    (LcaRelease & { definition: LcaDefinition }) | null
  >(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [pending, setPending] = useState<Intent | null>(null);
  const alive = useRef(true);
  const actionLock = useRef(false);
  useEffect(() => {
    alive.current = true;
    let current = true;
    setLoading(true);
    setError("");
    client
      .listReleases(projectId, flowId)
      .then((items) => {
        if (alive.current && current) setReleases(items);
      })
      .catch(() => {
        if (alive.current && current)
          setError(
            "Published versions could not be loaded. Refresh the list to retry.",
          );
      })
      .finally(() => {
        if (alive.current && current) setLoading(false);
      });
    return () => {
      current = false;
      alive.current = false;
    };
  }, [client, projectId, flowId, refresh]);
  async function review(release: LcaRelease) {
    if (actionLock.current) return;
    actionLock.current = true;
    setWorking(true);
    setError("");
    try {
      const next = await client.getRelease(projectId, release.id);
      if (alive.current) {
        setSelected(next);
        setValues({});
      }
    } catch {
      if (alive.current)
        setError("This published version could not be loaded. Try again.");
    } finally {
      actionLock.current = false;
      if (alive.current) setWorking(false);
    }
  }
  function runSelected() {
    if (!selected) return;
    try {
      const parameters: Record<string, Scalar> = {};
      for (const field of selected.definition.parameters) {
        if (Object.hasOwn(values, field.name))
          parameters[field.name] = parseParameterValue(
            field.type,
            values[field.name],
          );
        else if (field.required && field.default === undefined)
          throw new Error(`Enter published parameter ${field.name}.`);
      }
      void execute({
        kind: "replay",
        release: selected.id,
        key: crypto.randomUUID(),
        body: { parameters },
      });
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Invalid published parameters.",
      );
    }
  }
  async function execute(intent: Intent) {
    if (actionLock.current) return;
    actionLock.current = true;
    setWorking(true);
    setError("");
    onActiveChange?.(true);
    try {
      if (intent.kind === "publish") {
        const release = await client.publish(
          projectId,
          flowId,
          intent.body,
          intent.key,
        );
        if (alive.current)
          setReleases((items) => [
            release,
            ...items.filter((item) => item.id !== release.id),
          ]);
      } else {
        const run = await client.createPublishedRun(
          projectId,
          intent.release,
          intent.body,
          intent.key,
        );
        if (alive.current) {
          onRun(run);
          setSelected(null);
        }
      }
      if (alive.current) setPending(null);
      onActiveChange?.(false);
    } catch (cause) {
      const uncertain =
        pending !== null ||
        !(cause instanceof LcaHttpError) ||
        cause.status >= 500;
      if (alive.current) {
        setPending(uncertain ? intent : null);
        setError(
          uncertain
            ? "The outcome is unconfirmed. Retry this same request before making another change."
            : cause instanceof Error
              ? cause.message
              : "Request rejected.",
        );
      }
      if (!uncertain) onActiveChange?.(false);
    } finally {
      actionLock.current = false;
      if (alive.current) setWorking(false);
    }
  }
  return (
    <section
      className="lca-publications"
      aria-label="Published automation versions"
    >
      <h2>Published versions</h2>
      <button
        type="button"
        disabled={loading || working || locked || !!pending}
        onClick={() => setRefresh((value) => value + 1)}
      >
        Refresh published versions
      </button>
      <p>
        Publish a verified snapshot for repeatable execution. Later draft edits
        do not change published versions.
      </p>
      {error && <p role="alert">{error}</p>}
      {pending ? (
        <button
          type="button"
          disabled={working}
          onClick={() => void execute(pending)}
        >
          Retry same {pending.kind} request
        </button>
      ) : (
        <button
          type="button"
          disabled={loading || locked || working || dirty || !verifiedRunId}
          onClick={() =>
            void execute({
              kind: "publish",
              key: crypto.randomUUID(),
              body: {
                expected_version: version,
                verified_run_id: verifiedRunId!,
              },
            })
          }
        >
          Publish verified version
        </button>
      )}
      {!verifiedRunId && (
        <p className="lca-muted">
          Save and successfully replay the current version before publishing.
        </p>
      )}
      {loading ? (
        <p role="status">Loading published versions…</p>
      ) : releases.length === 0 ? (
        <p>No published versions yet.</p>
      ) : (
        <ul>
          {releases.map((release) => (
            <li key={release.id}>
              <strong>Version {release.version}</strong> · {release.created_at}
              <p className="lca-muted">Verified by {release.verified_run_id}</p>
              <button
                type="button"
                disabled={locked || working || !!pending || dirty}
                onClick={() => void review(release)}
              >
                Run published version {release.version}
              </button>
            </li>
          ))}
        </ul>
      )}
      {selected && (
        <fieldset disabled={locked || working || !!pending}>
          <legend>Run published version {selected.version}</legend>
          <p>
            Uses the immutable published steps and parameter schema, not the
            current draft.
          </p>
          {selected.definition.parameters.map((field) => (
            <label key={field.name}>
              Published {field.name} ({field.type})
              <input
                value={values[field.name] ?? ""}
                placeholder={
                  field.default !== undefined
                    ? `Default: ${String(field.default)}`
                    : undefined
                }
                onChange={(event) =>
                  setValues((old) => ({
                    ...old,
                    [field.name]: event.target.value,
                  }))
                }
              />
            </label>
          ))}
          <button type="button" onClick={runSelected}>
            Confirm published replay
          </button>
          <button type="button" onClick={() => setSelected(null)}>
            Cancel published replay
          </button>
        </fieldset>
      )}
    </section>
  );
}
