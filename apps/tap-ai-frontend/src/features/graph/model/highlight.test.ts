import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import {
  clearGraphHighlight,
  GRAPH_HIGHLIGHT_PATH,
  pushGraphHighlight,
  readGraphHighlight,
  useGraphHighlightState,
  type GraphHighlightState,
} from "./highlight";

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

it("round-trips a highlight through history state and rejects malformed state", () => {
  const state: GraphHighlightState = {
    edgeIds: ["e1", "e2"],
    graphVersion: "3",
    turnId: "turn_1",
  };

  pushGraphHighlight(state);

  expect(window.location.pathname).toBe(GRAPH_HIGHLIGHT_PATH);
  expect(readGraphHighlight()).toEqual(state);

  window.history.replaceState({ graphHighlight: { edgeIds: "x" } }, "", "/");
  expect(readGraphHighlight()).toBeNull();
});

it("rejects an empty edgeIds array and a too-long one", () => {
  window.history.replaceState(
    { graphHighlight: { edgeIds: [], graphVersion: "1", turnId: null } },
    "",
    "/",
  );
  expect(readGraphHighlight()).toBeNull();

  window.history.replaceState(
    {
      graphHighlight: {
        edgeIds: Array.from({ length: 21 }, (_, i) => `e${i}`),
        graphVersion: "1",
        turnId: null,
      },
    },
    "",
    "/",
  );
  expect(readGraphHighlight()).toBeNull();
});

it("clears the highlight by replacing history state and path", () => {
  pushGraphHighlight({
    edgeIds: ["e1"],
    graphVersion: "1",
    turnId: null,
  });

  clearGraphHighlight();

  expect(readGraphHighlight()).toBeNull();
  expect(window.location.pathname).toBe("/");
});

it("reflects the current history state and updates on popstate", () => {
  pushGraphHighlight({ edgeIds: ["e1"], graphVersion: "1", turnId: null });

  const { result } = renderHook(() => useGraphHighlightState());
  expect(result.current[0]).toEqual({
    edgeIds: ["e1"],
    graphVersion: "1",
    turnId: null,
  });

  act(() => {
    window.history.replaceState(null, "", "/");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  expect(result.current[0]).toBeNull();
});

it("clears via the hook's clear function", () => {
  pushGraphHighlight({ edgeIds: ["e1"], graphVersion: "1", turnId: null });
  const { result } = renderHook(() => useGraphHighlightState());

  act(() => {
    result.current[1]();
  });

  expect(result.current[0]).toBeNull();
  expect(window.location.pathname).toBe("/");
});
