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
