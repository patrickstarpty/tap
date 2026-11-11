import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  assistantSchema,
  boundedId,
  isAgentSourceReference,
  record,
  sameAssistantContext,
  tapperEmbedUrl,
  type AgentSourceReference,
  type LcaAssistantContext,
} from "./assistantProtocol";

/** The iframe can request source review, but can never write or execute an LCA flow. */
export function LcaAssistantPanel({
  context: providedContext,
  onSourceChange,
  onReconnect,
  reconnectRevision = 0,
  reconnecting = false,
  reconnectError = "",
}: {
  context: LcaAssistantContext;
  onSourceChange: (source: AgentSourceReference | null) => void;
  onReconnect?: () => void;
  reconnectRevision?: number;
  reconnecting?: boolean;
  reconnectError?: string;
}) {
  const {
    project_id,
    actor_id,
    identity_mode,
    gateway_instance_id,
    context_binding,
  } = providedContext;
  const context = useMemo(
    () => ({
      project_id,
      actor_id,
      identity_mode,
      gateway_instance_id,
      context_binding,
    }),
    [project_id, actor_id, identity_mode, gateway_instance_id, context_binding],
  );
  const sourceChange = useRef(onSourceChange);
  sourceChange.current = onSourceChange;
  const reconnectHandler = useRef(onReconnect);
  reconnectHandler.current = onReconnect;
  const authorityBlocked = useRef(false);
  authorityBlocked.current = reconnecting || Boolean(reconnectError);
  const url = tapperEmbedUrl();
  const origin = url?.origin;
  const frame = useRef<HTMLIFrameElement>(null);
  const loaded = useRef(false);
  const nonce = useRef("");
  const ready = useRef(false);
  const requests = useRef(new Set<string>());
  const connectionTimer = useRef<number | undefined>(undefined);
  const handshakeRetry = useRef<number | undefined>(undefined);
  const [open, setOpen] = useState(true);
  const [status, setStatus] = useState<"connecting" | "ready" | "unavailable">(
    "connecting",
  );
  const disconnect = useCallback(() => {
    window.clearTimeout(connectionTimer.current);
    window.clearInterval(handshakeRetry.current);
    ready.current = false;
    nonce.current = "";
    sourceChange.current(null);
    setStatus("unavailable");
  }, []);
  const awaitConnection = useCallback(() => {
    window.clearTimeout(connectionTimer.current);
    setStatus("connecting");
    connectionTimer.current = window.setTimeout(disconnect, 10000);
  }, [disconnect]);
  const initialize = useCallback(() => {
    if (authorityBlocked.current) {
      disconnect();
      return;
    }
    ready.current = false;
    requests.current.clear();
    nonce.current = crypto.randomUUID();
    sourceChange.current(null);
    awaitConnection();
    window.clearInterval(handshakeRetry.current);
    const message = {
      type: "lca:init",
      schema_version: assistantSchema,
      nonce: nonce.current,
      context,
    };
    const sendInit = () => {
      if (ready.current || authorityBlocked.current || !nonce.current) return;
      if (origin && frame.current?.isConnected)
        frame.current.contentWindow?.postMessage(message, origin);
    };
    // iframe load can precede the child's React effect listener. Retry the same
    // nonce/context only until verified readiness or the existing 10s timeout.
    handshakeRetry.current = window.setInterval(sendInit, 250);
    sendInit();
  }, [context, origin, awaitConnection, disconnect]);
  useEffect(() => {
    // A changed authority requires a fresh child document, not a second nonce
    // accepted by an already verified child. Cloned props leave this effect intact.
    if (loaded.current && frame.current && origin) {
      loaded.current = false;
      sourceChange.current(null);
      frame.current.src = new URL("/embed/lca", origin).href;
    }
    if (origin) awaitConnection();
    function receive(event: MessageEvent<unknown>) {
      const data = event.data;
      if (
        authorityBlocked.current ||
        !frame.current?.isConnected ||
        !origin ||
        event.origin !== origin ||
        event.source !== frame.current.contentWindow ||
        !record(data) ||
        data.schema_version !== assistantSchema ||
        !nonce.current ||
        data.nonce !== nonce.current ||
        !sameAssistantContext(data.context, context)
      )
        return;
      if (data.type === "lca:ready" && Object.keys(data).length === 4) {
        ready.current = true;
        window.clearInterval(handshakeRetry.current);
        window.clearTimeout(connectionTimer.current);
        setStatus("ready");
      } else if (
        data.type === "lca:review" &&
        Object.keys(data).length === 6 &&
        ready.current &&
        boundedId(data.request_id) &&
        isAgentSourceReference(data.source) &&
        !requests.current.has(data.request_id)
      ) {
        requests.current.add(data.request_id);
        sourceChange.current(data.source);
      }
    }
    const element = frame.current;
    element?.addEventListener("error", disconnect);
    window.addEventListener("message", receive);
    return () => {
      window.clearTimeout(connectionTimer.current);
      window.clearInterval(handshakeRetry.current);
      element?.removeEventListener("error", disconnect);
      window.removeEventListener("message", receive);
      ready.current = false;
      nonce.current = "";
    };
  }, [
    context,
    initialize,
    origin,
    awaitConnection,
    disconnect,
    reconnectRevision,
  ]);
  useEffect(() => {
    if (reconnecting || reconnectError) disconnect();
  }, [reconnecting, reconnectError, disconnect]);
  return (
    <div className={`lca-assistant-panel ${open ? "is-open" : "is-collapsed"}`}>
      <button
        type="button"
        className="lca-assistant-toggle"
        aria-expanded={open}
        aria-controls="lca-tapper-panel"
        onClick={() => setOpen(!open)}
      >
        {open ? "Hide Tapper" : "Open Tapper"}
      </button>
      <aside id="lca-tapper-panel" aria-label="Tapper assistant" hidden={!open}>
        <header>
          <strong>Tapper</strong>
          <span role="status">
            {reconnecting
              ? "Refreshing authorized context…"
              : status === "ready"
                ? "Tapper connected"
                : status === "unavailable"
                  ? "Tapper disconnected"
                  : "Waiting for verified connection…"}
          </span>
        </header>
        <p>
          Describe your goal and send it to Tapper to plan BDD steps and execute
          AA. Review the result here before creating an unsaved LCA draft.
        </p>
        {!url ||
        reconnectError ||
        (status === "unavailable" && !reconnecting) ? (
          <p role="alert">
            {!url
              ? "Tapper URL configuration is invalid."
              : reconnectError ||
                "Tapper is unavailable. Check the Tapper service and reconnect the assistant."}
          </p>
        ) : null}
        {url ? (
          <button
            type="button"
            className="lca-assistant-toggle"
            disabled={reconnecting}
            onClick={() => {
              disconnect();
              if (reconnectHandler.current) {
                reconnectHandler.current();
                return;
              }
              ready.current = false;
              nonce.current = "";
              loaded.current = false;
              sourceChange.current(null);
              awaitConnection();
              if (frame.current) frame.current.src = url.href;
            }}
          >
            Reconnect Tapper
          </button>
        ) : null}
        {url ? (
          <iframe
            ref={frame}
            src={url.href}
            title="Tapper LCA assistant"
            inert={reconnecting || Boolean(reconnectError)}
            referrerPolicy="no-referrer"
            sandbox="allow-scripts allow-same-origin allow-popups allow-downloads"
            onLoad={() => {
              loaded.current = true;
              initialize();
            }}
          />
        ) : null}
      </aside>
    </div>
  );
}
