import { describe, expect, it } from "vitest";

import {
  appendTurn,
  createConversation,
  isModelId,
  PENDING_MODEL_ID,
  type AssistantTurn,
} from "./model";

describe("Tapper workspace model", () => {
  it("accepts the same model names as the backend catalog rule", () => {
    expect(isModelId("4o-mini")).toBe(true);
    expect(isModelId("qwen3-vl-plus")).toBe(true);
    expect(isModelId("gpt-5.6-sol")).toBe(true);
    expect(isModelId("Qwen-Plus")).toBe(false);
    expect(isModelId("qwen_plus")).toBe(false);
    expect(isModelId("qwen--plus")).toBe(false);
    expect(isModelId("-qwen")).toBe(false);
    expect(isModelId("")).toBe(false);
    expect(isModelId("a".repeat(129))).toBe(false);
  });

  it("creates an empty conversation with independent context selections", () => {
    expect(createConversation("chat-2")).toMatchObject({
      id: "chat-2",
      modelId: PENDING_MODEL_ID,
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
