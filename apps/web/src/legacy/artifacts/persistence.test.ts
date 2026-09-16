import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createInitialArtifactState } from "./fixtures";
import { createSimulatedRun } from "./state";
import {
  AUTOMATION_STORAGE_KEY,
  loadAutomationState,
  saveAutomationState,
} from "./persistence";

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

it("recovers pre-split edits and runs without modifying the legacy snapshot", () => {
  const initial = createInitialArtifactState();
  const automation = {
    ...initial.automations[0]!,
    title: "My preserved underwriting edits",
    revision: 7,
  };
  const run = createSimulatedRun({
    automation,
    id: "RUN-legacy",
    target: {
      kind: "web",
      executionAgentId: "ado-web-agent-03",
      label: "ADO Web Agent 03",
    },
    triggeredFrom: "automation",
    timestamp: "2026-09-14T00:00:00Z",
  });
  const legacy = JSON.stringify({
    version: 2,
    artifacts: { ...initial, automations: [automation], runs: [run] },
  });
  localStorage.setItem("tap.prototype.workspace.v2", legacy);

  const recovered = loadAutomationState();
  expect(recovered.automations[0]?.title).toBe(
    "My preserved underwriting edits",
  );
  expect(recovered.automations[0]?.revision).toBe(7);
  expect(recovered.runs[0]?.id).toBe("RUN-legacy");
  saveAutomationState(recovered);
  expect(localStorage.getItem("tap.prototype.workspace.v2")).toBe(legacy);
  expect(loadAutomationState().runs[0]?.id).toBe("RUN-legacy");
});

it("prefers an existing TAP snapshot over a legacy snapshot", () => {
  const initial = createInitialArtifactState();
  localStorage.setItem(
    "tap.prototype.workspace.v2",
    JSON.stringify({ version: 2, artifacts: { ...initial, automations: [] } }),
  );
  saveAutomationState(initial);
  expect(loadAutomationState().automations).toHaveLength(2);
});

it("rejects malformed nested legacy data without changing it", () => {
  const legacy = JSON.stringify({
    version: 2,
    artifacts: { automations: [null], testPlans: [], runs: [] },
  });
  localStorage.setItem("tap.prototype.workspace.v2", legacy);
  expect(loadAutomationState().automations).toHaveLength(2);
  expect(localStorage.getItem("tap.prototype.workspace.v2")).toBe(legacy);
});

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
