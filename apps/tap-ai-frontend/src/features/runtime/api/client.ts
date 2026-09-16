import type { components } from "../../../shared/api/generated/schema";

export type RuntimeMode = components["schemas"]["RuntimeMode"];
export interface RuntimeClient {
  getMode(signal?: AbortSignal): Promise<RuntimeMode>;
}

export function createRuntimeClient({
  fetch: fetcher = globalThis.fetch,
}: { fetch?: typeof fetch } = {}): RuntimeClient {
  return {
    async getMode(signal) {
      const response = await fetcher("/api/v1/runtime-mode", { signal });
      if (!response.ok) throw new Error("Runtime is unavailable.");
      const value: unknown = await response.json();
      if (typeof value !== "object" || value === null || Array.isArray(value)) {
        throw new Error("Invalid runtime mode.");
      }
      const mode = value as Record<string, unknown>;
      if (
        mode.mode !== "validation" ||
        mode.identityMode !== "validation" ||
        typeof mode.projectId !== "string" ||
        mode.projectId.trim().length === 0 ||
        mode.projectId.length > 128 ||
        typeof mode.actorId !== "string" ||
        mode.actorId.trim().length === 0 ||
        mode.actorId.length > 128 ||
        Object.keys(mode).some(
          (key) =>
            !["mode", "identityMode", "projectId", "actorId"].includes(key),
        )
      )
        throw new Error("Invalid runtime mode.");
      return mode as RuntimeMode;
    },
  };
}
