import type { DocumentSummary, RetrievalAnswerRequest } from "../api/types";

export const MAX_SELECTED_SOURCES = 20;

export interface SourceSelectionState {
  readonly selectedIds: readonly string[];
}

export const INITIAL_SOURCE_SELECTION: SourceSelectionState = {
  selectedIds: [],
};

type SourceSelectionEvent =
  | {
      type: "snapshotChanged";
      documents: readonly DocumentSummary[];
    }
  | { type: "toggle"; sourceId: string; readyIds: readonly string[] }
  | { type: "selectAllReady"; readyIds: readonly string[] }
  | { type: "clear" }
  | { type: "questionSubmitted" };

function compareIds(left: string, right: string): number {
  if (left < right) return -1;
  if (left > right) return 1;
  return 0;
}

function sortedUnique(values: readonly string[]): string[] {
  return [...new Set(values)].sort(compareIds);
}

function sameIds(left: readonly string[], right: readonly string[]): boolean {
  return (
    left.length === right.length &&
    left.every((sourceId, index) => sourceId === right[index])
  );
}

export function sourceSelectionReducer(
  state: SourceSelectionState,
  event: SourceSelectionEvent,
): SourceSelectionState {
  if (event.type === "questionSubmitted") return state;
  if (event.type === "clear") {
    return state.selectedIds.length === 0 ? state : { selectedIds: [] };
  }
  if (event.type === "snapshotChanged") {
    const ready = new Set(
      event.documents
        .filter((document) => document.status === "ready")
        .map((document) => document.documentId),
    );
    const selectedIds = sortedUnique(
      state.selectedIds.filter((sourceId) => ready.has(sourceId)),
    );
    return sameIds(state.selectedIds, selectedIds) ? state : { selectedIds };
  }
  if (event.type === "selectAllReady") {
    const selectedIds = sortedUnique(event.readyIds).slice(
      0,
      MAX_SELECTED_SOURCES,
    );
    return sameIds(state.selectedIds, selectedIds) ? state : { selectedIds };
  }

  if (state.selectedIds.includes(event.sourceId)) {
    return {
      selectedIds: state.selectedIds.filter(
        (sourceId) => sourceId !== event.sourceId,
      ),
    };
  }
  if (
    !event.readyIds.includes(event.sourceId) ||
    state.selectedIds.length >= MAX_SELECTED_SOURCES
  ) {
    return state;
  }
  return {
    selectedIds: sortedUnique([...state.selectedIds, event.sourceId]),
  };
}

export function buildAnswerRequest(
  question: string,
  selectedIds: readonly string[],
): RetrievalAnswerRequest {
  const query = question.trim();
  const queryLength = Array.from(query).length;
  const uniqueIds = new Set(selectedIds);
  if (queryLength < 1 || queryLength > 8_000) {
    throw new RangeError(
      "question must contain between 1 and 8,000 code points",
    );
  }
  if (
    selectedIds.length < 1 ||
    selectedIds.length > MAX_SELECTED_SOURCES ||
    uniqueIds.size !== selectedIds.length ||
    selectedIds.some((sourceId) => sourceId.length === 0)
  ) {
    throw new RangeError("answer requests require 1..20 unique document IDs");
  }

  return {
    query,
    answerMode: "quick",
    sources: ["doc"],
    resourceRefs: selectedIds.map((sourceId) => ({
      family: "doc",
      sourceId,
      mode: "scope",
    })),
  };
}
