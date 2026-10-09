import { useCallback, useEffect, useState } from "react";

/**
 * Carries a cited-path highlight (set by "View in Library" on an answer's
 * edge citation) through `window.history.state`, the same mechanism
 * `TapperWorkspace.tsx` already uses for its own navigation
 * (`window.history.pushState`). `graphVersion` is the *answer's* graph
 * version at the time it was generated, kept only to label the highlight
 * for debugging — it is never sent back to the server (`POST /highlight`
 * takes only `edgeIds`), since an answer can cite edges from a graph
 * version the project has since moved past. `projectId` is the project the
 * answer belonged to — `useGraphHighlightState` ignores a highlight whose
 * `projectId` doesn't match the project currently open (e.g. the user
 * switched projects before "View in Library" was clicked, or a stale
 * highlight survives a back/forward navigation into a different project).
 */
export interface GraphHighlightState {
  edgeIds: string[];
  graphVersion: string;
  turnId: string | null;
  projectId: string;
}

export const GRAPH_HIGHLIGHT_PATH = "/library/graph";

const MAX_HIGHLIGHT_EDGE_IDS = 20;

// `pushState` never dispatches a `popstate` event (only back/forward and
// `history.back()`/`forward()`/`go()` do), so a same-tab "View in Library"
// click would otherwise be invisible to `useGraphHighlightState` until the
// next unrelated popstate. `pushGraphHighlight` dispatches this custom
// event immediately after pushing, and the hook listens for both.
const GRAPH_HIGHLIGHT_EVENT = "tap:graph-highlight";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isValidHighlightState(value: unknown): value is GraphHighlightState {
  if (!isRecord(value)) return false;
  const { edgeIds, graphVersion, turnId, projectId } = value;
  if (!Array.isArray(edgeIds)) return false;
  if (edgeIds.length === 0 || edgeIds.length > MAX_HIGHLIGHT_EDGE_IDS) {
    return false;
  }
  if (!edgeIds.every((id) => typeof id === "string" && id.length > 0)) {
    return false;
  }
  if (typeof graphVersion !== "string") return false;
  if (turnId !== null && typeof turnId !== "string") return false;
  if (typeof projectId !== "string" || projectId.length === 0) return false;
  return true;
}

export function pushGraphHighlight(state: GraphHighlightState): void {
  window.history.pushState({ graphHighlight: state }, "", GRAPH_HIGHLIGHT_PATH);
  window.dispatchEvent(new Event(GRAPH_HIGHLIGHT_EVENT));
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
 * sync with back/forward navigation (`popstate`) and same-tab pushes (the
 * `tap:graph-highlight` event `pushGraphHighlight` dispatches). When
 * `projectId` is given, a highlight whose own `projectId` doesn't match is
 * ignored (reported as `null`) rather than shown against the wrong
 * project's graph. The returned `clear` callback both replaces the history
 * entry (so forward/back no longer restores the highlight) and updates
 * local state immediately — a `popstate` event is not dispatched by
 * `replaceState` itself.
 */
export function useGraphHighlightState(
  projectId?: string | null,
): [GraphHighlightState | null, () => void] {
  const [state, setState] = useState<GraphHighlightState | null>(() => {
    const highlight = readGraphHighlight();
    if (highlight === null) return null;
    if (projectId != null && highlight.projectId !== projectId) return null;
    return highlight;
  });

  useEffect(() => {
    const sync = () => {
      const highlight = readGraphHighlight();
      if (highlight === null) {
        setState(null);
        return;
      }
      setState(
        projectId != null && highlight.projectId !== projectId
          ? null
          : highlight,
      );
    };
    sync();
    window.addEventListener("popstate", sync);
    window.addEventListener(GRAPH_HIGHLIGHT_EVENT, sync);
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener(GRAPH_HIGHLIGHT_EVENT, sync);
    };
  }, [projectId]);

  const clear = useCallback(() => {
    clearGraphHighlight();
    setState(null);
  }, []);

  return [state, clear];
}
