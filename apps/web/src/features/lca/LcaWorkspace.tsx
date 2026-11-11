import { useEffect, useRef, useState } from "react";
import {
  createLcaClient,
  LcaHttpError,
  type LcaClient,
  type LcaDefinition,
  type LcaFlow,
  type LcaRun,
  type RunInput,
  type Scalar,
} from "./client";
import { AgentDraftImport } from "./AgentDraftImport";
import { BddResults } from "./BddResults";
import { groupsFor } from "./bdd";
import { DefinitionEditor, parseParameterValue } from "./DefinitionEditor";
import "./lca.css";
import { PublishedVersions } from "./PublishedVersions";
import { RecorderPanel } from "./RecorderPanel";
import type { RecorderClient } from "./recorderClient";

// Display-only sanitization. Downloaded evidence is left byte-for-byte unchanged.
const diagnosticText = (value: string) =>
  value
    .replace(/\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)/g, "")
    .replace(/(?:\u001b\[|\u009b)[0-?]*[ -/]*[@-~]/g, "")
    .replace(/[\u0000-\u0008\u000b-\u001f\u007f-\u009f]/g, "")
    .slice(0, 1000);
const defaultClient = createLcaClient();
const blank = (): LcaDefinition => ({
  schema_version: "lca-debug-v1",
  allowed_origins: [""],
  targets: [],
  parameters: [],
  steps: [
    {
      id: "open",
      operation: "navigate",
      arguments: { url: "" },
      timeout_ms: 10000,
      provenance: { source: "manual" },
    },
  ],
});
const errorCopy = (error: unknown) =>
  error instanceof LcaHttpError
    ? error.message
    : "The request could not be confirmed. Your edits are preserved. Reconcile an uncertain replay before starting another.";
const uncertain = (error: unknown) =>
  !(error instanceof LcaHttpError) || error.status >= 500;
const terminal = new Set(["passed", "failed", "canceled", "unknown"]);
interface ReplayIntent {
  flowId: string;
  body: RunInput;
  key: string;
}
interface CreateIntent {
  body: { title: string; definition: LcaDefinition };
  key: string;
}

/** Remount project-local state so late responses cannot leak across project switches. */
export function LcaWorkspace({
  projectId,
  client = defaultClient,
  recorderClient,
}: {
  projectId: string;
  client?: LcaClient;
  recorderClient?: RecorderClient;
}) {
  return (
    <ProjectWorkspace
      key={projectId}
      projectId={projectId}
      client={client}
      recorderClient={recorderClient}
    />
  );
}

function ProjectWorkspace({
  projectId,
  client,
  recorderClient,
}: {
  projectId: string;
  client: LcaClient;
  recorderClient?: RecorderClient;
}) {
  const [selectedBddId, setSelectedBddId] = useState<string>();
  const [recordingActive, setRecordingActive] = useState(false);
  const [publicationActive, setPublicationActive] = useState(false);
  const [flows, setFlows] = useState<LcaFlow[]>([]);
  const [flow, setFlow] = useState<LcaFlow | null>(null);
  const [definition, setDefinition] = useState<LcaDefinition | null>(null);
  const [title, setTitle] = useState("");
  const [historyReady, setHistoryReady] = useState(false);
  const [runs, setRuns] = useState<LcaRun[]>([]);
  const [run, setRun] = useState<LcaRun | null>(null);
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const [discard, setDiscard] = useState(false);
  const [replayIntent, setReplayIntent] = useState<ReplayIntent | null>(null);
  const [createIntent, setCreateIntent] = useState<CreateIntent | null>(null);
  const alive = useRef(true);
  const lock = useRef(false);
  const generation = useRef(0);
  const runGeneration = useRef(0);
  const dirty =
    definition !== null &&
    (!flow || JSON.stringify(definition) !== JSON.stringify(flow.definition));
  const unresolved = runs.some((item) => item.execution_id === null);
  const selectedFlow = useRef<string | null>(null);
  const locked =
    recordingActive ||
    publicationActive ||
    busy ||
    replayIntent !== null ||
    createIntent !== null;

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    let current = true;
    setLoading(true);
    setError("");
    void client
      .listFlows(projectId)
      .then((next) => {
        if (current) setFlows(next);
      })
      .catch((cause) => {
        if (current) setError(errorCopy(cause));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [client, projectId]);
  useEffect(() => {
    if (!dirty && !locked) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, locked]);
  useEffect(() => {
    if (!run || (run.execution && terminal.has(run.execution.status))) return;
    const id = run.id;
    const current = runGeneration.current;
    const flowId = selectedFlow.current;
    const flowGeneration = generation.current;
    const timer = window.setTimeout(() => {
      void client
        .getRun(projectId, id)
        .then((next) => {
          if (
            alive.current &&
            current === runGeneration.current &&
            flowGeneration === generation.current &&
            next.flow_id === flowId
          )
            setRun(next);
        })
        .catch((cause) => {
          if (alive.current && current === runGeneration.current)
            setError(
              `${errorCopy(cause)} Automatic receipt checks paused. Use Refresh run receipt to resume.`,
            );
        });
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [client, projectId, run]);

  const begin = () => {
    if (lock.current) return false;
    lock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    return true;
  };
  const end = () => {
    lock.current = false;
    if (alive.current) setBusy(false);
  };
  const install = (next: LcaFlow) => {
    selectedFlow.current = next.id;
    setFlow(next);
    setDefinition(structuredClone(next.definition));
    setTitle(next.title);
    setInputs({});
    setConflict(false);
    setDiscard(false);
    setFlows((old) => [next, ...old.filter((f) => f.id !== next.id)]);
  };
  const openFlow = async (id: string, discardEdits = false) => {
    if (!discardEdits && dirty) {
      setError(
        "Save or explicitly discard your current edits before opening another flow.",
      );
      return;
    }
    if (!begin()) return;
    const current = ++generation.current;
    ++runGeneration.current;
    selectedFlow.current = id;
    setHistoryReady(false);
    setRuns([]);
    setRun(null);
    try {
      const [next, history] = await Promise.all([
        client.getFlow(projectId, id),
        client.listRuns(projectId, id),
      ]);
      if (alive.current && current === generation.current) {
        install(next);
        setRuns(history);
        setHistoryReady(true);
      }
    } catch (cause) {
      if (alive.current) setError(errorCopy(cause));
    } finally {
      end();
    }
  };
  const save = async () => {
    if (!definition || conflict || !begin()) return;
    const intent = createIntent || {
      key: crypto.randomUUID(),
      body: { title: title.trim(), definition: structuredClone(definition) },
    };
    try {
      const next = flow
        ? await client.saveDraft(projectId, flow.id, {
            expected_version: flow.version,
            definition,
          })
        : await client.createFlow(projectId, intent.body, intent.key);
      if (alive.current) {
        install(next);
        if (!flow) setHistoryReady(true);
        setCreateIntent(null);
        setNotice(
          `Draft saved as version ${next.version}. Saving did not start a replay.`,
        );
      }
    } catch (cause) {
      if (alive.current) {
        setError(errorCopy(cause));
        if (cause instanceof LcaHttpError && cause.status === 409)
          setConflict(true);
        if (!flow) setCreateIntent(uncertain(cause) ? intent : null);
      }
    } finally {
      end();
    }
  };
  const loadRun = async (id: string) => {
    const current = ++runGeneration.current;
    const flowId = selectedFlow.current;
    const flowGeneration = generation.current;
    setRun(null);
    setError("");
    try {
      const next = await client.getRun(projectId, id);
      if (
        alive.current &&
        current === runGeneration.current &&
        flowGeneration === generation.current &&
        next.flow_id === flowId
      )
        setRun(next);
    } catch (cause) {
      if (alive.current && current === runGeneration.current)
        setError(errorCopy(cause));
    }
  };
  const replay = async () => {
    if (
      !flow ||
      !definition ||
      dirty ||
      conflict ||
      !historyReady ||
      (unresolved && !replayIntent) ||
      !begin()
    )
      return;
    let intent = replayIntent;
    try {
      if (!intent) {
        const parameters: Record<string, Scalar> = {};
        for (const parameter of definition.parameters) {
          if (Object.hasOwn(inputs, parameter.name))
            parameters[parameter.name] = parseParameterValue(
              parameter.type,
              inputs[parameter.name],
            );
          else if (parameter.required && parameter.default === undefined)
            throw new Error(`Enter parameter ${parameter.name}.`);
        }
        intent = {
          key: crypto.randomUUID(),
          flowId: flow.id,
          body: { expected_version: flow.version, parameters },
        };
      }
      const next = await client.createRun(
        projectId,
        intent.flowId,
        intent.body,
        intent.key,
      );
      if (alive.current) {
        setReplayIntent(null);
        setRuns((old) => [next, ...old.filter((r) => r.id !== next.id)]);
        await loadRun(next.id);
      }
    } catch (cause) {
      if (alive.current) {
        setError(
          intent
            ? errorCopy(cause)
            : cause instanceof Error
              ? cause.message
              : "Invalid replay parameters.",
        );
        if (intent) setReplayIntent(uncertain(cause) ? intent : null);
        if (cause instanceof LcaHttpError && cause.status === 409)
          setConflict(true);
      }
    } finally {
      end();
    }
  };
  const reconcile = async (id: string) => {
    if (!begin()) return;
    const current = generation.current;
    const flowId = selectedFlow.current;
    try {
      const next = await client.reconcileRun(projectId, id);
      if (
        alive.current &&
        current === generation.current &&
        next.flow_id === flowId
      ) {
        setRuns((old) =>
          old.map((item) => (item.id === next.id ? next : item)),
        );
        await loadRun(next.id);
      }
    } catch (cause) {
      if (alive.current && current === generation.current)
        setError(errorCopy(cause));
    } finally {
      end();
    }
  };
  return (
    <section className="lca-workspace" aria-label="Real debug flows">
      <header>
        <div>
          <h1>Low Code Automation</h1>
          <p>
            Record a browser flow, edit its steps, verify the result, and
            publish a version for repeatable execution.
          </p>
        </div>
        <button
          type="button"
          disabled={loading || locked || dirty}
          onClick={() => {
            ++generation.current;
            ++runGeneration.current;
            selectedFlow.current = null;
            setFlow(null);
            setDefinition(blank());
            setTitle("");
            setRuns([]);
            setRun(null);
            setInputs({});
            setError("");
            setNotice("");
            setConflict(false);
          }}
        >
          New debug flow
        </button>
      </header>
      <AgentDraftImport
        projectId={projectId}
        disabled={loading || locked || dirty}
        onLoad={(next, nextTitle) => {
          ++generation.current;
          ++runGeneration.current;
          selectedFlow.current = null;
          setFlow(null);
          setDefinition(next);
          setTitle(nextTitle);
          setRuns([]);
          setRun(null);
          setInputs({});
          setError("");
          setConflict(false);
          setNotice(
            "AA actions loaded as an unsaved draft. Review targets, save, and explicitly verify this new flow before publishing.",
          );
        }}
      />
      <details className="lca-recording-disclosure">
        <summary>Browser recording · append actions to a BDD step</summary>
        {flow && definition ? (
          <RecorderPanel
            projectId={projectId}
            flowId={flow.id}
            version={flow.version}
            definition={definition}
            dirty={dirty}
            importGroupId={
              groupsFor(definition).find((g) => g.id === selectedBddId)?.id ??
              groupsFor(definition)[0].id
            }
            client={recorderClient}
            onActiveChange={setRecordingActive}
            onImport={(next) => {
              setDefinition(next);
              setNotice(
                "Recorded actions appended to the selected BDD step. Review targets and save explicitly. Nothing was replayed.",
              );
            }}
          />
        ) : (
          <aside className="lca-recording">
            <strong>
              Save or open a debug flow to connect shared browser recording.
            </strong>
            <p>
              Recording performs real actions. This editor does not simulate
              recording or execute changes as you type.
            </p>
          </aside>
        )}
      </details>
      {error && (
        <div role="alert" className="lca-error">
          {error}
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
      {replayIntent && (
        <div className="lca-error">
          <p>
            Replay admission is uncertain. Reconcile the same frozen request; do
            not create another.
          </p>
          <button type="button" disabled={busy} onClick={() => void replay()}>
            Reconcile replay request
          </button>
        </div>
      )}
      {createIntent && (
        <button type="button" disabled={busy} onClick={() => void save()}>
          Reconcile flow creation
        </button>
      )}
      <div className="lca-layout">
        <nav aria-label="Saved debug flows">
          <h2>Saved flows</h2>
          {loading ? (
            <p role="status">Loading flows…</p>
          ) : flows.length === 0 ? (
            <p>No saved flows loaded.</p>
          ) : (
            flows.map((item) => (
              <button
                type="button"
                key={item.id}
                aria-current={flow?.id === item.id ? "page" : undefined}
                disabled={locked}
                onClick={() => void openFlow(item.id)}
              >
                {item.title}
              </button>
            ))
          )}
        </nav>
        <main>
          {definition ? (
            <>
              <div className="lca-editor-heading">
                <label>
                  Flow title
                  <input
                    maxLength={256}
                    value={title}
                    disabled={!!flow || locked}
                    onChange={(e) => setTitle(e.target.value)}
                  />
                </label>
                <span>
                  {flow ? `Saved version ${flow.version}` : "Unsaved flow"} ·{" "}
                  {dirty ? "Unsaved edits" : "Saved"}
                </span>
              </div>
              <div className="lca-actions">
                <button
                  type="button"
                  disabled={locked || conflict || !dirty || !title.trim()}
                  onClick={() => void save()}
                >
                  Save draft
                </button>
                {flow && (
                  <button
                    type="button"
                    disabled={locked}
                    onClick={() => setDiscard(true)}
                  >
                    Reload saved draft
                  </button>
                )}
              </div>
              <DefinitionEditor
                value={definition}
                selectedGroupId={selectedBddId}
                onSelectGroup={setSelectedBddId}
                disabled={locked}
                onChange={(next) => {
                  setDefinition(next);
                  setNotice("");
                }}
              />
              {discard && (
                <div role="group" aria-label="Discard draft changes">
                  <p>Discard unsaved edits and reload the server version?</p>
                  <button
                    type="button"
                    onClick={() => flow && void openFlow(flow.id, true)}
                  >
                    Discard edits and reload
                  </button>
                  <button type="button" onClick={() => setDiscard(false)}>
                    Keep editing
                  </button>
                </div>
              )}
              <section className="lca-replay">
                <h2>Debug replay</h2>
                <p>
                  Explicit replay starts a fresh isolated browser. Recording
                  cookies are not copied. Use only owned sites and synthetic
                  non-secret data.
                </p>
                <fieldset disabled={locked}>
                  <legend>Replay parameters</legend>
                  {definition.parameters.length === 0 ? (
                    <p>No parameters declared.</p>
                  ) : (
                    definition.parameters.map((parameter) => (
                      <label key={parameter.name}>
                        {parameter.name} ({parameter.type}
                        {parameter.required ? ", required" : ""})
                        <input
                          value={inputs[parameter.name] ?? ""}
                          placeholder={
                            parameter.default !== undefined
                              ? `Default: ${String(parameter.default)}`
                              : ""
                          }
                          onChange={(e) =>
                            setInputs((old) => ({
                              ...old,
                              [parameter.name]: e.target.value,
                            }))
                          }
                        />
                        <button
                          type="button"
                          onClick={() =>
                            setInputs((old) => {
                              const next = { ...old };
                              delete next[parameter.name];
                              return next;
                            })
                          }
                        >
                          Use default / omit {parameter.name}
                        </button>
                      </label>
                    ))
                  )}
                </fieldset>
                <button
                  type="button"
                  disabled={
                    !flow ||
                    definition.bdd_steps?.some(
                      (group) => group.action_ids.length === 0,
                    ) ||
                    dirty ||
                    locked ||
                    conflict ||
                    unresolved ||
                    !historyReady ||
                    (!!run?.execution && !terminal.has(run.execution.status))
                  }
                  onClick={() => void replay()}
                >
                  Debug replay
                </button>
                {definition.bdd_steps?.some(
                  (group) => group.action_ids.length === 0,
                ) && <p>Add actions to empty BDD steps before replay.</p>}
                {dirty && <p>Save the draft before replay.</p>}
                {unresolved && (
                  <p>
                    Reconcile the existing unlinked run before starting a new
                    replay.
                  </p>
                )}
              </section>
              {flow && (
                <PublishedVersions
                  key={flow.id}
                  client={client}
                  projectId={projectId}
                  flowId={flow.id}
                  version={flow.version}
                  dirty={dirty}
                  locked={
                    locked ||
                    !historyReady ||
                    unresolved ||
                    (!!run?.execution && !terminal.has(run.execution.status))
                  }
                  verifiedRunId={
                    run?.execution?.status === "passed" &&
                    run.definition_snapshot?.version === flow.version &&
                    run.canonical_results?.length &&
                    run.canonical_results.every(
                      (result) => result.status === "passed",
                    )
                      ? run.id
                      : undefined
                  }
                  onActiveChange={setPublicationActive}
                  onRun={(next) => {
                    setRuns((old) => [
                      next,
                      ...old.filter((item) => item.id !== next.id),
                    ]);
                    setRun(next);
                  }}
                />
              )}
              <section>
                <h2>Run history</h2>
                <p>
                  Each run refers to its frozen snapshot, not the current
                  editor.
                </p>
                {runs.length === 0 ? (
                  <p>No debug runs loaded.</p>
                ) : (
                  <ul>
                    {runs.map((item) => (
                      <li key={item.id}>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void loadRun(item.id)}
                        >
                          {item.id}
                        </button>{" "}
                        · {item.classification} ·{" "}
                        {item.created_at || "Time unavailable"}
                        {item.execution_id === null && (
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => void reconcile(item.id)}
                          >
                            Reconcile existing run {item.id}
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                {run && (
                  <RunDetails
                    key={run.id}
                    client={client}
                    projectId={projectId}
                    run={run}
                    onRefresh={() => void loadRun(run.id)}
                  />
                )}
              </section>
            </>
          ) : (
            <p className="lca-empty">
              Open a saved flow or create a debug flow to edit its steps.
            </p>
          )}
        </main>
      </div>
    </section>
  );
}

function RunDetails({
  run,
  onRefresh,
  client,
  projectId,
}: {
  run: LcaRun;
  onRefresh(): void;
  client: LcaClient;
  projectId: string;
}) {
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);
  const actionLock = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const act = async (operation: () => Promise<void>) => {
    if (actionLock.current) return;
    actionLock.current = true;
    setBusy(true);
    setError("");
    try {
      await operation();
    } catch (cause) {
      if (mounted.current) setError(errorCopy(cause));
    } finally {
      actionLock.current = false;
      if (mounted.current) setBusy(false);
    }
  };
  const cancel = () =>
    act(async () => {
      await client.cancelRun(projectId, run.id);
      if (mounted.current)
        setNotice(
          "Cancellation requested. Waiting for the execution receipt to confirm whether it stopped.",
        );
    });
  const download = (id: string, kind: string) =>
    act(async () => {
      const blob = await client.getEvidence(projectId, run.id, id);
      if (!mounted.current) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `debug-evidence.${kind === "screenshot" ? "png" : kind === "trace" ? "zip" : kind === "video" ? "webm" : "json"}`;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  return (
    <article className="lca-run" aria-label={`Run ${run.id}`}>
      <h3>Run {run.id}</h3>
      <dl>
        <dt>Classification</dt>
        <dd>
          {run.classification === "published"
            ? "Published version"
            : "Debug only"}
        </dd>
        <dt>Frozen snapshot</dt>
        <dd>{run.snapshot_id}</dd>
        <dt>Definition digest</dt>
        <dd>{run.definition_digest}</dd>
        <dt>Input digest</dt>
        <dd>{run.input_digest}</dd>
        <dt>Execution</dt>
        <dd>{run.execution?.status || "Awaiting execution receipt"}</dd>
      </dl>
      {run.execution?.status === "unknown" && (
        <p className="lca-error">
          Outcome unknown. Do not automatically repeat this run.
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {run.execution_id &&
        run.execution &&
        !terminal.has(run.execution.status) && (
          <button type="button" disabled={busy} onClick={() => void cancel()}>
            Request cancellation
          </button>
        )}
      {run.execution?.failure_code && (
        <p>Failure: {run.execution.failure_code}</p>
      )}
      <BddResults run={run} />
      <h4>Canonical step results</h4>
      {run.canonical_results ? (
        <ol>
          {run.canonical_results.map((step) => (
            <li key={step.step_id}>
              {step.step_id} · {step.status}
            </li>
          ))}
        </ol>
      ) : (
        <p>
          Canonical results unavailable.{" "}
          {run.canonical_results_error ||
            "No validated step result attachment."}
        </p>
      )}
      <h4>Runtime diagnostics (not canonical step results)</h4>
      {run.execution?.steps?.length ? (
        <ol>
          {run.execution.steps.map((step, index) => (
            <li key={index}>
              <strong>
                {step.canonical_step_id ||
                  step.title ||
                  "Unmapped execution step"}
              </strong>{" "}
              · {step.status}
              {step.error && (
                <p className="lca-diagnostic" data-testid="lca-diagnostic">
                  {diagnosticText(step.error)}
                </p>
              )}
            </li>
          ))}
        </ol>
      ) : (
        <p>No step results available.</p>
      )}
      {run.execution?.evidence?.length ? (
        <ul>
          {run.execution.evidence.map((item) => (
            <li key={item.object_ref}>
              {item.kind}: {item.object_ref}{" "}
              <button
                type="button"
                disabled={busy}
                onClick={() => void download(item.object_ref, item.kind)}
              >
                Download {item.kind}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p>No evidence available.</p>
      )}
      {run.definition_snapshot && (
        <details>
          <summary>Frozen run definition</summary>
          <p>
            Version {run.definition_snapshot.version} · Generator{" "}
            {run.definition_snapshot.generator_version}
          </p>
          <pre>
            {JSON.stringify(run.definition_snapshot.definition, null, 2)}
          </pre>
        </details>
      )}
      <button type="button" onClick={onRefresh}>
        Refresh run receipt
      </button>
    </article>
  );
}
