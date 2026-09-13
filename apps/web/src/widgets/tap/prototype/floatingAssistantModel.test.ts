import { describe, expect, it } from "vitest";

import { createInitialArtifactState } from "./artifacts/fixtures";
import { createSimulatedRun } from "./artifacts/state";
import type { ArtifactState } from "./artifacts/model";
import {
  createFloatingAssistantReply,
  getFloatingAssistantContext,
} from "./floatingAssistantModel";

const initial = createInitialArtifactState();

function planContext(id: string | null, artifacts: ArtifactState = initial) {
  return getFloatingAssistantContext({
    activeModule: "test-management",
    selectedPlanId: id,
    automationView: { kind: "library" },
    artifacts,
    locale: "en",
  });
}

function automationContext(id: string, artifacts: ArtifactState = initial) {
  return getFloatingAssistantContext({
    activeModule: "low-code",
    selectedPlanId: "TP-101",
    automationView: { kind: "detail", automationId: id },
    artifacts,
    locale: "en",
  });
}

describe("floating assistant page context", () => {
  it("derives list counts and asset names from the loaded state", () => {
    const context = planContext(null, {
      ...initial,
      testPlans: initial.testPlans.slice(1),
    });

    expect(context.key).toBe("test-management:library");
    expect(context.summary).toContain("1 Test Plan");
    expect(context.facts.join("\n")).toContain(
      "Beneficiary designation validation (TP-102)",
    );
    expect(context.facts.join("\n")).toContain("1 without linked automation");
    expect(context.facts.join("\n")).not.toContain("TP-101");
    expect(context.prompts).toHaveLength(3);
  });

  it("keeps an object identity while reflecting edited scenario and relation data", () => {
    const before = planContext("TP-101");
    const edited: ArtifactState = {
      ...initial,
      testPlans: initial.testPlans.map((plan) =>
        plan.id === "TP-101"
          ? {
              ...plan,
              title: "Updated underwriting",
              scenarios: [
                { ...plan.scenarios[0]!, title: "Review revised coverage" },
              ],
            }
          : plan,
      ),
    };
    const after = planContext("TP-101", edited);

    expect(after.key).toBe(before.key);
    expect(after.label).toContain("Updated underwriting");
    expect(after.label).toContain("TP-101");
    expect(after.summary).toContain("1 scenario");
    expect(after.facts.join("\n")).toContain("Review revised coverage");
    expect(after.facts.join("\n")).toContain(
      "Life insurance application automation (AUTO-101)",
    );
    expect(after.facts.join("\n")).not.toContain(
      "High coverage requires manual review",
    );
    expect(planContext("TP-102").key).not.toBe(after.key);
    expect(planContext("TP-102").facts.join("\n")).toContain(
      "No linked automation",
    );
  });

  it("reports missing implementation actions and empty targets without inventing coverage", () => {
    const automation = initial.automations[0]!;
    const scenario = automation.feature.scenarios[0]!;
    const artifacts: ArtifactState = {
      ...initial,
      automations: [
        {
          ...automation,
          feature: {
            ...automation.feature,
            scenarios: [
              {
                ...scenario,
                steps: [
                  { ...scenario.steps[0]!, actions: [] },
                  {
                    ...scenario.steps[1]!,
                    actions: [
                      { ...scenario.steps[1]!.actions[0]!, target: " " },
                    ],
                  },
                ],
              },
            ],
          },
        },
      ],
    };
    const context = automationContext("AUTO-101", artifacts);

    expect(context.label).toContain("AUTO-101");
    expect(context.facts.join("\n")).toContain(
      "Life insurance application underwriting (TP-101)",
    );
    expect(context.facts.join("\n")).toContain(
      "1 step without implementation actions",
    );
    expect(context.facts.join("\n")).toContain("1 action without a target");
    expect(context.facts.join("\n")).toContain(
      "1 of 3 Test Plan scenarios mapped",
    );
    expect(context.facts.join("\n")).toContain("AUTO-101-ST-01");
    expect(automationContext("AUTO-102").facts.join("\n")).toContain(
      "No linked Test Plan",
    );
  });

  it("labels scoped run history as simulated and does not backfill later associations", () => {
    const run = createSimulatedRun({
      automation: initial.automations[1]!,
      id: "RUN-202",
      target: {
        kind: "mobile",
        platform: "ios",
        deviceId: "iphone-15",
        label: "iPhone 15",
      },
      triggeredFrom: "automation",
      timestamp: "2026-09-06T10:00:00Z",
    });
    const artifacts: ArtifactState = {
      ...initial,
      runs: [run],
      testPlans: initial.testPlans.map((plan) =>
        plan.id === "TP-102" ? { ...plan, automationId: "AUTO-102" } : plan,
      ),
      automations: initial.automations.map((automation) =>
        automation.id === "AUTO-102"
          ? { ...automation, testPlanId: "TP-102" }
          : automation,
      ),
    };

    const runFacts = automationContext("AUTO-102", artifacts).facts.join("\n");
    expect(runFacts).toContain("Simulated");
    expect(runFacts).toContain("RUN-202");
    expect(runFacts).toContain("iPhone 15");
    expect(runFacts).toContain("no real execution evidence");
    expect(planContext("TP-102", artifacts).facts.join("\n")).toContain(
      "No simulated runs",
    );
    expect(planContext("TP-102", artifacts).facts.join("\n")).not.toContain(
      "RUN-202",
    );
  });

  it("distinguishes automation list and creation contexts with localized quick prompts", () => {
    const library = getFloatingAssistantContext({
      activeModule: "low-code",
      selectedPlanId: "TP-101",
      automationView: { kind: "library" },
      artifacts: initial,
      locale: "en",
    });
    const creation = getFloatingAssistantContext({
      activeModule: "low-code",
      selectedPlanId: "TP-101",
      automationView: { kind: "new" },
      artifacts: initial,
      locale: "zh",
    });

    expect(library.key).toBe("automation:library");
    expect(library.summary).toContain("2 automations");
    expect(library.facts.join("\n")).toContain(
      "Claims photo upload (AUTO-102)",
    );
    expect(creation.key).toBe("automation:new");
    expect(creation.label).toContain("新建");
    expect(creation.facts.join("\n")).toContain("TP-102");
    expect(creation.facts.join("\n")).not.toContain("TP-101");
    expect(creation.prompts).toHaveLength(3);
    expect(
      creation.prompts.every((prompt) => /[\u4e00-\u9fff]/.test(prompt)),
    ).toBe(true);
  });

  it("does not silently substitute another object for a missing selection", () => {
    const context = automationContext("AUTO-missing");
    expect(context.key).toBe("automation:AUTO-missing");
    expect(context.summary).toContain("no longer available");
    expect(context.facts.join("\n")).not.toContain("Life insurance");
  });
});

describe("floating assistant prototype replies", () => {
  it("grounds coverage guidance in the selected scenarios and keeps run suggestions distinct", () => {
    const context = planContext("TP-102");
    const coverage = createFloatingAssistantReply(
      "Review coverage gaps",
      context,
      "en",
    );
    const run = createFloatingAssistantReply(
      "Prepare a run checklist",
      context,
      "en",
    );

    expect(coverage.text).toContain("No linked automation");
    expect(coverage.text).not.toContain("High coverage requires manual review");
    expect(coverage.suggestions.join(" ")).toMatch(/requirement|boundary/i);
    expect(run.suggestions.join(" ")).toMatch(/target|agent|device/i);
    expect(run.text).toContain("simulated");
    expect(run.suggestions).not.toEqual(coverage.suggestions);
  });

  it("handles unsupported freeform honestly without pretending to execute or generate artifacts", () => {
    const reply = createFloatingAssistantReply(
      "Create a production payment service and email it to my team",
      planContext("TP-101"),
      "en",
    );
    expect(reply.text).toContain(
      "cannot answer this request from page data alone",
    );
    expect(reply.text).toContain("Life insurance application underwriting");
    expect(reply.suggestions).toHaveLength(3);
    expect(reply.text).not.toMatch(/created|sent|bug found/i);
  });

  it("provides Chinese explanations and exploration suggestions grounded in current data", () => {
    const context = getFloatingAssistantContext({
      activeModule: "test-management",
      selectedPlanId: "TP-102",
      automationView: { kind: "library" },
      artifacts: initial,
      locale: "zh",
    });
    const explanation = createFloatingAssistantReply(
      "解释当前页面",
      context,
      "zh",
    );
    const exploration = createFloatingAssistantReply(
      "建议探索性测试方向",
      context,
      "zh",
    );
    expect(explanation.text).toContain("未关联自动化");
    expect(exploration.text).toContain("Require a valid beneficiary");
    expect(exploration.suggestions.join(" ")).toMatch(/边界|探索/);
    expect(exploration.suggestions).not.toEqual(explanation.suggestions);
  });

  it("keeps replies brief with at most one fact relevant to the requested category", () => {
    const context = planContext("TP-102");
    const requests = [
      ["Review coverage gaps", "No linked automation."],
      [
        "Suggest exploratory checks",
        "Scenario: Require a valid beneficiary (TP-102-SC-01)",
      ],
      [
        "Prepare a run checklist",
        "No simulated runs recorded for this context.",
      ],
    ] as const;

    for (const [prompt, relevantFact] of requests) {
      const reply = createFloatingAssistantReply(prompt, context, "en");
      expect(reply.text).toContain(context.summary);
      expect(reply.text).toContain(relevantFact);
      expect(
        context.facts.filter((fact) => reply.text.includes(fact)),
      ).toHaveLength(1);
    }
  });

  it.each(["en", "zh"] as const)(
    "supports every visible quick prompt in %s",
    (locale) => {
      const pages = [
        {
          activeModule: "test-management",
          selectedPlanId: null,
          automationView: { kind: "library" },
        },
        {
          activeModule: "test-management",
          selectedPlanId: "TP-101",
          automationView: { kind: "library" },
        },
        {
          activeModule: "low-code",
          selectedPlanId: null,
          automationView: { kind: "library" },
        },
        {
          activeModule: "low-code",
          selectedPlanId: null,
          automationView: { kind: "new" },
        },
        {
          activeModule: "low-code",
          selectedPlanId: null,
          automationView: { kind: "detail", automationId: "AUTO-101" },
        },
      ] as const;

      for (const page of pages) {
        const context = getFloatingAssistantContext({
          ...page,
          artifacts: initial,
          locale,
        });
        for (const prompt of context.prompts) {
          const reply = createFloatingAssistantReply(prompt, context, locale);
          expect(reply.text).not.toMatch(
            /cannot answer this request|无法仅凭页面数据回答该请求/,
          );
          expect(reply.suggestions).not.toEqual(context.prompts);
        }
      }
    },
  );
});
