import { describe, expect, it } from "vitest";

import {
  appendTurn,
  createConversation,
  detectAutomationType,
  detectIntent,
  type AssistantTurn,
} from "./model";

describe("Athena prototype model", () => {
  it.each([
    ["Generate a Playwright automation", "web"],
    ["Create a browser workflow", "web"],
    ["为移动端生成自动化脚本", "mobile"],
    ["Create an iOS automation", "mobile"],
    ["Create an automation script", null],
    ["Create a Web and Mobile automation", null],
  ] as const)("infers the automation channel for %s", (prompt, expected) => {
    expect(detectAutomationType(prompt)).toBe(expected);
  });

  it("classifies life-underwriting requests by their intended output", () => {
    expect(detectIntent("Create BDD tests for underwriting")).toBe("test-plan");
    expect(
      detectIntent("Generate a Playwright automation for a life application"),
    ).toBe("automation");
    expect(detectIntent("What evidence is needed for underwriting?")).toBe(
      "answer",
    );
    expect(detectIntent("为寿险新单核保生成 BDD 测试用例")).toBe("test-plan");
    expect(detectIntent("为寿险投保申请生成自动化脚本")).toBe("automation");
  });

  it.each([
    ["BDD test plan for life underwriting", "test-plan"],
    ["I need BDD test cases for life underwriting", "test-plan"],
    ["Please prepare test scenarios for beneficiary designation", "test-plan"],
    ["Automation script for a life application", "automation"],
    ["Can you build a Playwright script for policy submission?", "automation"],
    ["寿险核保 BDD 测试计划", "test-plan"],
    ["我需要一份受益人指定测试用例", "test-plan"],
    ["寿险投保自动化脚本", "automation"],
    ["请编写寿险投保 Playwright 脚本", "automation"],
    ["What evidence is needed for life underwriting?", "answer"],
  ] as const)("classifies noun-form request %s", (prompt, expectedIntent) => {
    expect(detectIntent(prompt)).toBe(expectedIntent);
  });

  it.each([
    "What tests are required for life underwriting?",
    "Explain our automation policy",
    "Can you explain our automation policy?",
    "How can I build a Playwright script?",
    "To create an automation script, what inputs do I need?",
    "What does this Playwright script do?",
    "Which BDD scenarios already cover beneficiary designation?",
    "寿险核保需要哪些测试？",
    "解释我们的自动化策略",
    "请解释我们的自动化策略",
    "如何构建 Playwright 脚本？",
    "生成自动化脚本需要哪些资料？",
    "这个 Playwright 脚本做什么？",
    "哪些 BDD 场景已经覆盖受益人指定？",
  ])("keeps artifact-related questions in Q&A: %s", (prompt) => {
    expect(detectIntent(prompt)).toBe("answer");
  });

  it("creates an empty conversation with independent context selections", () => {
    expect(createConversation("chat-2")).toMatchObject({
      id: "chat-2",
      modelId: "gpt-5.6-sol",
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
