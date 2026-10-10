import { describe, expect, it } from "vitest";

import {
  createStreamState,
  graphContextFromEvents,
  isTargetTurnActive,
  latestTurnState,
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
  it("appends ordered deltas once and retains the resume sequence", () => {
    let state = createStreamState();
    state = reduceStreamEvent(
      state,
      envelope(2, "turn-1", "answer.delta", { text: "ground" }),
    );
    state = reduceStreamEvent(
      state,
      envelope(3, "turn-1", "answer.delta", { text: "ed" }),
    );
    state = reduceStreamEvent(
      state,
      envelope(3, "turn-1", "answer.delta", { text: "ed" }),
    );

    expect(state.lastSequence).toBe(3);
    expect(state.turns["turn-1"]?.answer).toBe("grounded");
  });

  it("closes canceled and failed turns without accepting later deltas", () => {
    const canceled = reduceStreamEvent(
      reduceStreamEvent(
        createStreamState(),
        envelope(1, "turn-1", "answer.delta", { text: "partial" }),
      ),
      envelope(2, "turn-1", "turn.canceled", {
        partialAnswerRetained: true,
      }),
    );
    const afterLateDelta = reduceStreamEvent(
      canceled,
      envelope(3, "turn-1", "answer.delta", { text: "ignored" }),
    );
    const failed = reduceStreamEvent(
      afterLateDelta,
      envelope(4, "turn-2", "turn.failed", {
        problem: { title: "Generation unavailable" },
      }),
    );

    expect(failed.turns["turn-1"]).toMatchObject({
      answer: "partial",
      status: "canceled",
    });
    expect(failed.turns["turn-2"]).toMatchObject({
      error: "Generation unavailable",
      status: "failed",
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
      envelope(4, "turn-2", "answer.delta", { text: "partial" }),
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
