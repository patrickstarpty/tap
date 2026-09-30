import { describe, expect, it } from "vitest";

import { appendTurn, createConversation, type AssistantTurn } from "./model";

describe("Tapper workspace model", () => {
  it("creates an empty conversation with independent context selections", () => {
    expect(createConversation("chat-2")).toMatchObject({
      id: "chat-2",
      modelId: "tapper-chat",
      turns: [],
      selectedAgentIds: [],
      selectedSkillIds: [],
      selectedSourceIds: [],
    });
  });

  it("records the turn and clears only the pending message context", () => {
    const conversation = createConversation("chat-1", {
      selectedAgentIds: ["underwriting-reviewer"],
      selectedSkillIds: ["rules-lookup"],
      selectedSourceIds: ["life-underwriting-rules"],
      title: "Life underwriting evidence",
    });
    const turn: AssistantTurn = {
      id: "turn-1",
      intent: "answer",
      locale: "en",
      modelId: "gpt-5.6-sol",
      prompt: "What evidence is needed for life insurance underwriting?",
      sourceReferences: [
        {
          id: "life-underwriting-rules",
          name: "life-underwriting-rules.md",
          origin: "knowledge-base",
        },
      ],
    };

    const nextConversation = appendTurn(conversation, turn);

    expect(nextConversation).toEqual({
      ...conversation,
      turns: [turn],
      selectedSourceIds: [],
      selectedAgentIds: [],
      selectedSkillIds: [],
    });
    expect(nextConversation).not.toBe(conversation);
    expect(conversation.turns).toEqual([]);
    expect(nextConversation.selectedSourceIds).toEqual([]);
    expect(nextConversation.selectedAgentIds).toEqual([]);
    expect(nextConversation.selectedSkillIds).toEqual([]);
    expect(conversation.selectedSourceIds).toEqual(["life-underwriting-rules"]);
  });
});
