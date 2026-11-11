import { useEffect, useRef, useState } from "react";
import { useRuntimeModeQuery } from "../features/runtime/api/queries";
import {
  loadLcaContext,
  parseLcaContext,
  sameLcaContext,
  type LcaGatewayContext,
} from "../shared/api/browserContext";
import type { LcaSourceReference } from "../features/workflows/components/RunDetail";
import { TapperWorkspace } from "../widgets/tap/TapperWorkspace";
const version = "lca-assistant-v1";
const noncePattern = /^[A-Za-z0-9_-]{20,80}$/u;
const id = /^[A-Za-z0-9_-]{1,80}$/u;
const wireContext = (value: LcaGatewayContext) => ({
  project_id: value.project_id,
  actor_id: value.actor_id,
  identity_mode: value.identity_mode,
  gateway_instance_id: value.gateway_instance_id,
  context_binding: value.context_binding,
});
export function LcaEmbedPage() {
  const runtime = useRuntimeModeQuery();
  const [verified, setVerified] = useState<{
    nonce: string;
    context: LcaGatewayContext;
  } | null>(null);
  const [error, setError] = useState("");
  const acceptedNonce = useRef<string | null>(null);
  const runtimeRef = useRef(runtime);
  runtimeRef.current = runtime;
  const parentOrigin = (() => {
    try {
      return new URL(
        import.meta.env.VITE_TAP_WEB_URL || "http://127.0.0.1:5174",
      ).origin;
    } catch {
      return null;
    }
  })();
  useEffect(() => {
    const controller = new AbortController();
    let pending: { nonce: string; context: LcaGatewayContext } | null = null;
    let gateway: LcaGatewayContext | null = null;
    let failed = false;
    let readySent = false;
    const acknowledge = () => {
      const mode = runtimeRef.current;
      if (readySent || failed || !pending || !gateway || !mode.isSuccess)
        return;
      if (
        !sameLcaContext(pending.context, gateway) ||
        mode.data.projectId !== gateway.project_id ||
        mode.data.actorId !== gateway.actor_id ||
        mode.data.identityMode !== gateway.identity_mode
      ) {
        failed = true;
        setVerified(null);
        setError(
          "LCA and Tapper authorized contexts do not match. Check both gateway connections before running automation.",
        );
        return;
      }
      readySent = true;
      window.clearInterval(timer);
      acceptedNonce.current = pending.nonce;
      setVerified((current) =>
        current?.nonce === pending!.nonce ? current : pending,
      );
      window.parent.postMessage(
        {
          type: "lca:ready",
          schema_version: version,
          nonce: pending.nonce,
          context: wireContext(gateway),
        },
        parentOrigin!,
      );
    };
    const receive = (event: MessageEvent<unknown>) => {
      if (
        !parentOrigin ||
        event.origin !== parentOrigin ||
        event.source !== window.parent ||
        !event.data ||
        typeof event.data !== "object"
      )
        return;
      const value = event.data as Record<string, unknown>;
      if (
        value.type !== "lca:init" ||
        value.schema_version !== version ||
        typeof value.nonce !== "string" ||
        !noncePattern.test(value.nonce) ||
        Object.keys(value).some(
          (key) =>
            !["type", "schema_version", "nonce", "context"].includes(key),
        )
      )
        return;
      if (
        acceptedNonce.current !== null &&
        acceptedNonce.current !== value.nonce
      )
        return;
      try {
        pending = {
          nonce: value.nonce,
          context: parseLcaContext(value.context, false),
        };
        acknowledge();
      } catch {
        setError("LCA context could not be verified.");
      }
    };
    window.addEventListener("message", receive);
    void loadLcaContext(controller.signal)
      .then((context) => {
        gateway = context;
        acknowledge();
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          failed = true;
          setError(
            "Authorized browser gateway is unavailable. Check the connection before running automation.",
          );
        }
      });
    // Runtime may settle after init and the independently resolved gateway.
    const timer = window.setInterval(acknowledge, 100);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      window.removeEventListener("message", receive);
    };
  }, [parentOrigin]);
  const review = (source: LcaSourceReference) => {
    if (
      !verified ||
      !parentOrigin ||
      ![source.task_id, source.execution_id, source.bundle_id].every((value) =>
        id.test(value),
      ) ||
      !/^[a-f0-9]{64}$/u.test(source.input_sha256)
    )
      return;
    window.parent.postMessage(
      {
        type: "lca:review",
        schema_version: version,
        nonce: verified.nonce,
        context: wireContext(verified.context),
        request_id: crypto.randomUUID(),
        source,
      },
      parentOrigin,
    );
  };
  if (
    verified &&
    (!runtime.isSuccess ||
      runtime.data.projectId !== verified.context.project_id ||
      runtime.data.actorId !== verified.context.actor_id ||
      runtime.data.identityMode !== verified.context.identity_mode)
  )
    return (
      <p role="alert">
        Authorized Tapper identity changed. Reload the assistant before running
        automation.
      </p>
    );
  if (error) return <p role="alert">{error}</p>;
  if (!verified)
    return (
      <p role="status">Connecting Tapper to the authorized LCA workspace…</p>
    );
  return (
    <TapperWorkspace
      variant="lca-embedded"
      lcaContext={verified.context}
      onReviewLca={review}
    />
  );
}
