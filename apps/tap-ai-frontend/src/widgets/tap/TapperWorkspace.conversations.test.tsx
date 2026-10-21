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
  graphContextEvent,
  taskStepEvents = [],
  state = "completed",
  myFeedback = null,
  resolvedResources = [
    {
      documentId: "document-1",
      documentRevisionId: "document-rev-1",
      label: "Underwriting policy",
      sourceId: "source-1",
      sourceRevisionId: "source-rev-1",
    },
  ],
}: {
  traceId: string | null;
  extraSpans?: readonly Record<string, unknown>[];
  api?: ReturnType<typeof fakeKnowledgeClient>;
  graphContextEvent?: Record<string, unknown>;
  /** `task.step` payloads (review fix round 1, Important 1) — a durable
   * event type, replayed through `GET .../events` the same as any other,
   * so this exercises the real reducer's `task.step` branch rather than
   * stubbing `AssistantTurn.steps` directly. */
  taskStepEvents?: readonly Record<string, unknown>[];
  state?: "completed" | "abstained" | "canceled" | "failed";
  myFeedback?: { rating: "up" | "down"; comment?: string | null } | null;
  resolvedResources?: readonly Record<string, unknown>[];
}) {
  let nextSequence = 1;
  const events = [
    {
      eventId: "event-1",
      sequence: nextSequence++,
      turnId: "turn-1",
      occurredAt: "2026-09-29T08:00:00Z",
      eventType: "stage.completed",
      payload: { stage: "knowledge.answer" },
    },
    ...(graphContextEvent === undefined
      ? []
      : [
          {
            eventId: "event-graph",
            sequence: nextSequence++,
            turnId: "turn-1",
            occurredAt: "2026-09-29T08:00:00Z",
            eventType: "graph.context_ready",
            payload: graphContextEvent,
          },
        ]),
    ...taskStepEvents.map((payload, index) => ({
      eventId: `event-step-${index}`,
      sequence: nextSequence++,
      turnId: "turn-1",
      occurredAt: "2026-09-29T08:00:00Z",
      eventType: "task.step",
      payload,
    })),
    {
      eventId: "event-2",
      sequence: nextSequence++,
      turnId: "turn-1",
      occurredAt: "2026-09-29T08:00:01Z",
      eventType: "turn.completed",
      payload: { answer: ANSWER },
    },
  ];
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
            state,
            attempt: 1,
            traceId,
            myFeedback,
            inputSnapshotDigest: `sha256:${"a".repeat(64)}`,
            answerEvidenceSnapshotDigest: null,
            input: {
              message: "What is the rule?",
              modelAlias: "qwen-plus",
              sourceRevisionIds: [],
              documentRevisionIds: [],
              resolvedResources,
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
        items: events,
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

it("shows a completed task.step row alongside the trace panel for a terminal, traced turn (review fix round 1, Important 1)", async () => {
  // `task.step` is a durable event type replayed through `GET
  // .../events`, same as `graph.context_ready` above -- this exercises
  // the real reducer (`reduceStreamEvent`'s `task.step` branch) through
  // the full `TapperWorkspace` render tree, not a unit test of the
  // reducer or of `AnswerActivity` in isolation. Before this fix, the
  // terminal-with-trace branch rendered only `AnswerGraphSummary` +
  // `DurableTracePanel`, never `AnswerActivity`/any step list, so a
  // completed tool round's step was invisible once a turn had a trace.
  completedConversation({
    traceId: "trace-1",
    taskStepEvents: [
      {
        stepId: "turn-1:r1:c0",
        tool: "knowledge.search",
        status: "running",
        summary: "",
        durationMs: 0,
        toolRound: 1,
      },
      {
        stepId: "turn-1:r1:c0",
        tool: "knowledge.search",
        status: "completed",
        summary: "Found 2 hits",
        durationMs: 80,
        toolRound: 1,
      },
    ],
  });

  await screen.findByRole("button", { name: "Regenerate" });
  expect(
    await screen.findByRole("button", { name: /^Trace:/u }),
  ).toBeInTheDocument();
  expect(
    screen.getByText(
      "Round 1 · Knowledge search · Done · Found 2 hits · 80 ms",
    ),
  ).toBeVisible();
});

it("shows trace panel for terminal turn with traceId", async () => {
  completedConversation({ traceId: "trace-1" });

  await screen.findByRole("button", { name: "Regenerate" });
  expect(
    await screen.findByRole("button", { name: /^Trace:/u }),
  ).toBeInTheDocument();
  // I1 fix: the summary line must render alongside the trace panel, not
  // only when the trace/activity branch falls back to `AnswerActivity`.
  expect(screen.getByText(/^Searched/u)).toBeInTheDocument();
});

it("shows the summary line and the expandable seeds/paths rows for a terminal, traced turn with a graph context", async () => {
  completedConversation({
    traceId: "trace-1",
    graphContextEvent: {
      status: "APPLIED",
      seedCount: 2,
      paths: [["核保流程", "健康告知"]],
      relationCount: 1,
    },
  });

  await screen.findByRole("button", { name: "Regenerate" });
  expect(
    await screen.findByRole("button", { name: /^Trace:/u }),
  ).toBeInTheDocument();
  const summary = screen.getByText(/^Searched/u);
  expect(summary).toBeInTheDocument();
  await userEvent.click(summary);
  expect(screen.getByText("2 seed entities")).toBeVisible();
  expect(screen.getByText(/核保流程 → 健康告知/u)).toBeVisible();
});

it("falls back to activity when traceId is null", async () => {
  completedConversation({ traceId: null });

  await screen.findByRole("button", { name: "Regenerate" });
  expect(
    screen.queryByRole("button", { name: /^Trace:/u }),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/^Searched/u)).toBeInTheDocument();
});

it("shows feedback controls restored from myFeedback for a completed, traced turn", async () => {
  completedConversation({
    traceId: "trace-1",
    myFeedback: { rating: "down", comment: "missing a citation" },
  });

  await screen.findByRole("button", { name: "Regenerate" });
  const up = await screen.findByRole("button", { name: "Helpful" });
  const down = screen.getByRole("button", { name: "Not helpful" });
  expect(up).toHaveAttribute("aria-pressed", "false");
  expect(down).toHaveAttribute("aria-pressed", "true");
});

it("shows feedback controls for a completed turn with no traceId", async () => {
  // traceId is nullable and the backend accepts feedback without a trace,
  // so the AnswerActivity fallback branch must offer rating too, not only
  // the DurableTracePanel branch.
  completedConversation({ traceId: null });

  await screen.findByRole("button", { name: "Regenerate" });
  expect(
    await screen.findByRole("button", { name: "Helpful" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Not helpful" }),
  ).toBeInTheDocument();
});

it("does not render feedback controls for a non-completed terminal turn", async () => {
  completedConversation({ traceId: "trace-1", state: "abstained" });

  await screen.findByRole("button", { name: "Regenerate" });
  expect(
    screen.queryByRole("button", { name: "Helpful" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Not helpful" }),
  ).not.toBeInTheDocument();
});

it("replays a persisted graph.context_ready event and shows the summary line", async () => {
  // `relationCount: 3` with no edge citations in `ANSWER.citations` (which
  // is empty) is a value only the replayed `graph.context_ready` event can
  // produce — `buildAnswerGraphSummary` would otherwise fall back to
  // `edgeCitationCount` (0), so this fails if replay is broken even though
  // the summary line still renders.
  completedConversation({
    traceId: null,
    graphContextEvent: {
      status: "EMPTY",
      seedCount: 0,
      paths: [],
      relationCount: 3,
    },
  });

  await screen.findByRole("button", { name: "Regenerate" });
  expect(
    screen.getByText("Searched 1 source · 0 passages · 3 relations"),
  ).toBeInTheDocument();
});

it("shows no summary line for a traced turn with zero sources, citations or relations", async () => {
  completedConversation({ traceId: "trace-1", resolvedResources: [] });

  await screen.findByRole("button", { name: "Regenerate" });
  expect(
    await screen.findByRole("button", { name: /^Trace:/u }),
  ).toBeInTheDocument();
  expect(screen.queryByText(/^Searched/u)).not.toBeInTheDocument();
});

it("shows the no-relation-evidence banner as a static note when the turn is terminal and the question named at least 2 entities", async () => {
  completedConversation({
    traceId: null,
    graphContextEvent: {
      status: "EMPTY",
      seedCount: 2,
      querySeedCount: 2,
      paths: [],
      relationCount: 0,
    },
  });

  await screen.findByRole("button", { name: "Regenerate" });
  const banner = await screen.findByText(
    "No direct relation evidence was found; the answer below is grounded in source passages.",
  );
  expect(banner).toBeInTheDocument();
  const alertContainer = banner.closest(".ant-alert");
  expect(alertContainer).not.toBeNull();
  expect(alertContainer?.getAttribute("role")).toBeNull();
});

it("hides the no-relation-evidence banner when the question named fewer than 2 entities", async () => {
  completedConversation({
    traceId: null,
    graphContextEvent: {
      status: "EMPTY",
      seedCount: 1,
      querySeedCount: 1,
      paths: [],
      relationCount: 0,
    },
  });

  await screen.findByRole("button", { name: "Regenerate" });
  expect(
    screen.queryByText(
      "No direct relation evidence was found; the answer below is grounded in source passages.",
    ),
  ).not.toBeInTheDocument();
});

it.each([
  { label: "reports none", querySeedCount: 0 },
  { label: "is omitted", querySeedCount: undefined },
])(
  "hides the no-relation-evidence banner when only evidence seeds were found and the question's own seed count $label",
  async ({ querySeedCount }) => {
    completedConversation({
      traceId: null,
      graphContextEvent: {
        status: "EMPTY",
        seedCount: 2,
        ...(querySeedCount === undefined ? {} : { querySeedCount }),
        paths: [],
        relationCount: 0,
      },
    });

    await screen.findByRole("button", { name: "Regenerate" });
    expect(
      screen.queryByText(
        "No direct relation evidence was found; the answer below is grounded in source passages.",
      ),
    ).not.toBeInTheDocument();
  },
);

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

it("never fetches Skill detail for a last-used Skill the catalog no longer lists as enabled", async () => {
  // Regression: a durable conversation's composer selection is prefilled
  // from its last turn's frozen `input.skillRevisionIds` (restore effect
  // in TapperWorkspace) before the enabled-catalog filter effect has run.
  // If that prefilled id was since disabled (e.g. a built-in Skill
  // retired between an E2E journey's restart phases), the per-turn
  // Skill-budget lookup must not blindly fetch its detail -- the detail
  // endpoint only resolves *enabled* revisions and 404s for a disabled
  // one, which showed up as a stray console network error with no
  // user-visible symptom (`tests/e2e/persistence.spec.ts`).
  const skillDetailRequests: string[] = [];
  // The real race: the composer's prefill from the conversation's last
  // turn (fast -- already in the conversation-detail response) lands
  // before the enabled-catalog list (a separate, independently-timed
  // query) ever resolves. Hold the catalog list back so that ordering is
  // forced here too, instead of letting a synchronous mock collapse both
  // into the same render.
  let releaseSkillsCatalog: (() => void) | undefined;
  vi.stubGlobal("fetch", async (request: Request | string) => {
    // The Agent/Skill catalog reads (`readCatalog` in `aiAssets.ts`) call
    // the bare global `fetch("/api/...")` with a relative string, not a
    // `Request` object -- unlike the `knowledgeClient`'s requests, which
    // all pass a `Request`. Handle both call shapes.
    const url = typeof request === "string" ? request : request.url;
    const method = typeof request === "string" ? "GET" : request.method;
    const path = new URL(url, "http://localhost").pathname;
    if (path.endsWith("/ai/agents") && method === "GET")
      return json({ items: [] });
    if (path.endsWith("/ai/skills") && method === "GET") {
      await new Promise<void>((resolve) => {
        releaseSkillsCatalog = resolve;
      });
      return json({ items: [] });
    }
    if (path.endsWith("/ai/skills/retired-citation-skill")) {
      skillDetailRequests.push(path);
      return json({ code: "asset-revision-unavailable" }, 404);
    }
    if (path.endsWith("/conversations") && method === "GET")
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
              skillRevisionIds: ["retired-citation-skill"],
              skillLabels: ["Citation skill"],
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
            eventType: "turn.completed",
            payload: { answer: ANSWER },
          },
        ],
        nextCursor: null,
      });
    if (path.endsWith("/conversations/conversation-a/turns/turn-1/trace"))
      return json({ code: "not-found" }, 404);
    throw new Error(`Unexpected API call: ${method} ${url}`);
  });
  renderKnowledgeApp(<TapperWorkspace />, { api: fakeKnowledgeClient() });

  // The turn (and its prefill of the retired Skill id) is visible while
  // the enabled-catalog list is still in flight.
  await screen.findByRole("button", { name: "Regenerate" });
  await waitFor(() => expect(releaseSkillsCatalog).toBeDefined());
  expect(skillDetailRequests).toEqual([]);

  releaseSkillsCatalog!();
  // Let the now-resolved catalog settle and any (wrongly) already-fired
  // detail query finish, then confirm it still never happened.
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(skillDetailRequests).toEqual([]);
});
