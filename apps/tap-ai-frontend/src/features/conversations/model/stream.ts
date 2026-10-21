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

/**
 * The current attempt's in-progress draft text, assembled from `answer.draft`
 * frames. `attempt`/`seq` are the draft's own ordering keys (not the envelope
 * `sequence`) — a higher `attempt` than previously seen resets `text` and
 * starts tracking that attempt; within the same attempt, a `seq` at or below
 * the one already applied is a duplicate or out-of-order replay and is
 * ignored; `reset: true` clears `text` before continuing to append later
 * `seq`s of the same attempt (controller ruling, 2026-10-16).
 */
export interface DraftState {
  attempt: number;
  seq: number;
  text: string;
}

export interface CitationLabelInfo {
  title: string;
  location: string | null;
}

/**
 * One bounded tool-calling round's per-call progress, reduced from
 * `task.step` SSE frames (LangGraph PR 5, Task 9/10). The same `stepId`
 * arrives first as `"running"` and later as a terminal status
 * (`"completed"`/`"rejected"`/`"failed"`) — the terminal frame always
 * overwrites the running one in place rather than appending a second row.
 */
export interface TaskStepView {
  stepId: string;
  tool: string;
  status: "running" | "completed" | "rejected" | "failed";
  summary: string;
  durationMs: number;
  toolRound: number;
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
  /** The current attempt's in-progress draft, or `null` before the first
   * `answer.draft` frame arrives, or once the turn reaches a terminal
   * outcome (`turn.completed`/`turn.abstained` replace it with the
   * authoritative answer; `turn.canceled` moves it to `partialAnswer`). */
  draft: DraftState | null;
  /** `label` (`"S3"`, `"R1"`, …) to source title/location, accumulated from
   * `retrieval.hits_ready` payloads so a draft's inline `[S3]`/`[R1]`
   * markers can render as badges before the terminal answer's own
   * `citations` arrive. */
  citationLabels: Readonly<Record<string, CitationLabelInfo>>;
  /** Set by `stage.started {stage: "verify"}`, committed before the
   * verification step runs on the current attempt's draft. */
  stage: "verify" | null;
  /** The current attempt's already-redacted draft text at the moment
   * `turn.canceled` was recorded, or `null` when the turn was canceled
   * before any draft text existed. */
  partialAnswer: string | null;
  /** Tool-calling round steps for the current attempt, in first-seen
   * order; reset to `[]` when a higher-attempt draft arrives (a fresh
   * attempt's tool rounds are unrelated to a prior attempt's). */
  steps: readonly TaskStepView[];
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
  draft: null,
  citationLabels: {},
  stage: null,
  partialAnswer: null,
  steps: [],
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

/**
 * `draft`/`citationLabels` facts for one turn, taken independently of
 * `latestTurnState`'s whole-record `lastSequence` pick (review fix round 1,
 * finding I). `draft` is never persisted to MySQL, so `recoveredState`
 * (built from the durable event history) never carries one — on a
 * reconnect/page-reload mid-generation, `recoveredState`'s `lastSequence`
 * is typically higher than the freshly (re)connected `streamState`'s, so
 * `latestTurnState` would pick the recovered record and silently drop the
 * replayed draft and its labels. `citationLabels` is accumulated from the
 * durable `retrieval.hits_ready` event, which can land in either record
 * depending on whether the live stream replayed it since reconnecting, so
 * it is unioned from both instead of picked from one.
 */
export function mergedDraftFacts(
  turnId: string,
  recoveredState: ConversationStreamState,
  streamState: ConversationStreamState,
): {
  draft: DraftState | null;
  citationLabels: Readonly<Record<string, CitationLabelInfo>>;
} {
  const recovered = recoveredState.turns[turnId];
  const streamed = streamState.turns[turnId];
  return {
    draft: streamed?.draft ?? recovered?.draft ?? null,
    citationLabels: {
      ...(recovered?.citationLabels ?? {}),
      ...(streamed?.citationLabels ?? {}),
    },
  };
}

/**
 * Applies one `answer.draft` payload to a turn's current draft, per the
 * `(attempt, seq)` ordering rule: a higher `attempt` resets `text` and
 * starts tracking that attempt; a `seq` at or below the current attempt's
 * already-applied `seq` is a duplicate or out-of-order replay and is
 * ignored; `reset: true` clears `text` (to `payload.text`, which is empty on
 * a reset frame) instead of appending it.
 */
function reduceDraft(
  current: DraftState | null,
  payload: Record<string, unknown>,
): DraftState | null {
  const attempt = payload.attempt;
  const seq = payload.seq;
  const text = payload.text;
  if (
    typeof attempt !== "number" ||
    typeof seq !== "number" ||
    typeof text !== "string"
  ) {
    return current;
  }
  const reset = payload.reset === true;
  if (current === null || attempt > current.attempt) {
    return { attempt, seq, text };
  }
  if (attempt < current.attempt || seq <= current.seq) {
    return current;
  }
  return { attempt, seq, text: reset ? text : current.text + text };
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

  // `answer.draft` frames carry no SSE `id` and their envelope `sequence` is
  // merely the last *authoritative* sequence at send time (possibly already
  // applied) — unlike every other event type, they must never be dropped by
  // the `sequence <= state.lastSequence` staleness guard below, and must
  // never advance `lastSequence` themselves (controller ruling, 2026-10-16).
  const isDraft = event.type === "answer.draft";
  if (!isDraft && sequence <= state.lastSequence) {
    return state;
  }

  const current = state.turns[turnId] ?? emptyTurn();
  const payload = event.payload as Record<string, unknown>;
  let next = current;
  if (event.type === "turn.started") {
    next = { ...current, status: "running" };
  } else if (
    isDraft &&
    !["completed", "abstained", "canceled", "failed"].includes(current.status)
  ) {
    const draft = reduceDraft(current.draft, payload);
    // A higher attempt means the previous attempt's verification (if any)
    // no longer applies to the draft now showing — clear `stage` along with
    // the text reset so "Checking sources" cannot linger over a fresh
    // attempt's unverified draft (review fix round 1, minor finding).
    const isNewAttempt =
      typeof payload.attempt === "number" &&
      (current.draft === null || payload.attempt > current.draft.attempt);
    // Unlike `stage` (always null before the first draft, so clearing it
    // on that first frame is a no-op), tool-round `steps` are routinely
    // populated *before* the first draft frame of an attempt arrives (a
    // model's tool calls resolve before it drafts text) — resetting on
    // `isNewAttempt` would wipe that attempt's own steps the moment its
    // first draft frame shows up. Only an attempt genuinely higher than
    // one already tracked by a draft clears the prior attempt's steps.
    const attemptAdvanced =
      current.draft !== null &&
      typeof payload.attempt === "number" &&
      payload.attempt > current.draft.attempt;
    next = {
      ...current,
      draft,
      status: "running",
      stage: isNewAttempt ? null : current.stage,
      steps: attemptAdvanced ? [] : current.steps,
    };
  } else if (event.type === "task.step") {
    const stepId = payload.stepId;
    const tool = payload.tool;
    const status = payload.status;
    const summary = payload.summary;
    const durationMs = payload.durationMs;
    const toolRound = payload.toolRound;
    if (
      typeof stepId === "string" &&
      typeof tool === "string" &&
      (status === "running" ||
        status === "completed" ||
        status === "rejected" ||
        status === "failed") &&
      typeof summary === "string" &&
      typeof durationMs === "number" &&
      typeof toolRound === "number"
    ) {
      const step: TaskStepView = {
        stepId,
        tool,
        status,
        summary,
        durationMs,
        toolRound,
      };
      const existingIndex = current.steps.findIndex(
        (candidate) => candidate.stepId === stepId,
      );
      const steps =
        existingIndex === -1
          ? [...current.steps, step]
          : current.steps.map((candidate, index) =>
              index === existingIndex ? step : candidate,
            );
      next = { ...current, steps };
    }
  } else if (event.type === "retrieval.hits_ready") {
    const labels = payload.labels;
    if (Array.isArray(labels)) {
      const additions: Record<string, CitationLabelInfo> = {};
      for (const entry of labels) {
        if (
          typeof entry === "object" &&
          entry !== null &&
          typeof (entry as Record<string, unknown>).label === "string" &&
          typeof (entry as Record<string, unknown>).title === "string"
        ) {
          const label = (entry as Record<string, unknown>).label as string;
          const title = (entry as Record<string, unknown>).title as string;
          const location = (entry as Record<string, unknown>).location;
          additions[label] = {
            title,
            location: typeof location === "string" ? location : null,
          };
        }
      }
      next = {
        ...current,
        citationLabels: { ...current.citationLabels, ...additions },
      };
    }
  } else if (event.type === "stage.started" && payload.stage === "verify") {
    next = { ...current, stage: "verify" };
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
        draft: null,
        stage: null,
      };
    }
  } else if (event.type === "graph.context_ready") {
    const graphContext = parseGraphContextPayload(payload);
    if (graphContext !== null) {
      next = { ...current, graphContext };
    }
  } else if (event.type === "turn.canceled") {
    const partialAnswer = payload.partialAnswer;
    next = {
      ...current,
      status: "canceled",
      draft: null,
      stage: null,
      partialAnswer: typeof partialAnswer === "string" ? partialAnswer : null,
    };
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

  if (isDraft) {
    return {
      lastSequence: state.lastSequence,
      turns: { ...state.turns, [turnId]: next },
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
