import type { components } from "../../../shared/api/generated/schema";
import type { RetrievalAnswerResponse } from "../../knowledge/api/types";

export type ChatEventEnvelope = components["schemas"]["ChatEventEnvelope"];

export interface StreamTurnState {
  error: string | null;
  lastSequence: number;
  /**
   * Citations resolved incrementally via `citation.resolved` events, before
   * the turn's full answer (with `claims`) is known. Never surfaced as
   * `response` on its own — `response` must stay `null` until a complete
   * `RetrievalAnswerResponse` arrives (`turn.completed`/`turn.abstained`),
   * so consumers can rely on a non-null `response` always having `claims`.
   */
  pendingCitations: RetrievalAnswerResponse["citations"][number][];
  response: RetrievalAnswerResponse | null;
  status:
    "queued" | "running" | "completed" | "abstained" | "canceled" | "failed";
  /**
   * Provisional answer text accumulated from `answer.delta` events. It is
   * never authoritative: `answer.reset` and every terminal event clear it, and
   * the persisted Turn replaces it after `turn.completed`.
   */
  streamingText: string | null;
}

export interface ConversationStreamState {
  lastSequence: number;
  turns: Readonly<Record<string, StreamTurnState>>;
}

const emptyTurn = (): StreamTurnState => ({
  error: null,
  lastSequence: 0,
  pendingCitations: [],
  response: null,
  status: "queued",
  streamingText: null,
});

export function createStreamState(): ConversationStreamState {
  return { lastSequence: 0, turns: {} };
}

const terminalStatuses: readonly StreamTurnState["status"][] = [
  "completed",
  "abstained",
  "canceled",
  "failed",
];

export function isTargetTurnActive({
  detailStatus,
  recoveredState,
  streamState,
  targetTurnId,
}: {
  detailStatus: string | null;
  recoveredState: ConversationStreamState;
  streamState: ConversationStreamState;
  targetTurnId: string | null;
}): boolean {
  if (targetTurnId === null) return false;
  const status =
    latestTurnState(targetTurnId, recoveredState, streamState)?.status ??
    detailStatus ??
    "queued";
  return !terminalStatuses.includes(status as StreamTurnState["status"]);
}

export function latestTurnState(
  turnId: string,
  recoveredState: ConversationStreamState,
  streamState: ConversationStreamState,
): StreamTurnState | undefined {
  const recovered = recoveredState.turns[turnId];
  const streamed = streamState.turns[turnId];
  if (recovered === undefined) return streamed;
  if (streamed === undefined) return recovered;
  return streamed.lastSequence >= recovered.lastSequence ? streamed : recovered;
}

function problemTitle(payload: Record<string, unknown>): string {
  const problem = payload.problem;
  return typeof problem === "object" &&
    problem !== null &&
    "title" in problem &&
    typeof problem.title === "string"
    ? problem.title
    : "The answer could not be generated.";
}

export function reduceStreamEvent(
  state: ConversationStreamState,
  envelope: ChatEventEnvelope | Record<string, unknown>,
): ConversationStreamState {
  const sequence = envelope.sequence;
  const turnId = envelope.turnId;
  const event = envelope.event;
  if (
    typeof sequence !== "number" ||
    sequence <= state.lastSequence ||
    typeof turnId !== "string" ||
    typeof event !== "object" ||
    event === null ||
    !("type" in event) ||
    !("payload" in event) ||
    typeof event.type !== "string" ||
    typeof event.payload !== "object" ||
    event.payload === null
  ) {
    return state;
  }

  const current = state.turns[turnId] ?? emptyTurn();
  const payload = event.payload as Record<string, unknown>;
  let next = current;
  if (event.type === "turn.started") {
    next = { ...current, status: "running" };
  } else if (
    event.type === "answer.delta" &&
    typeof payload.text === "string" &&
    !terminalStatuses.includes(current.status)
  ) {
    next = {
      ...current,
      status: "running",
      streamingText: (current.streamingText ?? "") + payload.text,
    };
  } else if (
    event.type === "answer.reset" &&
    !terminalStatuses.includes(current.status)
  ) {
    next = {
      ...current,
      pendingCitations: [],
      status: "running",
      streamingText: null,
    };
  } else if (event.type === "citation.resolved") {
    const citation = payload.citation;
    if (typeof citation === "object" && citation !== null) {
      next = {
        ...current,
        pendingCitations: [
          ...current.pendingCitations,
          citation as RetrievalAnswerResponse["citations"][number],
        ],
      };
    }
  } else if (
    event.type === "turn.completed" ||
    event.type === "turn.abstained"
  ) {
    const answer = payload.answer;
    const status = event.type === "turn.abstained" ? "abstained" : "completed";
    next = { ...current, status, streamingText: null };
    if (typeof answer === "object" && answer !== null) {
      const response = answer as RetrievalAnswerResponse;
      next = {
        ...next,
        pendingCitations: [],
        response: {
          ...response,
          citations:
            response.citations.length > 0
              ? response.citations
              : current.pendingCitations,
        },
      };
    }
  } else if (event.type === "turn.canceled") {
    next = { ...current, status: "canceled", streamingText: null };
  } else if (event.type === "conversation.turn.completed") {
    const outcome = payload.outcome;
    if (
      outcome === "completed" ||
      outcome === "abstained" ||
      outcome === "canceled" ||
      outcome === "failed"
    ) {
      next = { ...current, status: outcome, streamingText: null };
    }
  } else if (event.type === "turn.failed") {
    next = {
      ...current,
      error: problemTitle(payload),
      status: "failed",
      streamingText: null,
    };
  }

  return {
    lastSequence: sequence,
    turns: {
      ...state.turns,
      [turnId]: { ...next, lastSequence: sequence },
    },
  };
}
