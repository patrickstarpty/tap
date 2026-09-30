import type { Locale } from "./model";

export type TraceSpanStatus = "ok" | "error";

export interface TraceModelCall {
  operation: "chat" | "embeddings";
  requestedModel: string;
  upstreamModel: string | null;
  provider: string | null;
  inputTokens: number | null;
  outputTokens: number | null;
  costUsd: number | null;
  request: string;
  response: string | null;
  reasoning: string | null;
}

export interface TraceHitChunk {
  id: string;
  sourceName: string;
  snippet: string;
}

export interface TraceSpan {
  spanId: string;
  parentSpanId: string | null;
  attempt: number;
  name: string;
  status: TraceSpanStatus;
  durationMs: number;
  modelCall?: TraceModelCall;
  hitChunks?: readonly TraceHitChunk[];
}

export interface SampleTrace {
  locale: Locale;
  attempts: readonly number[];
  totalDurationMs: number;
  inputTokens: number;
  outputTokens: number;
  costUsd: number | null;
  costIncomplete: boolean;
  requestedModel: string;
  upstreamModel: string;
  spans: readonly TraceSpan[];
}

export function createSampleTrace(locale: Locale): SampleTrace {
  return {
    locale,
    attempts: [1, 2],
    totalDurationMs: 3180,
    inputTokens: 1420,
    outputTokens: 286,
    costUsd: 0.0042,
    costIncomplete: true,
    requestedModel: "qwen-plus",
    upstreamModel: "dashscope/qwen-plus",
    spans: [
      {
        spanId: "turn-1",
        parentSpanId: null,
        attempt: 1,
        name: "turn.execute",
        status: "error",
        durationMs: 1860,
      },
      {
        spanId: "plan-1",
        parentSpanId: "turn-1",
        attempt: 1,
        name: "chat.plan",
        status: "ok",
        durationMs: 210,
      },
      {
        spanId: "retrieval-1a",
        parentSpanId: "turn-1",
        attempt: 1,
        name: "retrieval.search",
        status: "ok",
        durationMs: 340,
        hitChunks: [
          {
            id: "chunk-1",
            sourceName: "Life underwriting guide · v1.2.md",
            snippet: "Block submission when health disclosure is missing.",
          },
        ],
      },
      {
        spanId: "retrieval-1b",
        parentSpanId: "turn-1",
        attempt: 1,
        name: "retrieval.search",
        status: "ok",
        durationMs: 265,
        hitChunks: [
          {
            id: "chunk-2",
            sourceName: "Underwriting evidence.pdf",
            snippet: "Return HTTP 422 with HEALTH_DISCLOSURE_REQUIRED.",
          },
        ],
      },
      {
        spanId: "graph-1",
        parentSpanId: "turn-1",
        attempt: 1,
        name: "graph.enrich",
        status: "ok",
        durationMs: 180,
      },
      {
        spanId: "embed-1",
        parentSpanId: "turn-1",
        attempt: 1,
        name: "embeddings text-embedding-v3",
        status: "ok",
        durationMs: 95,
        modelCall: {
          operation: "embeddings",
          requestedModel: "text-embedding-v3",
          upstreamModel: "dashscope/text-embedding-v3",
          provider: "dashscope",
          inputTokens: 512,
          outputTokens: null,
          costUsd: null,
          request: JSON.stringify(
            { model: "text-embedding-v3", input: ["health disclosure"] },
            null,
            2,
          ),
          response: JSON.stringify({ dimension: 1024, count: 1 }, null, 2),
          reasoning: null,
        },
      },
      {
        spanId: "chat-1",
        parentSpanId: "turn-1",
        attempt: 1,
        name: "chat qwen-plus",
        status: "error",
        durationMs: 770,
        modelCall: {
          operation: "chat",
          requestedModel: "qwen-plus",
          upstreamModel: null,
          provider: null,
          inputTokens: null,
          outputTokens: null,
          costUsd: null,
          request: JSON.stringify(
            {
              model: "qwen-plus",
              messages: [{ role: "user", content: "health disclosure rule" }],
            },
            null,
            2,
          ),
          response: JSON.stringify(
            {
              error: {
                code: "upstream_timeout",
                message: "Gateway timed out after 30s",
              },
            },
            null,
            2,
          ),
          reasoning: null,
        },
      },
      {
        spanId: "turn-2",
        parentSpanId: null,
        attempt: 2,
        name: "turn.execute",
        status: "ok",
        durationMs: 1320,
      },
      {
        spanId: "citations-2",
        parentSpanId: "turn-2",
        attempt: 2,
        name: "citations.resolve",
        status: "ok",
        durationMs: 40,
      },
      {
        spanId: "chat-2",
        parentSpanId: "turn-2",
        attempt: 2,
        name: "chat qwen-plus",
        status: "ok",
        durationMs: 980,
        modelCall: {
          operation: "chat",
          requestedModel: "qwen-plus",
          upstreamModel: "dashscope/qwen-plus",
          provider: "dashscope",
          inputTokens: 1420,
          outputTokens: 286,
          costUsd: 0.0042,
          request: JSON.stringify(
            {
              model: "qwen-plus",
              messages: [{ role: "user", content: "health disclosure rule" }],
            },
            null,
            2,
          ),
          response: JSON.stringify(
            {
              choices: [
                {
                  message: {
                    content:
                      "Block submission when health disclosure is missing…",
                  },
                },
              ],
            },
            null,
            2,
          ),
          reasoning:
            "Checked underwriting guide section 4 and evidence PDF before composing the rule.",
        },
      },
    ],
  };
}
