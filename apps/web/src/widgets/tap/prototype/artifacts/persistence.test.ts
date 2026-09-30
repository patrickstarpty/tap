import { expect, it } from "vitest";
import { readPrototypeSnapshot } from "./persistence";

it("restores an interrupted answer as retryable instead of permanently running", () => {
  const snapshot = readPrototypeSnapshot(JSON.stringify({
    version: 2,
    activeConversationId: "chat-1",
    conversations: [{ id: "chat-1", turns: [
      { id: "answer-1", answerState: "running" },
      { id: "answer-2", answerState: "completed" },
    ] }],
    artifacts: { automations: [], testPlans: [], runs: [] },
  }));
  expect(snapshot?.conversations[0]?.turns.map((turn) => turn.answerState))
    .toEqual(["failed", "completed"]);
});

it("defaults removedSourceIds to an empty array for an older snapshot", () => {
  const snapshot = readPrototypeSnapshot(JSON.stringify({
    version: 2,
    activeConversationId: "chat-1",
    conversations: [{ id: "chat-1", turns: [] }],
    artifacts: { automations: [], testPlans: [], runs: [] },
    library: {
      open: false,
      examplesLoaded: true,
      sampleLoaded: true,
      localSources: [],
    },
  }));
  expect(snapshot?.library?.removedSourceIds).toEqual([]);
});

it("restores removedSourceIds when present in the snapshot", () => {
  const snapshot = readPrototypeSnapshot(JSON.stringify({
    version: 2,
    activeConversationId: "chat-1",
    conversations: [{ id: "chat-1", turns: [] }],
    artifacts: { automations: [], testPlans: [], runs: [] },
    library: {
      open: false,
      examplesLoaded: true,
      sampleLoaded: true,
      localSources: [],
      removedSourceIds: ["sample-underwriting"],
    },
  }));
  expect(snapshot?.library?.removedSourceIds).toEqual(["sample-underwriting"]);
});
