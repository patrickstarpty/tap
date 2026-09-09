import type { components } from "../../../shared/api/generated/schema";
import type { RetrievalAnswerResponse } from "../../knowledge/api/types";

export type ChatEventEnvelope = components["schemas"]["ChatEventEnvelope"];

export interface StreamTurnState {
  answer: string;
  error: string | null;
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
  response: null,
  status: "queued",
});

export function createStreamState(): ConversationStreamState {
  return { lastSequence: 0, turns: {} };
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
      const response =
        current.response ??
        ({ citations: [] } as unknown as RetrievalAnswerResponse);
      next = {
        ...current,
        response: {
          ...response,
          citations: [
            ...response.citations,
            citation as RetrievalAnswerResponse["citations"][number],
          ],
        },
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
        response: {
          ...response,
          citations:
            response.citations.length > 0
              ? response.citations
              : (current.response?.citations ?? []),
        },
        status: event.type === "turn.abstained" ? "abstained" : "completed",
      };
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
    turns: { ...state.turns, [turnId]: next },
  };
}
