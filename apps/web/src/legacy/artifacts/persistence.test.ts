import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createInitialArtifactState } from "./fixtures";
import {
  AUTOMATION_STORAGE_KEY,
  loadAutomationState,
  saveAutomationState,
} from "./persistence";

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

it.each([
  "not JSON",
  JSON.stringify({ version: 999, state: createInitialArtifactState() }),
  JSON.stringify({
    version: 1,
    state: { automations: [null], testPlans: [], runs: [] },
  }),
  JSON.stringify({
    version: 1,
    state: { ...createInitialArtifactState(), runs: [{ id: "broken" }] },
  }),
  JSON.stringify({
    version: 1,
    state: {
      ...createInitialArtifactState(),
      automations: [
        {
          ...createInitialArtifactState().automations[0],
          feature: { scenarios: [null] },
        },
      ],
    },
  }),
])(
  "falls back safely for an incompatible or malformed snapshot: %s",
  (snapshot) => {
    localStorage.setItem(AUTOMATION_STORAGE_KEY, snapshot);
    expect(loadAutomationState().automations.map(({ id }) => id)).toEqual([
      "AUTO-101",
      "AUTO-102",
    ]);
    expect(loadAutomationState().runs).toEqual([]);
  },
);

it("keeps the workspace usable when accessing browser storage throws", () => {
  vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
    throw new Error("storage blocked");
  });
  expect(loadAutomationState().automations).toHaveLength(2);
  expect(() => saveAutomationState(createInitialArtifactState())).not.toThrow();
});

it("keeps editing usable when the browser storage quota is exhausted", () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("quota exceeded");
  });
  expect(() => saveAutomationState(createInitialArtifactState())).not.toThrow();
});
