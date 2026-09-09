import type { components } from "../../../shared/api/generated/schema";
import type { ChatEventEnvelope } from "../model/stream";

export type ConversationAccepted =
  components["schemas"]["ConversationAccepted"];
export type ConversationCreateRequest =
  components["schemas"]["ConversationCreateRequest"];
export type ConversationDetail = components["schemas"]["ConversationDetail"];
export type ConversationEventPage =
  components["schemas"]["ConversationEventPage"];
export type ConversationPage = components["schemas"]["ConversationPage"];
export type ConversationTurnSummary =
  components["schemas"]["ConversationTurnSummary"];

export class ConversationClientError extends Error {
  constructor(
    readonly status: number,
    readonly retryable: boolean,
  ) {
    super(`Conversation request failed (${status}).`);
    this.name = "ConversationClientError";
  }
}

export interface ConversationClient {
  readonly projectId: string;
  list(input: {
    cursor?: string;
    limit?: number;
    signal?: AbortSignal;
  }): Promise<ConversationPage>;
  get(
    conversationId: string,
    signal?: AbortSignal,
  ): Promise<ConversationDetail>;
  events(
    conversationId: string,
    signal?: AbortSignal,
  ): Promise<ConversationEventPage>;
  create(
    input: ConversationCreateRequest,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<ConversationAccepted>;
  append(
    conversationId: string,
    input: ConversationCreateRequest,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<ConversationAccepted>;
  cancel(
    conversationId: string,
    turnId: string,
    signal?: AbortSignal,
  ): Promise<ConversationTurnSummary>;
  stream(
    conversationId: string,
    lastSequence: number,
    signal?: AbortSignal,
  ): AsyncGenerator<ChatEventEnvelope>;
}

function baseOrigin(): string {
  return typeof window === "undefined" || window.location.origin === "null"
    ? "http://127.0.0.1"
    : window.location.origin;
}

async function checkedJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let retryable = response.status >= 500;
    try {
      const body = (await response.json()) as { retryable?: unknown };
      if (typeof body.retryable === "boolean") retryable = body.retryable;
    } catch {
      // The status is sufficient; never expose an untrusted response body.
    }
    throw new ConversationClientError(response.status, retryable);
  }
  return (await response.json()) as T;
}

function parseFrame(frame: string): ChatEventEnvelope | null {
  const data = frame
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (data.length === 0) return null;
  try {
    const value = JSON.parse(data) as Partial<ChatEventEnvelope>;
    return typeof value.sequence === "number" &&
      typeof value.turnId === "string"
      ? (value as ChatEventEnvelope)
      : null;
  } catch {
    return null;
  }
}

export function createConversationClient({
  projectId,
  baseUrl = "",
  fetch: fetcher = globalThis.fetch,
}: {
  projectId: string;
  baseUrl?: string;
  fetch?: (input: Request) => Promise<Response>;
}): ConversationClient {
  if (projectId.trim().length === 0)
    throw new Error("A project ID is required.");
  const root = `${baseUrl || baseOrigin()}/api/v1/projects/${encodeURIComponent(projectId)}/conversations`;
  const request = async <T>(path: string, init?: RequestInit) =>
    checkedJson<T>(await fetcher(new Request(`${root}${path}`, init)));
  const write = (
    body: ConversationCreateRequest,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): RequestInit => ({
    method: "POST",
    headers: {
      "content-type": "application/json",
      "idempotency-key": idempotencyKey,
    },
    body: JSON.stringify(body),
    signal,
  });

  return {
    projectId,
    list({ cursor, limit = 20, signal }) {
      const query = new URLSearchParams({ limit: String(limit) });
      if (cursor !== undefined) query.set("cursor", cursor);
      return request<ConversationPage>(`?${query}`, { signal });
    },
    get: (id, signal) =>
      request<ConversationDetail>(`/${encodeURIComponent(id)}`, { signal }),
    events: (id, signal) =>
      request<ConversationEventPage>(`/${encodeURIComponent(id)}/events`, {
        signal,
      }),
    create: (body, key, signal) =>
      request<ConversationAccepted>("", write(body, key, signal)),
    append: (id, body, key, signal) =>
      request<ConversationAccepted>(
        `/${encodeURIComponent(id)}/turns`,
        write(body, key, signal),
      ),
    cancel: (id, turnId, signal) =>
      request<ConversationTurnSummary>(
        `/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}/cancel`,
        { method: "POST", signal },
      ),
    async *stream(id, lastSequence, signal) {
      const response = await fetcher(
        new Request(`${root}/${encodeURIComponent(id)}/stream`, {
          headers:
            lastSequence > 0 ? { "Last-Event-ID": String(lastSequence) } : {},
          signal,
        }),
      );
      if (!response.ok) {
        await checkedJson(response);
        return;
      }
      if (response.body === null) throw new ConversationClientError(502, true);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let pending = "";
      try {
        while (true) {
          const { done, value } = await reader.read();
          pending += decoder.decode(value, { stream: !done });
          pending = pending.replace(/\r\n/gu, "\n");
          if (done) pending = pending.replace(/\r/gu, "\n");
          let boundary = pending.indexOf("\n\n");
          while (boundary >= 0) {
            const parsed = parseFrame(pending.slice(0, boundary));
            pending = pending.slice(boundary + 2);
            if (parsed !== null) yield parsed;
            boundary = pending.indexOf("\n\n");
          }
          if (done) break;
        }
      } finally {
        reader.releaseLock();
      }
    },
  };
}
