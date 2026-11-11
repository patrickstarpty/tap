import { useEffect, useRef, useState } from "react";
import type { LcaDefinition } from "./client";
import {
  createRecorderClient,
  RecorderError,
  type RecorderClient,
  type Recording,
  type RecordingFrame,
  type RecordingGrant,
  type RecordingAction,
} from "./recorderClient";
const defaultClient = createRecorderClient();
const frameWaitLimit = 15000;
const frameWaitError =
  "Live frame is still unavailable after 15 seconds. Refresh the live frame to try again.";
const copy = (error: unknown) =>
  error instanceof Error ? error.message : "Recording request unavailable.";
export function RecorderPanel(props: {
  projectId: string;
  flowId: string;
  version: number;
  definition: LcaDefinition;
  dirty?: boolean;
  client?: RecorderClient;
  onImport: (definition: LcaDefinition) => void;
  onActiveChange?: (active: boolean) => void;
}) {
  return <Panel key={`${props.projectId}:${props.flowId}`} {...props} />;
}
function Panel({
  projectId,
  flowId,
  version,
  definition,
  dirty = false,
  client = defaultClient,
  onImport,
  onActiveChange,
}: Parameters<typeof RecorderPanel>[0]) {
  const [history, setHistory] = useState<Recording[]>([]),
    [loaded, setLoaded] = useState(false);
  const [recording, setRecording] = useState<Recording | null>(null),
    [grant, setGrant] = useState<RecordingGrant | null>(null),
    [frame, setFrame] = useState<RecordingFrame | null>(null);
  const [taking, setTaking] = useState(false);
  const takeoverReads = useRef(0);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [unknown, setUnknown] = useState(false),
    [framePaused, setFramePaused] = useState(false),
    [framePending, setFramePending] = useState(false),
    [framePoll, setFramePoll] = useState(0);
  const frameWait = useRef<{ epoch: number; startedAt: number } | null>(null);
  const frameRequest = useRef(0);
  const [operation, setOperation] = useState<RecordingAction["type"]>("click"),
    [selector, setSelector] = useState(""),
    [value, setValue] = useState(""),
    [url, setUrl] = useState("");
  const [point, setPoint] = useState<{ x: number; y: number } | null>(null),
    [log, setLog] = useState<string[]>([]),
    [now, setNow] = useState(Date.now());
  const alive = useRef(true),
    lock = useRef(false),
    epoch = useRef(0),
    baseline = useRef(""),
    startKey = useRef<string | null>(null),
    cancelKey = useRef<string | null>(null),
    stopKey = useRef<{ key: string; epoch: number } | null>(null),
    takeoverKey = useRef<{ key: string; epoch: number } | null>(null),
    grantDeadline = useRef(0);
  const definitionRef = useRef(definition);
  definitionRef.current = definition;
  const notify = useRef(onActiveChange);
  notify.current = onActiveChange;
  const human = grant?.control.control_state === "human_active";
  const active =
    !!recording && !["stopped", "canceled"].includes(recording.status);
  const unresolved = history.some(
    (item) => !["stopped", "canceled"].includes(item.status),
  );
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      notify.current?.(false);
    };
  }, []);
  useEffect(() => {
    notify.current?.(active || unresolved || !loaded || !!startKey.current);
  }, [active, busy, unresolved, loaded]);
  useEffect(() => {
    let current = true;
    void client
      .list(projectId, flowId)
      .then((items) => {
        if (current) {
          setHistory(items);
          setLoaded(true);
        }
      })
      .catch((e) => {
        if (current) setError(copy(e));
      });
    return () => {
      current = false;
    };
  }, [client, projectId, flowId]);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, [active]);
  useEffect(() => {
    if (!active) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [active]);
  const run = async (fn: () => Promise<void>) => {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      if (alive.current) setError(copy(e));
    } finally {
      lock.current = false;
      if (alive.current) setBusy(false);
    }
  };
  const apply = (next: Recording) => {
    if (!alive.current) return;
    setRecording(next);
    if (next.control)
      setGrant((old) => (old ? { ...old, control: next.control! } : old));
    setHistory((old) => [
      next,
      ...old.filter((item) => item.session_id !== next.session_id),
    ]);
  };
  const pauseFrameWait = () => {
    ++frameRequest.current;
    setFramePending(false);
    setFramePaused(true);
    setError(frameWaitError);
  };
  useEffect(() => {
    const waiting = frameWait.current;
    if (
      !active ||
      unknown ||
      !human ||
      !framePending ||
      framePaused ||
      !waiting
    )
      return;
    const timer = window.setTimeout(
      () => {
        if (
          !alive.current ||
          waiting.epoch !== epoch.current ||
          frameWait.current !== waiting
        )
          return;
        pauseFrameWait();
      },
      Math.max(0, waiting.startedAt + frameWaitLimit - Date.now()),
    );
    return () => window.clearTimeout(timer);
  }, [active, unknown, human, framePending, framePaused, framePoll]);
  const loadFrame = async (
    session: string,
    token: string,
    current = epoch.current,
  ) => {
    if (
      !alive.current ||
      current !== epoch.current ||
      Date.now() >= grantDeadline.current
    )
      return;
    if (
      frameWait.current?.epoch === current &&
      Date.now() - frameWait.current.startedAt >= frameWaitLimit
    ) {
      pauseFrameWait();
      return;
    }
    const request = ++frameRequest.current;
    let next: RecordingFrame;
    try {
      next = await client.frame(projectId, flowId, session, token);
    } catch (e) {
      if (
        !alive.current ||
        current !== epoch.current ||
        request !== frameRequest.current
      )
        return;
      if (
        e instanceof RecorderError &&
        e.status === 409 &&
        e.code === "live_frame_pending"
      ) {
        setFrame(null);
        setPoint(null);
        if (Date.now() >= grantDeadline.current) {
          setFramePending(false);
          return;
        }
        if (frameWait.current?.epoch !== current)
          frameWait.current = { epoch: current, startedAt: Date.now() };
        if (Date.now() - frameWait.current.startedAt >= frameWaitLimit) {
          pauseFrameWait();
          return;
        }
        setFramePending(true);
        setFramePaused(false);
        // A pending read has no frame change to rearm the one-shot polling effect.
        setFramePoll((poll) => poll + 1);
        return;
      }
      setFramePending(false);
      throw e;
    }
    if (
      !alive.current ||
      current !== epoch.current ||
      request !== frameRequest.current ||
      Date.now() >= grantDeadline.current
    )
      return;
    if (
      next.media_type !== "image/jpeg" ||
      next.image_base64.length > 700000 ||
      next.viewport.width <= 0 ||
      next.viewport.width > 1920 ||
      next.viewport.height <= 0 ||
      next.viewport.height > 1080
    )
      throw new Error("Live frame is invalid.");
    frameWait.current = null;
    setFramePending(false);
    setFrame(next);
    setPoint(null);
    setFramePaused(false);
  };
  const open = async (next: Recording) => {
    ++epoch.current;
    frameWait.current = null;
    setFramePending(false);
    setFramePaused(false);
    setFrame(null);
    setPoint(null);
    apply(next);
    baseline.current = JSON.stringify(definitionRef.current);
    setUnknown(next.status === "unknown");
    if (["stopped", "canceled", "draining"].includes(next.status)) return;
    const issued = await client.grant(projectId, flowId, next.session_id);
    if (!alive.current) return;
    setGrant(issued);
    grantDeadline.current =
      Date.now() + (issued.expiresInSeconds ?? 300) * 1000;
    if (issued.control.control_state === "human_active")
      await loadFrame(next.session_id, issued.capability);
  };
  const readStatus = async () => {
    if (!recording) return;
    const current = epoch.current;
    const next = await client.get(projectId, flowId, recording.session_id);
    if (!alive.current || current !== epoch.current) return;
    apply(next);
    if (takeoverKey.current && next.control?.control_state === "human_active") {
      if (
        next.control.control_epoch <= takeoverKey.current.epoch ||
        (grant?.controllerId &&
          next.control.controller_id !== grant.controllerId)
      ) {
        setError(
          "Recording control belongs to a different transition. Cancel or refresh this session.",
        );
        takeoverReads.current = 30;
        return;
      }
      takeoverKey.current = null;
      setError("");
      setTaking(false);
      setUnknown(false);
      if (grant) await loadFrame(next.session_id, grant.capability);
    }
    if (["stopped", "canceled"].includes(next.status)) {
      setTaking(false);
      setUnknown(false);
      takeoverKey.current = null;
      setGrant(null);
      setFrame(null);
    } else if (!grant && next.status !== "draining" && !taking)
      await open(next);
  };
  const refresh = () => run(readStatus);
  useEffect(() => {
    if (!taking || busy || !recording || takeoverReads.current >= 30) return;
    const current = epoch.current;
    const timer = window.setTimeout(() => {
      takeoverReads.current++;
      void readStatus()
        .then(() => {
          if (
            alive.current &&
            current === epoch.current &&
            takeoverReads.current >= 30 &&
            takeoverKey.current
          )
            setError(
              "Takeover remains unconfirmed. Refresh recording status or cancel this session.",
            );
        })
        .catch((e) => {
          if (alive.current && current === epoch.current) {
            takeoverReads.current = 30;
            setError(
              `${copy(e)} Takeover is unconfirmed. Refresh recording status; no command was retried.`,
            );
          }
        });
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [taking, busy, recording, grant]);
  useEffect(() => {
    if (
      !recording ||
      !grant ||
      !human ||
      busy ||
      unknown ||
      framePaused ||
      point ||
      recording.status !== "recording" ||
      now >= grantDeadline.current
    )
      return;
    const current = epoch.current;
    const timer = window.setTimeout(() => {
      if (
        document.visibilityState === "hidden" ||
        Date.now() >= grantDeadline.current
      )
        return;
      void loadFrame(recording.session_id, grant.capability, current).catch(
        (e) => {
          if (alive.current && current === epoch.current) {
            setFramePaused(true);
            setError(`${copy(e)} Live updates paused. Refresh the live frame.`);
          }
        },
      );
    }, 1500);
    return () => window.clearTimeout(timer);
  }, [
    recording,
    grant,
    human,
    busy,
    unknown,
    framePaused,
    frame,
    point,
    framePoll,
  ]);
  const start = () =>
    run(async () => {
      if (unresolved || dirty) return;
      startKey.current ??= crypto.randomUUID();
      setUnknown(true);
      const next = await client.start(
        projectId,
        flowId,
        { expected_version: version },
        startKey.current!,
      );
      if (!alive.current) return;
      startKey.current = null;
      await open(next);
    });
  const take = () =>
    run(async () => {
      if (!recording || !grant) return;
      takeoverKey.current ??= {
        key: crypto.randomUUID(),
        epoch: grant.control.control_epoch,
      };
      takeoverReads.current = 0;
      setTaking(true);
      setUnknown(true);
      await client.takeover(
        projectId,
        flowId,
        recording.session_id,
        takeoverKey.current.epoch,
        takeoverKey.current.key,
        grant.capability,
      );
      // Admission is not handover completion. Preserve this grant and command.
      await readStatus();
    });
  const perform = () =>
    run(async () => {
      if (
        !recording ||
        !grant ||
        !frame ||
        unknown ||
        now >= grantDeadline.current
      )
        return;
      const action: RecordingAction = {
        type: operation,
        frame_sequence: frame.frame_sequence,
        viewport: frame.viewport,
      };
      if (operation === "navigate") action.url = url;
      else if (operation === "scroll") {
        action.delta_x = 0;
        action.delta_y = 400;
      } else {
        if (point) {
          action.x = point.x;
          action.y = point.y;
        } else action.selector = selector;
        if (operation === "fill" || operation === "select")
          action.value = value;
      }
      ++epoch.current;
      setUnknown(true);
      setValue("");
      setFrame(null);
      setPoint(null);
      let completed = false;
      try {
        const receipt = await client.action(
          projectId,
          flowId,
          recording.session_id,
          { action, expected_epoch: grant.control.control_epoch },
          crypto.randomUUID(),
          grant.capability,
        );
        if (!alive.current) return;
        setLog((old) => [
          `${operation}: ${receipt.status}${receipt.action_id ? ` (${receipt.action_id})` : ""}`,
          ...old,
        ]);
        if (receipt.status !== "completed") {
          setError(
            "Action outcome unknown or unconfirmed. Cancel the session and review its receipts before importing.",
          );
          return;
        }
        completed = true;
        setUnknown(false);
        await loadFrame(recording.session_id, grant.capability);
      } catch (e) {
        if (alive.current)
          setError(
            completed
              ? `Action completed. Live frame refresh failed. ${copy(e)}`
              : `Action outcome unknown. Do not repeat the action. ${copy(e)}`,
          );
      }
    });
  const cancel = () =>
    run(async () => {
      if (!recording) return;
      cancelKey.current ??= crypto.randomUUID();
      setTaking(false);
      ++epoch.current;
      setUnknown(true);
      setFrame(null);
      setValue("");
      const next = await client.cancel(
        projectId,
        flowId,
        recording.session_id,
        cancelKey.current,
        grant?.capability,
      );
      apply(next);
      if (["stopped", "canceled"].includes(next.status)) {
        setGrant(null);
        setUnknown(false);
      }
    });
  const stop = () =>
    run(async () => {
      if (!recording || !grant) return;
      stopKey.current ??= {
        key: crypto.randomUUID(),
        epoch: grant.control.control_epoch,
      };
      ++epoch.current;
      setUnknown(true);
      setFrame(null);
      const next = await client.stop(
        projectId,
        flowId,
        recording.session_id,
        stopKey.current.epoch,
        stopKey.current.key,
        grant.capability,
      );
      apply(next);
      if (["stopped", "canceled"].includes(next.status)) {
        setGrant(null);
        setUnknown(false);
      }
    });
  const projection = recording?.projection;
  const stale =
    !frame ||
    !Number.isFinite(Date.parse(frame.captured_at)) ||
    now - Date.parse(frame.captured_at) > 4500 ||
    frame.control_epoch !== grant?.control.control_epoch;
  const canAct =
    !busy &&
    !unknown &&
    human &&
    recording?.status === "recording" &&
    !stale &&
    now < grantDeadline.current;
  const changed =
    dirty ||
    baseline.current !== JSON.stringify(definition) ||
    recording?.draft_version !== version;
  return (
    <section className="lca-recorder" aria-label="Live recording">
      <h2>Live recording</h2>
      <p>
        <strong>Recording performs real actions.</strong> Use only owned sites
        and synthetic, non-secret data. Passwords, nested frames and keyboard
        shortcuts are unsupported. Scroll is not a replay step.
      </p>
      {error && <p role="alert">{error}</p>}
      {!recording && (
        <>
          <button
            type="button"
            disabled={!loaded || busy || unresolved || dirty}
            onClick={() => void start()}
          >
            {startKey.current ? "Reconcile recording start" : "Start recording"}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const items = await client.list(projectId, flowId);
                if (!alive.current) return;
                setHistory(items);
                setLoaded(true);
              })
            }
          >
            Refresh recording history
          </button>
          {history.map((item) => (
            <button
              key={item.session_id}
              type="button"
              disabled={busy}
              onClick={() => void run(() => open(item))}
            >
              Open recording {item.session_id}
            </button>
          ))}
        </>
      )}
      {recording && (
        <>
          <p>Recording state: {recording.status}</p>
          <p>Session: {recording.session_id}</p>
          {recording.outcome_unknown && (
            <p>
              Some action outcomes remain unknown. Closing the session does not
              confirm those actions failed or are safe to repeat.
            </p>
          )}
          {["stopped", "canceled"].includes(recording.status) && (
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                ++epoch.current;
                setRecording(null);
                setGrant(null);
                setFrame(null);
                setPoint(null);
                setUnknown(false);
                setLog([]);
                setError("");
                stopKey.current = null;
                cancelKey.current = null;
                takeoverKey.current = null;
                setTaking(false);
              }}
            >
              Back to recording history
            </button>
          )}
          {active && (
            <button type="button" disabled={busy} onClick={() => void cancel()}>
              Cancel recording session
            </button>
          )}
          {active && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void refresh()}
            >
              Refresh recording status
            </button>
          )}
          {active && !human && grant && (
            <button
              type="button"
              disabled={busy || taking || (unknown && !!takeoverKey.current)}
              onClick={() => void take()}
            >
              Take recording control
            </button>
          )}
          {active && (
            <button
              type="button"
              disabled={busy || taking}
              onClick={() =>
                void run(async () => {
                  const issued = await client.grant(
                    projectId,
                    flowId,
                    recording.session_id,
                  );
                  if (!alive.current) return;
                  ++epoch.current;
                  setFrame(null);
                  setPoint(null);
                  setGrant(issued);
                  grantDeadline.current =
                    Date.now() + (issued.expiresInSeconds ?? 300) * 1000;
                })
              }
            >
              Renew recording access
            </button>
          )}
          {active && grant && (
            <button type="button" disabled={busy} onClick={() => void stop()}>
              Stop and drain recording
            </button>
          )}
          {active && human && (
            <>
              <button
                type="button"
                disabled={busy || unknown}
                onClick={() =>
                  void run(async () => {
                    frameWait.current = null;
                    setFramePending(false);
                    setFramePaused(false);
                    await loadFrame(recording.session_id, grant!.capability);
                  })
                }
              >
                Refresh live frame
              </button>
              {framePending && !framePaused && now < grantDeadline.current && (
                <p role="status">
                  Waiting for a fresh live frame. No action is being retried.
                </p>
              )}
              {frame && (
                <img
                  className="lca-live-frame"
                  alt="Live recording browser"
                  src={`data:image/jpeg;base64,${frame.image_base64}`}
                  onClick={(e) => {
                    if (!canAct) return;
                    const rect = e.currentTarget.getBoundingClientRect();
                    setPoint({
                      x: Math.round(
                        ((e.clientX - rect.left) * frame.viewport.width) /
                          rect.width,
                      ),
                      y: Math.round(
                        ((e.clientY - rect.top) * frame.viewport.height) /
                          rect.height,
                      ),
                    });
                  }}
                />
              )}
              <p>
                {point
                  ? `Selected frame position ${point.x}, ${point.y}. No action performed yet.`
                  : "Click the image to select a target, or enter a CSS selector."}
                {stale ? " Live frame unavailable or stale." : ""}
              </p>
              <fieldset disabled={!canAct}>
                <legend>Explicit browser action</legend>
                <label>
                  Recording operation
                  <select
                    value={operation}
                    onChange={(e) =>
                      setOperation(e.target.value as RecordingAction["type"])
                    }
                  >
                    <option value="navigate">Entry navigation</option>
                    <option value="click">Click</option>
                    <option value="fill">Fill</option>
                    <option value="select">Native select</option>
                    <option value="scroll">Scroll down (not replayed)</option>
                  </select>
                </label>
                {operation === "navigate" ? (
                  <label>
                    Entry URL
                    <input
                      value={url}
                      onChange={(e) => setUrl(e.target.value)}
                    />
                  </label>
                ) : (
                  operation !== "scroll" && (
                    <label>
                      Target CSS selector
                      <input
                        value={selector}
                        onChange={(e) => {
                          setSelector(e.target.value);
                          setPoint(null);
                        }}
                      />
                    </label>
                  )
                )}
                {(operation === "fill" || operation === "select") && (
                  <label>
                    Synthetic input value
                    <input
                      autoComplete="off"
                      value={value}
                      onChange={(e) => setValue(e.target.value)}
                    />
                  </label>
                )}
                <button
                  type="button"
                  disabled={
                    !canAct ||
                    (operation === "navigate"
                      ? !url
                      : operation === "scroll"
                        ? false
                        : !point && !selector)
                  }
                  onClick={() => void perform()}
                >
                  Perform action
                </button>
              </fieldset>
            </>
          )}
          {taking && (
            <p>
              Waiting for confirmed human control. Status checks do not retry
              takeover.
            </p>
          )}
          {unknown && !taking && (
            <p>
              Further writes are blocked. Cancel this recording and review its
              receipts.
            </p>
          )}
          <ul aria-label="Acknowledged recording actions">
            {log.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
          {projection?.excluded.map((item) => (
            <p key={item.action_id}>
              Excluded {item.action_id}: {item.reason}
            </p>
          ))}
          {recording.status === "stopped" &&
            projection?.ready &&
            projection.definition && (
              <>
                <p>
                  Recorded draft: {projection.definition.steps.length} steps.
                  Import replaces the editor draft, without saving or replaying.
                </p>
                {changed && (
                  <p>
                    The editor changed since recording began. Preserve or reload
                    those edits before importing; this recording remains saved.
                  </p>
                )}
                <button
                  type="button"
                  disabled={changed || busy}
                  onClick={() => {
                    if (
                      baseline.current !== JSON.stringify(definitionRef.current)
                    )
                      return;
                    onImport(structuredClone(projection.definition!));
                  }}
                >
                  Import recorded draft
                </button>
              </>
            )}
          {recording.status === "draining" && (
            <p>
              Waiting for admitted actions to settle. Refresh recording status;
              nothing is imported yet.
            </p>
          )}
        </>
      )}
    </section>
  );
}
