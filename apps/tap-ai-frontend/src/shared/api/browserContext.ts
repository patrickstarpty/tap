export interface LcaGatewayContext {
  available: true;
  project_id: string;
  actor_id: string;
  identity_mode: "validation";
  gateway_instance_id: string;
  context_binding: string;
}
const id = /^[A-Za-z0-9_-]{1,80}$/u;
export function parseLcaContext(
  value: unknown,
  requireAvailable = true,
): LcaGatewayContext {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Authorized browser context is unavailable.");
  const item = value as Record<string, unknown>;
  if (
    (requireAvailable && item.available !== true) ||
    item.identity_mode !== "validation" ||
    ![item.project_id, item.actor_id, item.gateway_instance_id].every(
      (value) => typeof value === "string" && id.test(value),
    ) ||
    Object.keys(item).some(
      (key) =>
        ![
          "available",
          "project_id",
          "actor_id",
          "identity_mode",
          "gateway_instance_id",
          "context_binding",
        ].includes(key),
    ) ||
    typeof item.context_binding !== "string" ||
    !/^[a-f0-9]{64}$/u.test(item.context_binding)
  )
    throw new Error("Authorized browser context is unavailable.");
  return {
    available: true,
    project_id: item.project_id as string,
    actor_id: item.actor_id as string,
    identity_mode: "validation",
    gateway_instance_id: item.gateway_instance_id as string,
    context_binding: item.context_binding,
  };
}
export const sameLcaContext = (
  left: LcaGatewayContext,
  right: LcaGatewayContext,
) =>
  left.project_id === right.project_id &&
  left.actor_id === right.actor_id &&
  left.identity_mode === right.identity_mode &&
  left.gateway_instance_id === right.gateway_instance_id &&
  left.context_binding === right.context_binding;
export async function loadLcaContext(
  signal?: AbortSignal,
): Promise<LcaGatewayContext> {
  const response = await fetch("/api/lca/context", {
    credentials: "same-origin",
    cache: "no-store",
    signal,
  });
  if (!response.ok)
    throw new Error("Authorized browser context is unavailable.");
  return parseLcaContext(await response.json());
}

export async function loadBrowserContext(
  signal?: AbortSignal,
): Promise<LcaGatewayContext> {
  const response = await fetch("/api/v1/browser-context", {
    credentials: "same-origin",
    cache: "no-store",
    signal,
  });
  if (!response.ok)
    throw new Error("Authorized browser context is unavailable.");
  return parseLcaContext(await response.json());
}
