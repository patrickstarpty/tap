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
export type ConversationCitationPreview =
  components["schemas"]["CitationPreview"];
export type InsightsExplanationRequest =
  components["schemas"]["InsightsExplanationRequest"] & {
    conversationId?: string;
    sourceRevisionIds?: string[];
    documentRevisionIds?: string[];
  };
export type InsightsExplanationResult =
  components["schemas"]["InsightsExplanationResult"];
export type InsightsExplanationAccepted =
  components["schemas"]["InsightsExplanationAccepted"];

export type InsightsHandoff = {
  queryId: string;
  resourceRefs: string[];
  draft: string;
};

const HANDOFF_KEYS = new Set(["projectId", "queryId", "resourceRef", "draft"]);
const HANDOFF_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/u;

export function parseInsightsHandoff(
  href: string,
  authorizedProjectId: string,
): InsightsHandoff | null {
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return null;
  }
  if (
    [...url.searchParams.keys()].some((key) => !HANDOFF_KEYS.has(key)) ||
    url.searchParams.getAll("projectId").length !== 1 ||
    url.searchParams.get("projectId") !== authorizedProjectId ||
    url.searchParams.getAll("queryId").length !== 1 ||
    url.searchParams.getAll("draft").length !== 1
  )
    return null;
  const queryId = url.searchParams.get("queryId") ?? "";
  const draft = url.searchParams.get("draft") ?? "";
  const resourceRefs = url.searchParams.getAll("resourceRef");
  if (
    !HANDOFF_ID.test(queryId) ||
    draft.trim().length === 0 ||
    draft.length > 500 ||
    resourceRefs.length === 0 ||
    resourceRefs.length > 20 ||
    new Set(resourceRefs).size !== resourceRefs.length ||
    resourceRefs.some((reference) => !HANDOFF_ID.test(reference))
  )
    return null;
  return { queryId, resourceRefs, draft };
}

export class ConversationClientError extends Error {
  constructor(
    readonly status: number,
    readonly retryable: boolean,
    readonly code: string | null = null,
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
  explainInsights(
    input: InsightsExplanationRequest,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<InsightsExplanationAccepted>;
  getInsightsExplanation(
    conversationId: string,
    turnId: string,
    signal?: AbortSignal,
  ): Promise<InsightsExplanationAccepted | InsightsExplanationResult>;
  cancel(
    conversationId: string,
    turnId: string,
    signal?: AbortSignal,
  ): Promise<ConversationTurnSummary>;
  citation(
    conversationId: string,
    turnId: string,
    citationId: string,
    signal?: AbortSignal,
  ): Promise<ConversationCitationPreview>;
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
    let code: string | null = null;
    try {
      const body = (await response.json()) as {
        retryable?: unknown;
        type?: unknown;
      };
      if (typeof body.retryable === "boolean") retryable = body.retryable;
      if (
        typeof body.type === "string" &&
        body.type.startsWith("https://tap.example/problems/")
      ) {
        const problemCode = body.type.slice(
          "https://tap.example/problems/".length,
        );
        if (
          [
            "citation-stale",
            "citation-unavailable",
            "knowledge-runtime-unavailable",
          ].includes(problemCode)
        ) {
          code = problemCode;
        }
      }
    } catch {
      // The status is sufficient; never expose an untrusted response body.
    }
    throw new ConversationClientError(response.status, retryable, code);
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
  const projectRoot = `${baseUrl || baseOrigin()}/api/v1/projects/${encodeURIComponent(projectId)}`;
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
    explainInsights: async (input, idempotencyKey, signal) =>
      checkedJson<InsightsExplanationAccepted>(
        await fetcher(
          new Request(`${projectRoot}/insights/explanations`, {
            method: "POST",
            headers: {
              "content-type": "application/json",
              "idempotency-key": idempotencyKey,
            },
            body: JSON.stringify(input),
            signal,
          }),
        ),
      ),
    getInsightsExplanation: async (conversationId, turnId, signal) =>
      checkedJson<InsightsExplanationAccepted | InsightsExplanationResult>(
        await fetcher(
          new Request(
            `${projectRoot}/insights/explanations/${encodeURIComponent(conversationId)}/turns/${encodeURIComponent(turnId)}`,
            { signal },
          ),
        ),
      ),
    cancel: (id, turnId, signal) =>
      request<ConversationTurnSummary>(
        `/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}/cancel`,
        { method: "POST", signal },
      ),
    citation: (id, turnId, citationId, signal) =>
      request<ConversationCitationPreview>(
        `/${encodeURIComponent(id)}/turns/${encodeURIComponent(turnId)}/citations/${encodeURIComponent(citationId)}`,
        { signal },
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
