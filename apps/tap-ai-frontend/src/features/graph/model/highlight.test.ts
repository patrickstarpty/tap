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
    projectId: "proj_1",
  };

  pushGraphHighlight(state);

  expect(window.location.pathname).toBe(GRAPH_HIGHLIGHT_PATH);
  expect(readGraphHighlight()).toEqual(state);

  window.history.replaceState({ graphHighlight: { edgeIds: "x" } }, "", "/");
  expect(readGraphHighlight()).toBeNull();
});

it("rejects an empty edgeIds array, a too-long one, and a missing projectId", () => {
  window.history.replaceState(
    {
      graphHighlight: {
        edgeIds: [],
        graphVersion: "1",
        turnId: null,
        projectId: "proj_1",
      },
    },
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
        projectId: "proj_1",
      },
    },
    "",
    "/",
  );
  expect(readGraphHighlight()).toBeNull();

  window.history.replaceState(
    {
      graphHighlight: { edgeIds: ["e1"], graphVersion: "1", turnId: null },
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
    projectId: "proj_1",
  });

  clearGraphHighlight();

  expect(readGraphHighlight()).toBeNull();
  expect(window.location.pathname).toBe("/");
});

it("reflects the current history state and updates on popstate", () => {
  pushGraphHighlight({
    edgeIds: ["e1"],
    graphVersion: "1",
    turnId: null,
    projectId: "proj_1",
  });

  const { result } = renderHook(() => useGraphHighlightState("proj_1"));
  expect(result.current[0]).toEqual({
    edgeIds: ["e1"],
    graphVersion: "1",
    turnId: null,
    projectId: "proj_1",
  });

  act(() => {
    window.history.replaceState(null, "", "/");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  expect(result.current[0]).toBeNull();
});

it("clears via the hook's clear function", () => {
  pushGraphHighlight({
    edgeIds: ["e1"],
    graphVersion: "1",
    turnId: null,
    projectId: "proj_1",
  });
  const { result } = renderHook(() => useGraphHighlightState("proj_1"));

  act(() => {
    result.current[1]();
  });

  expect(result.current[0]).toBeNull();
  expect(window.location.pathname).toBe("/");
});

it("updates state in the same tab when pushGraphHighlight is called, without waiting for popstate", () => {
  const { result } = renderHook(() => useGraphHighlightState("proj_1"));
  expect(result.current[0]).toBeNull();

  act(() => {
    pushGraphHighlight({
      edgeIds: ["e1"],
      graphVersion: "2",
      turnId: "turn_1",
      projectId: "proj_1",
    });
  });

  expect(result.current[0]).toEqual({
    edgeIds: ["e1"],
    graphVersion: "2",
    turnId: "turn_1",
    projectId: "proj_1",
  });
});

it("ignores a highlight pushed for a different project", () => {
  const { result } = renderHook(() => useGraphHighlightState("proj_1"));

  act(() => {
    pushGraphHighlight({
      edgeIds: ["e1"],
      graphVersion: "2",
      turnId: null,
      projectId: "proj_2",
    });
  });

  expect(result.current[0]).toBeNull();
});

it("returns the highlight regardless of project when no projectId filter is given", () => {
  const { result } = renderHook(() => useGraphHighlightState());

  act(() => {
    pushGraphHighlight({
      edgeIds: ["e1"],
      graphVersion: "2",
      turnId: null,
      projectId: "proj_2",
    });
  });

  expect(result.current[0]).not.toBeNull();
});
