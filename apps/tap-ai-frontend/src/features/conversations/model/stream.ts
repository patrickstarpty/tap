import type { components } from "../../../shared/api/generated/schema";
import type { RetrievalAnswerResponse } from "../../knowledge/api/types";

export type ChatEventEnvelope = components["schemas"]["ChatEventEnvelope"];

/**
 * Mirrors the HTTP `GraphContextSummaryView` status enum (not the broader
 * `graph.context_ready` stream payload enum, which also allows
 * `"UNAVAILABLE"` and `"NOT_SELECTED"` for turns that never attempted graph
 * retrieval) — those two statuses carry nothing worth summarizing in the
 * answer activity line, so `reduceStreamEvent` treats them like any other
 * invalid status and leaves `graphContext` unchanged.
 *
 * `seedCount` counts every seed entity (question aliases plus retrieval
 * evidence); `querySeedCount` counts only the entities the question itself
 * named, and is 0 for events persisted before the field existed.
 */
export interface GraphContextSummary {
  status: "APPLIED" | "NOT_READY" | "STALE" | "FAILED" | "EMPTY";
  seedCount: number;
  querySeedCount: number;
  paths: string[][];
  relationCount: number;
}

const GRAPH_CONTEXT_STATUSES: ReadonlySet<GraphContextSummary["status"]> =
  new Set(["APPLIED", "NOT_READY", "STALE", "FAILED", "EMPTY"]);

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function parseGraphContextPayload(
  payload: Record<string, unknown>,
): GraphContextSummary | null {
  const status = payload.status;
  if (
    typeof status !== "string" ||
    !GRAPH_CONTEXT_STATUSES.has(status as GraphContextSummary["status"])
  ) {
    return null;
  }
  const querySeedCount = payload.querySeedCount ?? 0;
  if (
    !isNonNegativeInteger(payload.seedCount) ||
    !isNonNegativeInteger(querySeedCount) ||
    !isNonNegativeInteger(payload.relationCount)
  ) {
    return null;
  }
  const rawPaths = payload.paths;
  if (
    !Array.isArray(rawPaths) ||
    !rawPaths.every(
      (path) =>
        Array.isArray(path) && path.every((label) => typeof label === "string"),
    )
  ) {
    return null;
  }
  return {
    status: status as GraphContextSummary["status"],
    seedCount: payload.seedCount as number,
    querySeedCount,
    paths: (rawPaths as string[][]).slice(0, 3),
    relationCount: payload.relationCount as number,
  };
}

export interface StreamTurnState {
  answer: string;
  error: string | null;
  graphContext: GraphContextSummary | null;
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
}

export interface ConversationStreamState {
  lastSequence: number;
  turns: Readonly<Record<string, StreamTurnState>>;
}

const emptyTurn = (): StreamTurnState => ({
  answer: "",
  error: null,
  graphContext: null,
  lastSequence: 0,
  pendingCitations: [],
  response: null,
  status: "queued",
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
    !["completed", "abstained", "canceled", "failed"].includes(current.status)
  ) {
    next = {
      ...current,
      answer: current.answer + payload.text,
      status: "running",
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
    if (typeof answer === "object" && answer !== null) {
      const response = answer as RetrievalAnswerResponse;
      next = {
        ...current,
        answer: response.answer,
        pendingCitations: [],
        response: {
          ...response,
          citations:
            response.citations.length > 0
              ? response.citations
              : current.pendingCitations,
        },
        status: event.type === "turn.abstained" ? "abstained" : "completed",
      };
    }
  } else if (event.type === "graph.context_ready") {
    const graphContext = parseGraphContextPayload(payload);
    if (graphContext !== null) {
      next = { ...current, graphContext };
    }
  } else if (event.type === "turn.canceled") {
    next = { ...current, status: "canceled" };
  } else if (event.type === "conversation.turn.completed") {
    const outcome = payload.outcome;
    if (
      outcome === "completed" ||
      outcome === "abstained" ||
      outcome === "canceled" ||
      outcome === "failed"
    ) {
      next = { ...current, status: outcome };
    }
  } else if (event.type === "turn.failed") {
    next = { ...current, error: problemTitle(payload), status: "failed" };
  }

  return {
    lastSequence: sequence,
    turns: {
      ...state.turns,
      [turnId]: { ...next, lastSequence: sequence },
    },
  };
}

/**
 * Derives the latest valid `graph.context_ready` summary from a turn's
 * replayed history events (`GET /conversations/{id}/events`), mirroring the
 * live reduction in `reduceStreamEvent` so a reloaded page shows the same
 * answer summary line as a live stream would have produced.
 */
export function graphContextFromEvents(
  events: readonly { eventType: string; payload: Record<string, unknown> }[],
): GraphContextSummary | null {
  let latest: GraphContextSummary | null = null;
  for (const event of events) {
    if (event.eventType !== "graph.context_ready") continue;
    const graphContext = parseGraphContextPayload(event.payload);
    if (graphContext !== null) latest = graphContext;
  }
  return latest;
}
