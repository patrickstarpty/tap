import type { components } from "../../../shared/api/generated/schema";

export type WorkflowTaskPage = components["schemas"]["WorkflowTaskPage"];
export type WorkflowTaskSnapshot =
  components["schemas"]["WorkflowTaskSnapshot"];
export type WorkflowNodeDetail = components["schemas"]["WorkflowNodeDetail"];
export type WorkflowLogPage = components["schemas"]["WorkflowLogPage"];
export type WorkflowEventEnvelope =
  components["schemas"]["WorkflowEventEnvelope"];
export type WorkflowCommandRequest =
  components["schemas"]["WorkflowCommandRequest"];
export type WorkflowCommandView = components["schemas"]["WorkflowCommandView"];
export type WorkflowCreateRequest =
  components["schemas"]["WorkflowCreateRequest"];

export class WorkflowClientError extends Error {
  constructor(readonly status: number) {
    super(`Workflow request failed (${status}).`);
  }
}

const origin = () =>
  typeof window === "undefined" || window.location.origin === "null"
    ? "http://127.0.0.1"
    : window.location.origin;

export function createWorkflowClient(
  projectId: string,
  fetcher: typeof fetch = globalThis.fetch,
) {
  const base = `/api/v1/projects/${encodeURIComponent(projectId)}/workflows`;
  const path = (suffix = "") => new URL(`${base}${suffix}`, origin());
  async function json<T>(request: Request): Promise<T> {
    const response = await fetcher(request);
    if (!response.ok) throw new WorkflowClientError(response.status);
    return (await response.json()) as T;
  }
  return {
    list(
      input: {
        agentId?: string;
        cursor?: string;
        limit?: number;
        signal?: AbortSignal;
      } = {},
    ) {
      const url = path();
      if (input.agentId) url.searchParams.set("agentId", input.agentId);
      if (input.cursor) url.searchParams.set("cursor", input.cursor);
      if (input.limit) url.searchParams.set("limit", String(input.limit));
      return json<WorkflowTaskPage>(new Request(url, { signal: input.signal }));
    },
    snapshot(taskId: string, signal?: AbortSignal) {
      return json<WorkflowTaskSnapshot>(
        new Request(path(`/${encodeURIComponent(taskId)}`), { signal }),
      );
    },
    node(taskId: string, nodeId: string, signal?: AbortSignal) {
      return json<WorkflowNodeDetail>(
        new Request(
          path(
            `/${encodeURIComponent(taskId)}/nodes/${encodeURIComponent(nodeId)}`,
          ),
          { signal },
        ),
      );
    },
    logs(
      taskId: string,
      nodeId: string,
      attempt: number,
      after = 0,
      signal?: AbortSignal,
    ) {
      const url = path(
        `/${encodeURIComponent(taskId)}/nodes/${encodeURIComponent(nodeId)}/logs`,
      );
      url.searchParams.set("attempt", String(attempt));
      url.searchParams.set("after", String(after));
      return json<WorkflowLogPage>(new Request(url, { signal }));
    },
    command(
      taskId: string,
      body: WorkflowCommandRequest,
      key: string,
      signal?: AbortSignal,
    ) {
      return json<WorkflowCommandView>(
        new Request(path(`/${encodeURIComponent(taskId)}/commands`), {
          method: "POST",
          signal,
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": key,
          },
          body: JSON.stringify(body),
        }),
      );
    },
    create(body: WorkflowCreateRequest, key: string, signal?: AbortSignal) {
      return json<WorkflowTaskSnapshot>(
        new Request(path(), {
          method: "POST",
          signal,
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": key,
          },
          body: JSON.stringify(body),
        }),
      );
    },
    replay(taskId: string, key: string, signal?: AbortSignal) {
      return json<WorkflowTaskSnapshot>(
        new Request(path(`/${encodeURIComponent(taskId)}/replay`), {
          method: "POST",
          signal,
          headers: { "Idempotency-Key": key },
        }),
      );
    },
    async *stream(
      taskId: string,
      cursor: number,
      signal?: AbortSignal,
    ): AsyncGenerator<WorkflowEventEnvelope> {
      const url = path(`/${encodeURIComponent(taskId)}/events`);
      url.searchParams.set("snapshotSequence", String(cursor));
      const response = await fetcher(
        new Request(url, {
          signal,
          headers: { "Last-Event-ID": String(cursor) },
        }),
      );
      if (!response.ok) throw new WorkflowClientError(response.status);
      if (response.body === null) throw new WorkflowClientError(503);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder
            .decode(value, { stream: true })
            .replaceAll("\r\n", "\n");
          let boundary = buffer.indexOf("\n\n");
          while (boundary >= 0) {
            const frame = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            const data = frame
              .split("\n")
              .filter((line) => line.startsWith("data:"))
              .map((line) => line.slice(5).trimStart())
              .join("\n");
            if (data) yield JSON.parse(data) as WorkflowEventEnvelope;
            boundary = buffer.indexOf("\n\n");
          }
        }
      } finally {
        reader.releaseLock();
      }
    },
  };
}
