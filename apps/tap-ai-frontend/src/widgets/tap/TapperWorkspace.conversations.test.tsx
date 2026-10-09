import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { fakeKnowledgeClient } from "../../features/knowledge/testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../../features/knowledge/testing/renderKnowledgeApp";
import { TapperWorkspace } from "./TapperWorkspace";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });

it("keeps an explicit New chat when durable history arrives afterwards", async () => {
  let releaseList: (() => void) | undefined;
  const requested: string[] = [];
  vi.stubGlobal("fetch", async (request: Request) => {
    requested.push(new URL(request.url).pathname);
    if (request.url.endsWith("/conversations?limit=20")) {
      await new Promise<void>((resolve) => {
        releaseList = resolve;
      });
      return json({
        items: [
          {
            conversationId: "conversation-old",
            title: "Earlier question",
            createdAt: "2026-09-28T08:00:00Z",
            updatedAt: "2026-09-28T08:00:00Z",
          },
        ],
        nextCursor: null,
      });
    }
    throw new Error(`Unexpected API call: ${request.url}`);
  });
  renderKnowledgeApp(<TapperWorkspace />, {
    api: fakeKnowledgeClient(),
  });
  const user = userEvent.setup();

  await waitFor(() => expect(releaseList).toBeDefined());
  await user.click(screen.getByRole("button", { name: "New chat" }));
  releaseList!();

  expect(
    await screen.findByRole("button", { name: /^Earlier question/u }),
  ).toBeInTheDocument();
  expect(
    requested.some((path) => path.endsWith("/conversations/conversation-old")),
  ).toBe(false);
  expect(screen.getByRole("textbox", { name: "Message Tapper" })).toHaveValue(
    "",
  );
});

const ANSWER = {
  traceId: "trace-1",
  queryPlanId: "plan-1",
  contextSnapshotId: "context-1",
  corpusVersion: "v1",
  retrievalProfileId: "quick",
  degradedMode: false,
  answer: "Verified identity is required.",
  abstained: false,
  claims: [
    {
      claimId: "claim-1",
      text: "Verified identity is required.",
      citationIds: [],
    },
  ],
  citations: [],
};

function completedConversation({
  traceId,
  extraSpans = [],
  api = fakeKnowledgeClient(),
}: {
  traceId: string | null;
  extraSpans?: readonly Record<string, unknown>[];
  api?: ReturnType<typeof fakeKnowledgeClient>;
}) {
  vi.stubGlobal("fetch", async (request: Request) => {
    const path = new URL(request.url).pathname;
    if (path.endsWith("/conversations") && request.method === "GET")
      return json({
        items: [
          {
            conversationId: "conversation-a",
            title: "What is the rule?",
            createdAt: "2026-09-29T08:00:00Z",
            updatedAt: "2026-09-29T08:00:00Z",
          },
        ],
        nextCursor: null,
      });
    if (path.endsWith("/conversations/conversation-a"))
      return json({
        conversationId: "conversation-a",
        title: "What is the rule?",
        createdAt: "2026-09-29T08:00:00Z",
        updatedAt: "2026-09-29T08:00:01Z",
        turns: [
          {
            turnId: "turn-1",
            state: "completed",
            attempt: 1,
            traceId,
            inputSnapshotDigest: `sha256:${"a".repeat(64)}`,
            answerEvidenceSnapshotDigest: null,
            input: {
              message: "What is the rule?",
              modelAlias: "qwen-plus",
              sourceRevisionIds: [],
              documentRevisionIds: [],
              resolvedResources: [],
              agentRevisionId: null,
              agentLabel: null,
              skillRevisionIds: [],
              skillLabels: [],
              insightsQueryId: null,
            },
          },
        ],
      });
    if (path.endsWith("/conversations/conversation-a/events"))
      return json({
        items: [
          {
            eventId: "event-1",
            sequence: 1,
            turnId: "turn-1",
            occurredAt: "2026-09-29T08:00:00Z",
            eventType: "stage.completed",
            payload: { stage: "knowledge.answer" },
          },
          {
            eventId: "event-2",
            sequence: 2,
            turnId: "turn-1",
            occurredAt: "2026-09-29T08:00:01Z",
            eventType: "turn.completed",
            payload: { answer: ANSWER },
          },
        ],
        nextCursor: null,
      });
    if (path.endsWith("/conversations/conversation-a/turns/turn-1/trace"))
      return json({
        traceId: "trace-1",
        summary: {
          totalDurationMs: 900,
          inputTokens: 10,
          outputTokens: 20,
          costUsd: "0.0010",
          costIncomplete: false,
          requestedModels: ["qwen-plus"],
          upstreamModels: ["qwen-plus-2024"],
          attemptCount: 1,
        },
        spans: [
          {
            spanId: "span-1",
            parentSpanId: null,
            name: "turn.execute",
            status: "ok",
            startedAt: "2026-09-29T08:00:00.000Z",
            durationMs: 900,
            attributes: {},
            attempt: 1,
          },
          ...extraSpans,
        ],
        modelCalls: [],
      });
    throw new Error(`Unexpected API call: ${request.method} ${request.url}`);
  });
  return renderKnowledgeApp(<TapperWorkspace />, {
    api,
  });
}

it("shows trace panel for terminal turn with traceId", async () => {
  completedConversation({ traceId: "trace-1" });

  await screen.findByRole("button", { name: "Regenerate" });
  expect(
    await screen.findByRole("button", { name: /^Trace:/u }),
  ).toBeInTheDocument();
  expect(screen.queryByText(/^Searched/u)).not.toBeInTheDocument();
});

it("falls back to activity when traceId is null", async () => {
  completedConversation({ traceId: null });

  await screen.findByRole("button", { name: "Regenerate" });
  expect(
    screen.queryByRole("button", { name: /^Trace:/u }),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/^Searched/u)).toBeInTheDocument();
});

it("does not crash when a citation resolves before a running turn completes", async () => {
  vi.stubGlobal("fetch", async (request: Request) => {
    const path = new URL(request.url).pathname;
    if (path.endsWith("/conversations") && request.method === "GET")
      return json({
        items: [
          {
            conversationId: "conversation-a",
            title: "What is the rule?",
            createdAt: "2026-09-29T08:00:00Z",
            updatedAt: "2026-09-29T08:00:00Z",
          },
        ],
        nextCursor: null,
      });
    if (path.endsWith("/conversations/conversation-a"))
      return json({
        conversationId: "conversation-a",
        title: "What is the rule?",
        createdAt: "2026-09-29T08:00:00Z",
        updatedAt: "2026-09-29T08:00:01Z",
        turns: [
          {
            turnId: "turn-1",
            state: "running",
            attempt: 1,
            traceId: null,
            inputSnapshotDigest: `sha256:${"a".repeat(64)}`,
            answerEvidenceSnapshotDigest: null,
            input: {
              message: "What is the rule?",
              modelAlias: "qwen-plus",
              sourceRevisionIds: [],
              documentRevisionIds: [],
              resolvedResources: [],
              agentRevisionId: null,
              agentLabel: null,
              skillRevisionIds: [],
              skillLabels: [],
              insightsQueryId: null,
            },
          },
        ],
      });
    if (path.endsWith("/conversations/conversation-a/events"))
      return json({
        items: [
          {
            eventId: "event-1",
            sequence: 1,
            turnId: "turn-1",
            occurredAt: "2026-09-29T08:00:00Z",
            eventType: "citation.resolved",
            payload: {
              citation: {
                citationId: "citation-1",
                evidenceLabel: "Rules",
                chunkId: "chunk-1",
                logicalChunkId: "logical-1",
                source: {
                  sourceId: "source-1",
                  sourceType: "doc",
                  revisionKind: "blob_version",
                  revision: "revision-1",
                  sourceContentHash: "sha256:source",
                  anchor: { type: "document", page: 2 },
                },
                chunkContentHash: "sha256:chunk",
                contentRole: "source",
              },
            },
          },
        ],
        nextCursor: null,
      });
    throw new Error(`Unexpected API call: ${request.method} ${request.url}`);
  });

  // Reproduces the real journey's timing: the backend emits `citation.resolved`
  // events before the turn's `turn.completed` event/state transition, so the
  // widget must tolerate a running turn whose citations have started
  // resolving but whose full answer (with `claims`) has not arrived yet.
  renderKnowledgeApp(<TapperWorkspace />, {
    api: fakeKnowledgeClient(),
  });

  expect(
    await screen.findByText(/Waiting to start…|等待开始…/u),
  ).toBeInTheDocument();
  expect(screen.queryByText(/^Activity/u)).not.toBeInTheDocument();
  expect(
    screen.queryByText(/format could not be verified|回答格式无法核验/u),
  ).not.toBeInTheDocument();
});

it("resolves a retrieval hit to its published document name", async () => {
  const api = fakeKnowledgeClient().withPublishedSources({
    items: [
      {
        sourceId: "src_policy",
        sourceName: "Policy source",
        documentId: "doc_policy",
        filename: "policy.md",
        revisionId: "rev_policy",
        publicationId: "pub_policy",
        approvedItemCount: 1,
        inventoryItemCount: 1,
        partial: false,
        expiresAt: "2027-01-01T00:00:00Z",
      },
    ],
  });
  completedConversation({
    traceId: "trace-1",
    api,
    extraSpans: [
      {
        spanId: "span-retrieval",
        parentSpanId: "span-1",
        name: "retrieval.search",
        status: "ok",
        startedAt: "2026-09-29T08:00:00.100Z",
        durationMs: 100,
        attempt: 1,
        attributes: {
          "tap.retrieval.chunk_ids": ["chunk-1"],
          "tap.retrieval.document_ids": ["doc_policy"],
          "tap.retrieval.scores": [0.9],
          "tap.retrieval.hit_count": 1,
        },
      },
    ],
  });

  await screen.findByRole("button", { name: "Regenerate" });
  await userEvent.click(
    await screen.findByRole("button", { name: /^Trace:/u }),
  );

  expect(
    await screen.findByRole("link", { name: "policy.md" }),
  ).toBeInTheDocument();
});
