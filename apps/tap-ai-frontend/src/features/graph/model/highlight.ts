import { useCallback, useEffect, useState } from "react";

/**
 * Carries a cited-path highlight (set by "View in Library" on an answer's
 * edge citation) through `window.history.state`, the same mechanism
 * `TapperWorkspace.tsx` already uses for its own navigation
 * (`window.history.pushState`). `graphVersion` is the *answer's* graph
 * version at the time it was generated, kept only to label the highlight
 * for debugging — it is never sent back to the server (`POST /highlight`
 * takes only `edgeIds`), since an answer can cite edges from a graph
 * version the project has since moved past.
 */
export interface GraphHighlightState {
  edgeIds: string[];
  graphVersion: string;
  turnId: string | null;
}

export const GRAPH_HIGHLIGHT_PATH = "/library/graph";

const MAX_HIGHLIGHT_EDGE_IDS = 20;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isValidHighlightState(value: unknown): value is GraphHighlightState {
  if (!isRecord(value)) return false;
  const { edgeIds, graphVersion, turnId } = value;
  if (!Array.isArray(edgeIds)) return false;
  if (edgeIds.length === 0 || edgeIds.length > MAX_HIGHLIGHT_EDGE_IDS) {
    return false;
  }
  if (!edgeIds.every((id) => typeof id === "string" && id.length > 0)) {
    return false;
  }
  if (typeof graphVersion !== "string") return false;
  if (turnId !== null && typeof turnId !== "string") return false;
  return true;
}

export function pushGraphHighlight(state: GraphHighlightState): void {
  window.history.pushState({ graphHighlight: state }, "", GRAPH_HIGHLIGHT_PATH);
}

export function readGraphHighlight(): GraphHighlightState | null {
  const historyState: unknown = window.history.state;
  if (!isRecord(historyState)) return null;
  const candidate = historyState.graphHighlight;
  return isValidHighlightState(candidate) ? candidate : null;
}

export function clearGraphHighlight(): void {
  window.history.replaceState(null, "", "/");
}

/**
 * Reads the current highlight from `window.history.state` and keeps it in
 * sync with back/forward navigation (`popstate`). The returned `clear`
 * callback both replaces the history entry (so forward/back no longer
 * restores the highlight) and updates local state immediately — a
 * `popstate` event is not dispatched by `replaceState` itself.
 */
export function useGraphHighlightState(): [
  GraphHighlightState | null,
  () => void,
] {
  const [state, setState] = useState<GraphHighlightState | null>(() =>
    readGraphHighlight(),
  );

  useEffect(() => {
    const onPopState = () => setState(readGraphHighlight());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const clear = useCallback(() => {
    clearGraphHighlight();
    setState(null);
  }, []);

  return [state, clear];
}
