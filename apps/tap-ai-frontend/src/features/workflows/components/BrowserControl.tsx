import { useEffect, useRef, useState } from "react";

export interface ControlSnapshot {
  control_state:
    | "agent_active"
    | "takeover_pending"
    | "human_active"
    | "handback_pending"
    | "blocked"
    | "closed";
  control_epoch: number;
  controller_kind: "agent" | "human";
  controller_id: string;
  deadline_at: string;
  allowed_controls: string[];
  pending_action_id?: string | null;
  terminal_reason?: string;
}
export interface BrowserFrame {
  frameSequence: number;
  capturedAt: string;
  controlEpoch: number;
  viewport: { width: number; height: number };
  imageDataUrl: string;
}
export interface HumanPageAction {
  type: "click" | "fill" | "scroll";
  frame_sequence: number;
  viewport: { width: number; height: number };
  x: number;
  y: number;
  value?: string;
  delta_x?: number;
  delta_y?: number;
}
export interface HumanActionReceipt {
  status: "completed" | "failed" | "unknown" | "rejected";
}
export interface BrowserPanel {
  capability: string;
  controllerId: string;
  control: ControlSnapshot;
  expiresInSeconds?: number;
}
export interface BrowserControlTransport {
  issue(signal: AbortSignal): Promise<BrowserPanel>;
  state(capability: string, signal: AbortSignal): Promise<ControlSnapshot>;
  control(
    capability: string,
    command: { operation: "takeover" | "handback"; expectedEpoch: number },
    key: string,
    signal: AbortSignal,
  ): Promise<ControlSnapshot>;
  humanAction?(
    capability: string,
    command: { expectedEpoch: number; action: HumanPageAction },
    key: string,
    signal: AbortSignal,
  ): Promise<HumanActionReceipt>;
  frame?(capability: string, signal: AbortSignal): Promise<BrowserFrame | null>;
}

export function BrowserControl({
  transport,
  pollIntervalMs = 1000,
}: {
  transport: BrowserControlTransport;
  pollIntervalMs?: number;
}) {
  const [open, setOpen] = useState(true);
  const [revision, setRevision] = useState(0);
  const [control, setControl] = useState<ControlSnapshot | null>(null);
  const [frame, setFrame] = useState<BrowserFrame | null>(null);
  const [connected, setConnected] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now);
  const [pointX, setPointX] = useState("");
  const [pointY, setPointY] = useState("");
  const [fillValue, setFillValue] = useState("");
  const [actionUnknown, setActionUnknown] = useState(false);
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  const panel = useRef<BrowserPanel | null>(null);
  const lifetime = useRef<AbortController | null>(null);
  const commandBusy = useRef(false);
  const generation = useRef(0);
  const mutation = useRef(0);
  const previousTransport = useRef(transport);

  useEffect(() => {
    if (!open) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    if (previousTransport.current !== transport) {
      panel.current = null;
      previousTransport.current = transport;
      setControl(null);
      setFrame(null);
    }
    setConnected(false);
    const controller = new AbortController();
    lifetime.current = controller;
    const current = ++generation.current;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const alive = () =>
      !controller.signal.aborted && generation.current === current;
    const poll = async () => {
      if (!alive()) return;
      if (commandBusy.current) {
        timer = setTimeout(() => void poll(), pollIntervalMs);
        return;
      }
      const observedMutation = mutation.current;
      const superseded = () => {
        if (!alive()) return true;
        if (observedMutation !== mutation.current) {
          timer = setTimeout(() => void poll(), pollIntervalMs);
          return true;
        }
        return false;
      };
      try {
        let next: ControlSnapshot;
        if (!panel.current) {
          const issued = await transport.issue(controller.signal);
          if (!alive()) return;
          panel.current = issued;
          next = issued.control;
        } else {
          next = await transport.state(
            panel.current.capability,
            controller.signal,
          );
        }
        if (superseded()) return;
        setControl(next);
        if (
          next.control_state !== "human_active" ||
          next.controller_id !== panel.current?.controllerId
        )
          setFillValue("");
        setConnected(true);
        setError(null);
        if (
          next.control_state === "closed" ||
          next.control_state === "blocked" ||
          !Number.isFinite(Date.parse(next.deadline_at)) ||
          Date.parse(next.deadline_at) <= Date.now()
        ) {
          setFrame(null);
          return;
        }
        if (transport.frame) {
          const received = await transport.frame(
            panel.current.capability,
            controller.signal,
          );
          if (superseded()) return;
          if (received === null) {
            setFrame(null);
            timer = setTimeout(() => void poll(), pollIntervalMs);
            return;
          }
          if (
            !/^data:image\/(png|jpeg);base64,[A-Za-z0-9+/=]+$/u.test(
              received.imageDataUrl,
            )
          ) {
            throw new Error("Invalid browser frame");
          }
          setFrame(received);
        }
        timer = setTimeout(() => void poll(), pollIntervalMs);
      } catch (failure) {
        if (superseded()) return;
        if (
          typeof failure === "object" &&
          failure &&
          "status" in failure &&
          (failure.status === 401 || failure.status === 403)
        )
          panel.current = null;
        setConnected(false);
        setError(
          "Browser view disconnected. Control has not changed automatically.",
        );
      }
    };
    void poll();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [open, revision, transport, pollIntervalMs]);

  const expired = control
    ? !Number.isFinite(Date.parse(control.deadline_at)) ||
      Date.parse(control.deadline_at) <= now
    : false;
  const terminal = control?.control_state === "closed" || expired;
  const ours =
    control?.controller_kind === "human" &&
    control.controller_id === panel.current?.controllerId;
  const writable = connected && !pending && !terminal && !actionUnknown;
  const stale =
    !frame ||
    !connected ||
    !control ||
    frame.controlEpoch !== control.control_epoch ||
    now - Date.parse(frame.capturedAt) > 5000 ||
    !Number.isFinite(Date.parse(frame.capturedAt));
  const pageWritable =
    writable &&
    ours &&
    control?.control_state === "human_active" &&
    !stale &&
    !!transport.humanAction;
  const validPoint =
    !!frame &&
    pointX !== "" &&
    pointY !== "" &&
    Number.isFinite(Number(pointX)) &&
    Number.isFinite(Number(pointY)) &&
    Number(pointX) >= 0 &&
    Number(pointY) >= 0 &&
    Number(pointX) < frame.viewport.width &&
    Number(pointY) < frame.viewport.height;
  const humanAction = async (
    type: HumanPageAction["type"],
    point?: { x: number; y: number },
    deltaY?: number,
  ) => {
    if (
      !pageWritable ||
      !frame ||
      !control ||
      !panel.current ||
      !lifetime.current ||
      !transport.humanAction ||
      commandBusy.current
    )
      return;
    const target =
      point ??
      (validPoint
        ? { x: Number(pointX), y: Number(pointY) }
        : type === "scroll"
          ? {
              x: Math.floor(frame.viewport.width / 2),
              y: Math.floor(frame.viewport.height / 2),
            }
          : null);
    if (!target) return;
    commandBusy.current = true;
    mutation.current += 1;
    setPending(true);
    setActionNotice(null);
    const controller = lifetime.current;
    const current = generation.current;
    const action: HumanPageAction = {
      type,
      ...target,
      frame_sequence: frame.frameSequence,
      viewport: frame.viewport,
      ...(type === "fill" ? { value: fillValue } : {}),
      ...(type === "scroll" ? { delta_x: 0, delta_y: deltaY ?? 400 } : {}),
    };
    setFillValue("");
    try {
      const receipt = await transport.humanAction(
        panel.current.capability,
        { expectedEpoch: control.control_epoch, action },
        crypto.randomUUID(),
        controller.signal,
      );
      if (controller.signal.aborted || current !== generation.current) return;
      setFrame(null);
      if (receipt.status === "unknown") {
        setActionUnknown(true);
      } else {
        setActionNotice(
          receipt.status === "completed"
            ? "Action confirmed."
            : receipt.status === "rejected"
              ? "Action rejected. Refresh the page before trying again."
              : "Action failed. Check the refreshed page before another action.",
        );
      }
      setRevision((value) => value + 1);
    } catch {
      if (controller.signal.aborted || current !== generation.current) return;
      // HTTP conflicts and lost responses do not prove the action did not run.
      // Only an acknowledged rejected receipt permits a fresh deliberate action.
      setFrame(null);
      setActionUnknown(true);
      controller.abort();
      setConnected(false);
    } finally {
      commandBusy.current = false;
      setPending(false);
    }
  };
  const command = async (operation: "takeover" | "handback") => {
    if (
      !panel.current ||
      !control ||
      !writable ||
      commandBusy.current ||
      !lifetime.current
    )
      return;
    commandBusy.current = true;
    mutation.current += 1;
    setPending(true);
    setFillValue("");
    const controller = lifetime.current;
    const current = generation.current;
    try {
      const result = await transport.control(
        panel.current.capability,
        { operation, expectedEpoch: control.control_epoch },
        crypto.randomUUID(),
        controller.signal,
      );
      if (!controller.signal.aborted && current === generation.current)
        setControl(result);
    } catch {
      if (!controller.signal.aborted && current === generation.current) {
        controller.abort();
        setConnected(false);
        setError(
          "Control request could not be confirmed. Reconnect to check its outcome; it will not be repeated automatically.",
        );
      }
    } finally {
      commandBusy.current = false;
      setPending(false);
    }
  };
  if (!open)
    return (
      <section aria-label="Live browser">
        <button
          type="button"
          onClick={() => {
            setConnected(false);
            setOpen(true);
          }}
        >
          Open browser view
        </button>
      </section>
    );
  const status = terminal
    ? `Browser session ${control?.terminal_reason ?? (expired ? "expired" : "closed")}`
    : control?.control_state === "takeover_pending"
      ? "Waiting for agent actions to settle"
      : control?.control_state === "handback_pending"
        ? "Refreshing the page before the agent continues"
        : control?.control_state === "human_active"
          ? ours
            ? "You have browser control"
            : "Another panel has control"
          : control?.control_state === "blocked"
            ? "Browser control blocked · outcome needs review"
            : control
              ? "Agent has browser control"
              : "Connecting browser view";
  return (
    <section className="tap-workflow-browser-section" aria-label="Live browser">
      <div className="tap-workflow-actions">
        <h2>Live browser</h2>
        <button
          type="button"
          onClick={() => {
            setFillValue("");
            setConnected(false);
            setOpen(false);
          }}
        >
          Close browser view
        </button>
      </div>
      <p role="status">{status}</p>
      {control ? (
        <p className="tap-workflow-meta">
          Controller: {control.controller_kind} · Deadline:{" "}
          <time dateTime={control.deadline_at}>{control.deadline_at}</time>
        </p>
      ) : null}
      {error ? <p role="alert">{error}</p> : null}
      {actionUnknown ? (
        <p role="alert">Action outcome unknown. Do not repeat it.</p>
      ) : null}
      {actionNotice ? <p role="status">{actionNotice}</p> : null}
      {!connected && error && !terminal ? (
        <button
          type="button"
          onClick={() => {
            setError(null);
            setRevision((value) => value + 1);
          }}
        >
          Reconnect browser view
        </button>
      ) : null}
      {!terminal &&
      control?.control_state === "agent_active" &&
      control.allowed_controls.includes("request_takeover") ? (
        <button
          type="button"
          disabled={!writable}
          onClick={() => void command("takeover")}
        >
          Request control
        </button>
      ) : null}
      {!terminal &&
      ours &&
      control?.control_state === "human_active" &&
      control.allowed_controls.includes("hand_back") ? (
        <button
          type="button"
          disabled={!writable}
          onClick={() => void command("handback")}
        >
          Hand back to agent
        </button>
      ) : null}
      {!terminal && frame ? (
        <figure>
          {ours && transport.humanAction ? (
            <button
              type="button"
              aria-label="Click browser page"
              disabled={!pageWritable}
              style={{
                padding: 0,
                border: 0,
                display: "block",
                maxWidth: "100%",
              }}
              onClick={(event) => {
                if (event.detail === 0) {
                  if (validPoint) void humanAction("click");
                  return;
                }
                const image = event.currentTarget.querySelector("img");
                const rect = image?.getBoundingClientRect();
                if (!rect || !rect.width || !rect.height) return;
                const x = Math.floor(
                  ((event.clientX - rect.left) * frame.viewport.width) /
                    rect.width,
                );
                const y = Math.floor(
                  ((event.clientY - rect.top) * frame.viewport.height) /
                    rect.height,
                );
                if (
                  x < 0 ||
                  y < 0 ||
                  x >= frame.viewport.width ||
                  y >= frame.viewport.height
                )
                  return;
                setPointX(String(x));
                setPointY(String(y));
                void humanAction("click", { x, y });
              }}
            >
              <img
                src={frame.imageDataUrl}
                alt="Browser page, last received frame"
                style={{ maxWidth: "100%", height: "auto", display: "block" }}
              />
            </button>
          ) : (
            <img
              src={frame.imageDataUrl}
              alt="Browser page, last received frame"
              style={{ maxWidth: "100%", height: "auto" }}
            />
          )}
          <figcaption>
            <time dateTime={frame.capturedAt}>{frame.capturedAt}</time> · Frame{" "}
            {frame.frameSequence}
            <br />
            <span>
              {stale
                ? "Stale frame · interaction disabled"
                : "Latest received frame"}
            </span>
          </figcaption>
        </figure>
      ) : !terminal ? (
        <p>Live frame unavailable. No page interaction is enabled.</p>
      ) : null}
      {!terminal && ours && transport.humanAction ? (
        <fieldset
          disabled={!pageWritable}
          style={{ display: "grid", gap: 8, minWidth: 0, marginTop: 12 }}
        >
          <legend>Page interaction</legend>
          <p>
            Click the image, or choose a point below. Synthetic non-secret
            values only; password fields are not supported.
          </p>
          <div className="tap-workflow-actions" style={{ alignItems: "end" }}>
            <label style={{ display: "grid", gap: 4 }}>
              Page X
              <input
                type="number"
                style={{
                  border: "1px solid var(--tap-line)",
                  borderRadius: 6,
                  padding: "6px 8px",
                  width: 90,
                }}
                min="0"
                max={frame ? frame.viewport.width - 1 : undefined}
                value={pointX}
                onChange={(event) => setPointX(event.target.value)}
              />
            </label>
            <label style={{ display: "grid", gap: 4 }}>
              Page Y
              <input
                type="number"
                style={{
                  border: "1px solid var(--tap-line)",
                  borderRadius: 6,
                  padding: "6px 8px",
                  width: 90,
                }}
                min="0"
                max={frame ? frame.viewport.height - 1 : undefined}
                value={pointY}
                onChange={(event) => setPointY(event.target.value)}
              />
            </label>
            <button
              type="button"
              disabled={!pageWritable || !validPoint}
              onClick={() => void humanAction("click")}
            >
              Click point
            </button>
          </div>
          <label style={{ display: "grid", gap: 4 }}>
            Text to fill
            <input
              style={{
                border: "1px solid var(--tap-line)",
                borderRadius: 6,
                padding: "6px 8px",
                width: "100%",
                minWidth: 0,
              }}
              autoComplete="off"
              maxLength={4000}
              value={fillValue}
              onChange={(event) => setFillValue(event.target.value)}
            />
          </label>
          <div className="tap-workflow-actions">
            <button
              type="button"
              disabled={!pageWritable || !validPoint}
              onClick={() => void humanAction("fill")}
            >
              Fill point
            </button>
            <button
              type="button"
              disabled={!pageWritable}
              onClick={() => void humanAction("scroll", undefined, -400)}
            >
              Scroll up
            </button>
            <button
              type="button"
              disabled={!pageWritable}
              onClick={() => void humanAction("scroll", undefined, 400)}
            >
              Scroll down
            </button>
          </div>
        </fieldset>
      ) : null}
      {!terminal ? (
        <p className="tap-workflow-meta">
          Closing this view does not cancel the task or hand back control.
        </p>
      ) : null}
    </section>
  );
}
