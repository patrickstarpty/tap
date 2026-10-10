import { useCallback, useEffect, useMemo, useState } from "react";

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
 * project's graph. That filter is applied during render, so the value
 * returned always reflects the `projectId` passed in the same render — a
 * caller never sees an unfiltered highlight for a render in which
 * `projectId` has just resolved. A matching highlight keeps its identity
 * across a `projectId` change. The returned `clear` callback both replaces
 * the history entry (so forward/back no longer restores the highlight) and
 * updates local state immediately — a `popstate` event is not dispatched by
 * `replaceState` itself.
 */
export function useGraphHighlightState(
  projectId?: string | null,
): [GraphHighlightState | null, () => void] {
  const [highlight, setHighlight] = useState<GraphHighlightState | null>(
    readGraphHighlight,
  );

  useEffect(() => {
    const sync = () => setHighlight(readGraphHighlight());
    // Catch anything pushed between the initial read and subscribing, but
    // keep the current object when the entry is unchanged so its identity
    // only changes for a genuinely new highlight.
    setHighlight((current) => {
      const latest = readGraphHighlight();
      return sameHighlight(current, latest) ? current : latest;
    });
    window.addEventListener("popstate", sync);
    window.addEventListener(GRAPH_HIGHLIGHT_EVENT, sync);
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener(GRAPH_HIGHLIGHT_EVENT, sync);
    };
  }, []);

  const visible = useMemo(
    () =>
      highlight !== null &&
      projectId != null &&
      highlight.projectId !== projectId
        ? null
        : highlight,
    [highlight, projectId],
  );

  const clear = useCallback(() => {
    clearGraphHighlight();
    setHighlight(null);
  }, []);

  return [visible, clear];
}

function sameHighlight(
  a: GraphHighlightState | null,
  b: GraphHighlightState | null,
): boolean {
  if (a === b) return true;
  if (a === null || b === null) return false;
  return (
    a.projectId === b.projectId &&
    a.graphVersion === b.graphVersion &&
    a.turnId === b.turnId &&
    a.edgeIds.length === b.edgeIds.length &&
    a.edgeIds.every((id, index) => id === b.edgeIds[index])
  );
}
