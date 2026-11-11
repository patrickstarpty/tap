/** A reference to a server-owned result, never executable content from an iframe. */
export interface AgentSourceReference {
  task_id: string;
  execution_id: string;
  bundle_id: string;
  input_sha256: string;
}
export interface LcaAssistantContext {
  project_id: string;
  actor_id: string;
  identity_mode: "validation";
  gateway_instance_id: string;
  context_binding: string;
}
export const assistantSchema = "lca-assistant-v1";
export const boundedId = (value: unknown): value is string =>
  typeof value === "string" && /^[A-Za-z0-9_-]{1,80}$/.test(value);
const digest = (value: unknown): value is string =>
  typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
export const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
export function isAssistantContext(
  value: unknown,
): value is LcaAssistantContext {
  return (
    record(value) &&
    boundedId(value.project_id) &&
    boundedId(value.actor_id) &&
    value.identity_mode === "validation" &&
    boundedId(value.gateway_instance_id) &&
    digest(value.context_binding)
  );
}
export function sameAssistantContext(
  value: unknown,
  expected: LcaAssistantContext,
): boolean {
  return (
    isAssistantContext(value) &&
    value.project_id === expected.project_id &&
    value.actor_id === expected.actor_id &&
    value.identity_mode === expected.identity_mode &&
    value.gateway_instance_id === expected.gateway_instance_id &&
    value.context_binding === expected.context_binding
  );
}
export function isAgentSourceReference(
  value: unknown,
): value is AgentSourceReference {
  return (
    record(value) &&
    Object.keys(value).length === 4 &&
    boundedId(value.task_id) &&
    boundedId(value.execution_id) &&
    boundedId(value.bundle_id) &&
    digest(value.input_sha256)
  );
}
export function sameAgentSource(
  value: AgentSourceReference,
  expected: AgentSourceReference,
): boolean {
  return (
    value.task_id === expected.task_id &&
    value.execution_id === expected.execution_id &&
    value.bundle_id === expected.bundle_id &&
    value.input_sha256 === expected.input_sha256
  );
}
export function tapperEmbedUrl(): URL | null {
  try {
    const url = new URL(
      import.meta.env.VITE_TAPPER_URL ?? "http://127.0.0.1:5173",
    );
    if (!/^https?:$/.test(url.protocol) || url.username || url.password)
      return null;
    return new URL("/embed/lca", url);
  } catch {
    return null;
  }
}
