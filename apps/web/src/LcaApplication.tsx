import { useEffect, useRef, useState } from "react";
import { LcaWorkspace } from "./features/lca/LcaWorkspace";
import { LcaAssistantPanel } from "./features/lca/LcaAssistantPanel";
import {
  isAssistantContext,
  type AgentSourceReference,
  type LcaAssistantContext,
} from "./features/lca/assistantProtocol";

async function loadAuthorizedContext(
  signal: AbortSignal,
): Promise<LcaAssistantContext> {
  const response = await fetch("/api/lca/context", {
    credentials: "same-origin",
    cache: "no-store",
    signal,
  });
  if (!response.ok)
    throw new Error("Authorized LCA project context is unavailable.");
  const context: unknown = await response.json();
  if (
    !isAssistantContext(context) ||
    !("available" in context) ||
    context.available !== true
  ) {
    throw new Error(
      "LCA gateway is unavailable. Configure the authorized backend connection before using real debug flows.",
    );
  }
  return {
    project_id: context.project_id,
    actor_id: context.actor_id,
    identity_mode: context.identity_mode,
    gateway_instance_id: context.gateway_instance_id,
    context_binding: context.context_binding,
  };
}

/** The project comes from the authorized gateway, never a demo constant or URL. */
export function LcaApplication() {
  const [binding, setBinding] = useState<{
    context: LcaAssistantContext;
    revision: number;
  }>();
  const context = binding?.context;
  const [source, setSource] = useState<AgentSourceReference | null>(null);
  const [error, setError] = useState("");
  const [reconnecting, setReconnecting] = useState(false);
  const [reconnectError, setReconnectError] = useState("");
  const refresh = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void loadAuthorizedContext(controller.signal)
      .then((context) => {
        if (!controller.signal.aborted) setBinding({ context, revision: 0 });
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted)
          setError(
            failure instanceof Error
              ? failure.message
              : "LCA project context is unavailable.",
          );
      });
    return () => {
      controller.abort();
      refresh.current?.abort();
    };
  }, []);

  async function reconnect() {
    if (!context || refresh.current) return;
    const controller = new AbortController();
    refresh.current = controller;
    setSource(null);
    setReconnecting(true);
    setReconnectError("");
    try {
      const next = await loadAuthorizedContext(controller.signal);
      if (controller.signal.aborted) return;
      if (
        next.project_id !== context.project_id ||
        next.actor_id !== context.actor_id ||
        next.identity_mode !== context.identity_mode
      ) {
        throw new Error("The authorized LCA project or actor changed.");
      }
      // Keep the workspace's identity and local draft. Only renew the gateway
      // incarnation; even an unchanged binding requires a fresh child handshake.
      setBinding((current) => ({
        context: next,
        revision: (current?.revision ?? 0) + 1,
      }));
    } catch (failure) {
      if (!controller.signal.aborted)
        setReconnectError(
          `${failure instanceof Error ? failure.message : "LCA context could not be verified."} Your LCA draft is preserved. Restore the original authorized project and actor, then save your draft before reloading.`,
        );
    } finally {
      if (!controller.signal.aborted) {
        refresh.current = null;
        setReconnecting(false);
      }
    }
  }
  const authorityBlocked = reconnecting || Boolean(reconnectError);
  return (
    <section
      className="tap-lca-application"
      aria-label="Low Code Automation workspace"
    >
      {error ? (
        <p role="alert">{error}</p>
      ) : binding ? (
        <div className="tap-lca-layout">
          <fieldset
            className="lca-authority-guard"
            disabled={authorityBlocked}
            inert={authorityBlocked}
          >
            <LcaWorkspace
              projectId={binding.context.project_id}
              sourceReference={source}
              onSourceClear={() => setSource(null)}
            />
          </fieldset>
          <LcaAssistantPanel
            context={binding.context}
            onSourceChange={setSource}
            onReconnect={() => void reconnect()}
            reconnectRevision={binding.revision}
            reconnecting={reconnecting}
            reconnectError={reconnectError}
          />
        </div>
      ) : (
        <p role="status">Loading authorized project…</p>
      )}
    </section>
  );
}
