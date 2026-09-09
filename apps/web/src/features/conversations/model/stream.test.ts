import { describe, expect, it } from "vitest";

import { createStreamState, reduceStreamEvent } from "./stream";

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
});
