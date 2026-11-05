import { describe, expect, it } from "vitest";

import {
  createStreamState,
  graphContextFromEvents,
  isTargetTurnActive,
  latestTurnState,
  mergedDraftFacts,
  reduceStreamEvent,
} from "./stream";

const envelope = (
  sequence: number,
  turnId: string,
  type: string,
  payload: Record<string, unknown>,
) => ({
  eventId: `event-${sequence}`,
  sequence,
  chatId: "conversation-1",
  turnId,
  occurredAt: `2026-09-09T00:00:0${sequence}Z`,
  schemaVersion: 1,
  event: { type, payload },
});

describe("conversation stream reducer", () => {
  it("appends draft text ordered by (attempt, seq) and never advances lastSequence", () => {
    let state = createStreamState();
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "answer.draft", {
        attempt: 1,
        seq: 1,
        text: "ground",
      }),
    );
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "answer.draft", {
        attempt: 1,
        seq: 2,
        text: "ed",
      }),
    );

    expect(state.lastSequence).toBe(0);
    expect(state.turns["turn-1"]?.draft).toEqual({
      attempt: 1,
      seq: 2,
      text: "grounded",
    });
    expect(state.turns["turn-1"]?.lastSequence).toBe(0);
  });

  it("ignores a duplicate or out-of-order draft seq within the same attempt", () => {
    let state = createStreamState();
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "answer.draft", {
        attempt: 1,
        seq: 5,
        text: "abc",
      }),
    );
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "answer.draft", {
        attempt: 1,
        seq: 3,
        text: "ignored",
      }),
    );
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "answer.draft", {
        attempt: 1,
        seq: 5,
        text: "ignored",
      }),
    );

    expect(state.turns["turn-1"]?.draft).toEqual({
      attempt: 1,
      seq: 5,
      text: "abc",
    });
  });

  it("resets the draft text when a higher attempt arrives", () => {
    let state = createStreamState();
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "answer.draft", {
        attempt: 1,
        seq: 2,
        text: "stale attempt text",
      }),
    );
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "answer.draft", {
        attempt: 2,
        seq: 1,
        text: "fresh",
      }),
    );

    expect(state.turns["turn-1"]?.draft).toEqual({
      attempt: 2,
      seq: 1,
      text: "fresh",
    });
  });

  it("clears a pending verify stage when a higher-attempt draft arrives", () => {
    let state = createStreamState();
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "answer.draft", {
        attempt: 1,
        seq: 1,
        text: "attempt one",
      }),
    );
    state = reduceStreamEvent(
      state,
      envelope(2, "turn-1", "stage.started", { stage: "verify" }),
    );
    expect(state.turns["turn-1"]?.stage).toBe("verify");

    state = reduceStreamEvent(
      state,
      envelope(2, "turn-1", "answer.draft", {
        attempt: 2,
        seq: 1,
        text: "attempt two",
      }),
    );

    expect(state.turns["turn-1"]?.stage).toBeNull();
    expect(state.turns["turn-1"]?.draft).toEqual({
      attempt: 2,
      seq: 1,
      text: "attempt two",
    });
  });

  it("clears draft text on a reset frame and keeps appending later seqs", () => {
    let state = createStreamState();
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "answer.draft", {
        attempt: 1,
        seq: 1,
        text: "before reset",
      }),
    );
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "answer.draft", {
        attempt: 1,
        seq: 2,
        text: "",
        reset: true,
      }),
    );
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "answer.draft", {
        attempt: 1,
        seq: 3,
        text: "after reset",
      }),
    );

    expect(state.turns["turn-1"]?.draft).toEqual({
      attempt: 1,
      seq: 3,
      text: "after reset",
    });
  });

  it("I-2: clears stale pre-disconnect text when a reconnect replay delivers a reset at a higher seq", () => {
    // The client applied seq 1..10 of attempt 1, then disconnected. While it
    // was gone, the batcher wrote a reset at seq 11 and resent the answer
    // from the start as seq 12..15. On reconnect, the backend replay now
    // includes the reset chunk (11) ahead of 12..15 -- the fix for I-2 --
    // and this reducer must clear the stale seq 1..10 text on that reset
    // frame instead of appending the fresh text after it.
    let state = createStreamState();
    for (let seq = 1; seq <= 10; seq += 1) {
      state = reduceStreamEvent(
        state,
        envelope(1, "turn-1", "answer.draft", {
          attempt: 1,
          seq,
          text: `chunk-${seq}`,
        }),
      );
    }

    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "answer.draft", {
        attempt: 1,
        seq: 11,
        text: "",
        reset: true,
      }),
    );
    for (const [seq, text] of [
      [12, "re-"],
      [13, "a"],
      [14, "n"],
      [15, "swer"],
    ] as const) {
      state = reduceStreamEvent(
        state,
        envelope(1, "turn-1", "answer.draft", { attempt: 1, seq, text }),
      );
    }

    expect(state.turns["turn-1"]?.draft).toEqual({
      attempt: 1,
      seq: 15,
      text: "re-answer",
    });
  });

  it("fills citationLabels from retrieval.hits_ready without requiring every hit to carry one", () => {
    let state = createStreamState();
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "retrieval.hits_ready", {
        traceId: "trace-1",
        authorizedHitCount: 3,
        labels: [
          { label: "S1", title: "Policy handbook", location: "p. 4" },
          { label: "R1", title: "Rule graph", location: null },
        ],
      }),
    );

    expect(state.turns["turn-1"]?.citationLabels).toEqual({
      S1: { title: "Policy handbook", location: "p. 4" },
      R1: { title: "Rule graph", location: null },
    });
  });

  it("sets the verify stage from stage.started and clears it once the turn completes", () => {
    let state = createStreamState();
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "stage.started", { stage: "verify" }),
    );
    expect(state.turns["turn-1"]?.stage).toBe("verify");

    state = reduceStreamEvent(
      state,
      envelope(2, "turn-1", "turn.completed", {
        answer: { answer: "done", citations: [] },
      }),
    );
    expect(state.turns["turn-1"]?.stage).toBeNull();
  });

  it("replaces the draft with the terminal answer on completion", () => {
    let state = createStreamState();
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "answer.draft", {
        attempt: 1,
        seq: 1,
        text: "partial draft text",
      }),
    );
    state = reduceStreamEvent(
      state,
      envelope(2, "turn-1", "turn.completed", {
        answer: { answer: "Final answer.", citations: [] },
      }),
    );

    expect(state.turns["turn-1"]?.draft).toBeNull();
    expect(state.turns["turn-1"]?.response?.answer).toBe("Final answer.");
    expect(state.turns["turn-1"]?.status).toBe("completed");
  });

  it("closes canceled and failed turns, keeping the partial answer and refusing later drafts", () => {
    const canceled = reduceStreamEvent(
      reduceStreamEvent(
        createStreamState(),
        envelope(1, "turn-1", "answer.draft", {
          attempt: 1,
          seq: 1,
          text: "partial",
        }),
      ),
      envelope(2, "turn-1", "turn.canceled", {
        partialAnswer: "partial",
        partialAnswerRetained: true,
      }),
    );
    const afterLateDraft = reduceStreamEvent(
      canceled,
      envelope(3, "turn-1", "answer.draft", {
        attempt: 1,
        seq: 2,
        text: "ignored",
      }),
    );
    const failed = reduceStreamEvent(
      afterLateDraft,
      envelope(4, "turn-2", "turn.failed", {
        problem: { title: "Generation unavailable" },
      }),
    );

    expect(failed.turns["turn-1"]).toMatchObject({
      partialAnswer: "partial",
      draft: null,
      status: "canceled",
    });
    expect(failed.turns["turn-2"]).toMatchObject({
      error: "Generation unavailable",
      status: "failed",
    });
  });

  it("keeps partialAnswer null when a turn is canceled before any draft text", () => {
    const canceled = reduceStreamEvent(
      createStreamState(),
      envelope(1, "turn-1", "turn.canceled", {
        partialAnswerRetained: false,
      }),
    );

    expect(canceled.turns["turn-1"]).toMatchObject({
      partialAnswer: null,
      draft: null,
      status: "canceled",
    });
  });

  it("maps the durable lifecycle completion outcome to canceled", () => {
    const canceled = reduceStreamEvent(
      reduceStreamEvent(
        createStreamState(),
        envelope(1, "turn-1", "turn.started", { state: "running" }),
      ),
      envelope(2, "turn-1", "conversation.turn.completed", {
        turnId: "turn-1",
        answerEvidenceSnapshotId: "snapshot-1",
        answerEvidenceSnapshotDigest: `sha256:${"a".repeat(64)}`,
        outcome: "canceled",
      }),
    );

    expect(canceled.turns["turn-1"]?.status).toBe("canceled");
  });

  it("does not surface a response until the turn actually completes", () => {
    const state = reduceStreamEvent(
      createStreamState(),
      envelope(1, "turn-1", "citation.resolved", {
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
      }),
    );

    // A citation resolving early must never hand callers a `response` that is
    // missing `claims`/`answer` — every consumer of `StreamTurnState.response`
    // treats a non-null value as a complete `RetrievalAnswerResponse` (see
    // TapperWorkspace.tsx's AssistantResponse, which crashed on
    // `turn.response.claims.flatMap(...)` when this held a partial object).
    expect(state.turns["turn-1"]?.response).toBeNull();
    expect(state.turns["turn-1"]?.pendingCitations).toHaveLength(1);
  });

  it("keeps resolved citation identity with the completed answer", () => {
    let state = createStreamState();
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "citation.resolved", {
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
      }),
    );
    state = reduceStreamEvent(
      state,
      envelope(2, "turn-1", "turn.completed", {
        answer: {
          traceId: "trace-1",
          queryPlanId: "plan-1",
          contextSnapshotId: "context-1",
          corpusVersion: "v1",
          retrievalProfileId: "quick",
          degradedMode: false,
          answer: "A rule applies.",
          abstained: false,
          claims: [
            {
              claimId: "claim-1",
              text: "A rule applies.",
              citationIds: ["citation-1"],
            },
          ],
          citations: [],
        },
      }),
    );

    expect(state.turns["turn-1"]?.response?.citations).toHaveLength(1);
    expect(state.turns["turn-1"]?.status).toBe("completed");
  });

  it("stops an active target when newer stream facts supersede stale queued detail", () => {
    const streamState = reduceStreamEvent(
      createStreamState(),
      envelope(8, "turn-2", "conversation.turn.completed", {
        outcome: "completed",
      }),
    );

    expect(
      isTargetTurnActive({
        detailStatus: "queued",
        recoveredState: createStreamState(),
        streamState,
        targetTurnId: "turn-2",
      }),
    ).toBe(false);
  });

  it("uses recovered terminal facts before stale detail and keeps a genuinely running target active", () => {
    const recoveredState = reduceStreamEvent(
      createStreamState(),
      envelope(7, "turn-2", "turn.completed", {
        answer: { answer: "done", citations: [] },
      }),
    );
    const staleStreamState = reduceStreamEvent(
      createStreamState(),
      envelope(4, "turn-2", "turn.started", { state: "running" }),
    );

    expect(
      isTargetTurnActive({
        detailStatus: "running",
        recoveredState,
        streamState: staleStreamState,
        targetTurnId: "turn-2",
      }),
    ).toBe(false);
    expect(
      latestTurnState("turn-2", recoveredState, staleStreamState)?.answer,
    ).toBe("done");
    expect(
      isTargetTurnActive({
        detailStatus: "running",
        recoveredState: createStreamState(),
        streamState: createStreamState(),
        targetTurnId: "turn-3",
      }),
    ).toBe(true);
  });

  it("keeps the latest valid graph context and ignores malformed payloads", () => {
    let state = createStreamState();
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "graph.context_ready", {
        status: "EMPTY",
        seedCount: 2,
        paths: [],
        relationCount: 0,
      }),
    );
    state = reduceStreamEvent(
      state,
      envelope(2, "turn-1", "graph.context_ready", {
        status: "???",
        seedCount: 5,
        paths: [],
        relationCount: 5,
      }),
    );

    expect(state.turns["turn-1"]?.graphContext).toEqual({
      status: "EMPTY",
      seedCount: 2,
      querySeedCount: 0,
      paths: [],
      relationCount: 0,
    });
  });

  it("keeps the question's own seed count apart from the total seed count", () => {
    let state = createStreamState();
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "graph.context_ready", {
        status: "EMPTY",
        seedCount: 4,
        querySeedCount: 2,
        paths: [],
        relationCount: 0,
      }),
    );
    expect(state.turns["turn-1"]?.graphContext).toMatchObject({
      seedCount: 4,
      querySeedCount: 2,
    });

    state = reduceStreamEvent(
      state,
      envelope(2, "turn-1", "graph.context_ready", {
        status: "EMPTY",
        seedCount: 4,
        querySeedCount: -1,
        paths: [],
        relationCount: 0,
      }),
    );
    expect(state.turns["turn-1"]?.graphContext?.querySeedCount).toBe(2);
  });

  it("ignores a graph context event with a negative count or non-string path labels", () => {
    let state = createStreamState();
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "graph.context_ready", {
        status: "APPLIED",
        seedCount: -1,
        paths: [],
        relationCount: 0,
      }),
    );
    expect(state.turns["turn-1"]?.graphContext).toBeNull();

    state = reduceStreamEvent(
      state,
      envelope(2, "turn-1", "graph.context_ready", {
        status: "APPLIED",
        seedCount: 1,
        paths: [[1, 2]],
        relationCount: 1,
      }),
    );
    expect(state.turns["turn-1"]?.graphContext).toBeNull();
  });

  it("caps graph context paths at three entries", () => {
    let state = createStreamState();
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "graph.context_ready", {
        status: "APPLIED",
        seedCount: 2,
        paths: [
          ["A", "B"],
          ["B", "C"],
          ["C", "D"],
          ["D", "E"],
        ],
        relationCount: 4,
      }),
    );
    expect(state.turns["turn-1"]?.graphContext?.paths).toHaveLength(3);
  });
});

describe("task.step reduction", () => {
  it("reduces task.step running then completed into one step", () => {
    let state = createStreamState();
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "task.step", {
        stepId: "turn-1:r1:c0",
        tool: "knowledge.search",
        status: "running",
        summary: "",
        durationMs: 0,
        toolRound: 1,
      }),
    );
    expect(state.turns["turn-1"]?.steps).toEqual([
      {
        stepId: "turn-1:r1:c0",
        tool: "knowledge.search",
        status: "running",
        summary: "",
        durationMs: 0,
        toolRound: 1,
      },
    ]);

    state = reduceStreamEvent(
      state,
      envelope(2, "turn-1", "task.step", {
        stepId: "turn-1:r1:c0",
        tool: "knowledge.search",
        status: "completed",
        summary: "Found 3 hits",
        durationMs: 120,
        toolRound: 1,
      }),
    );

    expect(state.turns["turn-1"]?.steps).toEqual([
      {
        stepId: "turn-1:r1:c0",
        tool: "knowledge.search",
        status: "completed",
        summary: "Found 3 hits",
        durationMs: 120,
        toolRound: 1,
      },
    ]);
  });

  it("keeps steps from an earlier attempt once a newer attempt's own steps and first draft arrive (review fix round 1)", () => {
    // `turn.started` carries no `attempt` (`TurnStartedPayload = {state}`
    // only), so there is no reliable signal to key a reset off — `steps`
    // is never reset across attempts; `stepId` upsert is the only thing
    // that keeps live and recovered (durable-replay) state in agreement.
    let state = createStreamState();
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "answer.draft", {
        attempt: 1,
        seq: 1,
        text: "attempt one",
      }),
    );
    state = reduceStreamEvent(
      state,
      envelope(2, "turn-1", "task.step", {
        stepId: "turn-1:r1:c0",
        tool: "graph.relations",
        status: "running",
        summary: "",
        durationMs: 0,
        toolRound: 1,
      }),
    );
    expect(state.turns["turn-1"]?.steps).toEqual([
      {
        stepId: "turn-1:r1:c0",
        tool: "graph.relations",
        status: "running",
        summary: "",
        durationMs: 0,
        toolRound: 1,
      },
    ]);

    // Attempt 2's own tool-round steps arrive before its first draft frame
    // (the realistic ordering: a model's tool calls resolve before it
    // drafts text).
    state = reduceStreamEvent(
      state,
      envelope(3, "turn-1", "task.step", {
        stepId: "turn-1:r2:c0",
        tool: "insights.query",
        status: "running",
        summary: "",
        durationMs: 0,
        toolRound: 2,
      }),
    );
    state = reduceStreamEvent(
      state,
      envelope(4, "turn-1", "answer.draft", {
        attempt: 2,
        seq: 1,
        text: "attempt two",
      }),
    );

    // Attempt 1's step is retained, not wiped by attempt 2 starting.
    expect(
      state.turns["turn-1"]?.steps.find(
        (step) => step.stepId === "turn-1:r1:c0",
      ),
    ).toBeDefined();
    // A same-stepId terminal frame still overwrites the running one, not
    // appends a second row.
    state = reduceStreamEvent(
      state,
      envelope(5, "turn-1", "task.step", {
        stepId: "turn-1:r2:c0",
        tool: "insights.query",
        status: "completed",
        summary: "3 metrics",
        durationMs: 40,
        toolRound: 2,
      }),
    );
    expect(state.turns["turn-1"]?.steps).toEqual([
      {
        stepId: "turn-1:r1:c0",
        tool: "graph.relations",
        status: "running",
        summary: "",
        durationMs: 0,
        toolRound: 1,
      },
      {
        stepId: "turn-1:r2:c0",
        tool: "insights.query",
        status: "completed",
        summary: "3 metrics",
        durationMs: 40,
        toolRound: 2,
      },
    ]);
  });

  it("hits_ready with stepId merges labels", () => {
    let state = createStreamState();
    state = reduceStreamEvent(
      state,
      envelope(1, "turn-1", "retrieval.hits_ready", {
        traceId: "trace-1",
        authorizedHitCount: 1,
        stepId: "turn-1:r1:c0",
        labels: [{ label: "S1", title: "Policy handbook", location: null }],
      }),
    );

    expect(state.turns["turn-1"]?.citationLabels).toEqual({
      S1: { title: "Policy handbook", location: null },
    });
  });
});

describe("mergedDraftFacts", () => {
  it("takes the draft from the live stream (never the recovered record) after a reconnect mid-generation", () => {
    // Simulates a page reload while a turn is still generating: the
    // recovered record (GET /conversations/{id}/events, durable events
    // only) has already seen `retrieval.hits_ready` at a higher sequence
    // than anything the freshly reconnected stream has processed yet,
    // because `answer.draft` is never persisted and so never advances
    // `lastSequence` on the live side.
    const recoveredState = reduceStreamEvent(
      reduceStreamEvent(
        createStreamState(),
        envelope(1, "turn-1", "turn.started", { state: "running" }),
      ),
      envelope(2, "turn-1", "retrieval.hits_ready", {
        traceId: "trace-1",
        authorizedHitCount: 2,
        labels: [{ label: "S1", title: "Policy handbook", location: "p. 4" }],
      }),
    );
    const streamState = reduceStreamEvent(
      createStreamState(),
      envelope(2, "turn-1", "answer.draft", {
        attempt: 1,
        seq: 3,
        text: "Replayed draft text.",
      }),
    );

    // `latestTurnState` picks the whole recovered record here, which has
    // no draft at all -- this is the bug `mergedDraftFacts` exists to work
    // around, not a property of `mergedDraftFacts` itself.
    expect(
      latestTurnState("turn-1", recoveredState, streamState)?.draft,
    ).toBeNull();

    const facts = mergedDraftFacts("turn-1", recoveredState, streamState);
    expect(facts.draft).toEqual({
      attempt: 1,
      seq: 3,
      text: "Replayed draft text.",
    });
    expect(facts.citationLabels).toEqual({
      S1: { title: "Policy handbook", location: "p. 4" },
    });
  });

  it("unions citationLabels seen by only one of the two records", () => {
    const recoveredState = reduceStreamEvent(
      createStreamState(),
      envelope(1, "turn-1", "retrieval.hits_ready", {
        traceId: "trace-1",
        authorizedHitCount: 1,
        labels: [
          { label: "S1", title: "Recovered-only source", location: null },
        ],
      }),
    );
    const streamState = reduceStreamEvent(
      createStreamState(),
      envelope(1, "turn-1", "retrieval.hits_ready", {
        traceId: "trace-1",
        authorizedHitCount: 1,
        labels: [
          { label: "R1", title: "Stream-only relation", location: null },
        ],
      }),
    );

    const facts = mergedDraftFacts("turn-1", recoveredState, streamState);
    expect(facts.citationLabels).toEqual({
      S1: { title: "Recovered-only source", location: null },
      R1: { title: "Stream-only relation", location: null },
    });
  });

  it("returns a null draft and empty citationLabels when neither record has the turn", () => {
    const facts = mergedDraftFacts(
      "turn-1",
      createStreamState(),
      createStreamState(),
    );
    expect(facts.draft).toBeNull();
    expect(facts.citationLabels).toEqual({});
  });
});

describe("graphContextFromEvents", () => {
  it("returns the last valid graph.context_ready payload from replayed history", () => {
    const result = graphContextFromEvents([
      { eventType: "turn.started", payload: {} },
      {
        eventType: "graph.context_ready",
        payload: {
          status: "EMPTY",
          seedCount: 2,
          paths: [["核保流程", "健康告知"]],
          relationCount: 0,
        },
      },
      {
        eventType: "graph.context_ready",
        payload: { status: "???", seedCount: 1, paths: [], relationCount: 1 },
      },
    ]);
    expect(result).toEqual({
      status: "EMPTY",
      seedCount: 2,
      querySeedCount: 0,
      paths: [["核保流程", "健康告知"]],
      relationCount: 0,
    });
  });

  it("returns null when no graph.context_ready event is present", () => {
    expect(
      graphContextFromEvents([{ eventType: "turn.started", payload: {} }]),
    ).toBeNull();
  });
});
