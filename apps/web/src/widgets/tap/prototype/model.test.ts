import { describe, expect, it } from "vitest";

import {
  appendTurn,
  createConversation,
  detectIntent,
  type AssistantTurn,
} from "./model";

describe("Athena prototype model", () => {
  it("classifies life-underwriting requests by their intended output", () => {
    expect(detectIntent("Create BDD tests for underwriting")).toBe("test-plan");
    expect(
      detectIntent("Generate a Playwright automation for a life application"),
    ).toBe("automation");
    expect(detectIntent("What evidence is needed for underwriting?")).toBe(
      "answer",
    );
  });

  it("creates an empty conversation with independent context selections", () => {
    expect(createConversation("chat-2")).toMatchObject({
      id: "chat-2",
      turns: [],
      selectedAgentIds: [],
      selectedSkillIds: [],
      selectedSourceIds: [],
    });
  });

  it("appends turns immutably without clearing an earlier conversation context", () => {
    const conversation = createConversation("chat-1", {
      selectedAgentIds: ["underwriting-reviewer"],
      selectedSkillIds: ["rules-lookup"],
      selectedSourceIds: ["life-underwriting-rules"],
      title: "Life underwriting evidence",
    });
    const turn: AssistantTurn = {
      id: "turn-1",
      intent: "answer",
      prompt: "What evidence is needed for life insurance underwriting?",
    };

    const nextConversation = appendTurn(conversation, turn);

    expect(nextConversation).toEqual({
      ...conversation,
      turns: [turn],
    });
    expect(nextConversation).not.toBe(conversation);
    expect(conversation.turns).toEqual([]);
    expect(nextConversation.selectedSourceIds).toEqual([
      "life-underwriting-rules",
    ]);
    expect(nextConversation.selectedAgentIds).toEqual([
      "underwriting-reviewer",
    ]);
    expect(nextConversation.selectedSkillIds).toEqual(["rules-lookup"]);
  });
});
