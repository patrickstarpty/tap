import { describe, expect, it } from "vitest";

import { createInitialArtifactState, createLifeAutomation } from "./fixtures";
import {
  artifactReducer,
  createSimulatedRun,
  selectAutomationRuns,
  selectTestPlanRuns,
} from "./state";

describe("prototype artifact state", () => {
  it("keeps Test Plan and Automation as an optional strict 1:1 relation", () => {
    const initial = createInitialArtifactState();
    const linked = artifactReducer(initial, {
      type: "association/set",
      automationId: "AUTO-102",
      testPlanId: "TP-102",
    });

    expect(
      linked.automations.find(({ id }) => id === "AUTO-102")?.testPlanId,
    ).toBe("TP-102");
    expect(
      linked.testPlans.find(({ id }) => id === "TP-102")?.automationId,
    ).toBe("AUTO-102");

    const refused = artifactReducer(linked, {
      type: "association/set",
      automationId: "AUTO-101",
      testPlanId: "TP-102",
    });
    expect(refused).toBe(linked);
  });

  it("keeps every implementation action owned by one visible BDD step", () => {
    const state = createInitialArtifactState();
    const automation = state.automations.find(({ id }) => id === "AUTO-101")!;

    const actionOwners = automation.feature.scenarios.flatMap((scenario) =>
      scenario.steps.flatMap((step) =>
        step.actions.map((action) => ({
          action: action.action,
          actionOwner: action.bddStepId,
          stepId: step.id,
        })),
      ),
    );

    expect(actionOwners).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ action: "Click" }),
        expect.objectContaining({ action: "Send keys" }),
      ]),
    );
    expect(
      actionOwners.every(({ actionOwner, stepId }) => actionOwner === stepId),
    ).toBe(true);
  });

  it("carries generated Test Plan identifiers through the BDD trace mapping", () => {
    const automation = createLifeAutomation("AUTO-205", "TP-205", "Athena");
    const scenario = automation.feature.scenarios[0]!;
    const step = scenario.steps[0]!;

    expect(scenario.sourceTestPlanScenarioId).toBe("TP-205-SC-01");
    expect(step.sourceTestPlanStepId).toBe("TP-205-ST-01");
    expect(step.actions.every(({ bddStepId }) => bddStepId === step.id)).toBe(
      true,
    );
  });

  it("projects one linked Run into both histories without duplicating it", () => {
    const initial = createInitialArtifactState();
    const automation = initial.automations.find(({ id }) => id === "AUTO-101")!;
    const run = createSimulatedRun({
      automation,
      id: "RUN-201",
      target: {
        kind: "web",
        executionAgentId: "ado-web-agent-03",
        label: "ADO Web Agent 03",
      },
      triggeredFrom: "automation",
      timestamp: "2026-09-03T10:00:00Z",
    });
    const completed = artifactReducer(initial, { type: "run/add", run });

    expect(selectAutomationRuns(completed, "AUTO-101")).toEqual([run]);
    expect(selectTestPlanRuns(completed, "TP-101")).toEqual([run]);
    expect(completed.runs).toHaveLength(1);
    expect(run.testPlanIdAtRun).toBe("TP-101");
  });

  it("never backfills an unlinked Run into a later Test Plan association", () => {
    const initial = createInitialArtifactState();
    const automation = initial.automations.find(({ id }) => id === "AUTO-102")!;
    const run = createSimulatedRun({
      automation,
      id: "RUN-202",
      target: {
        kind: "mobile",
        platform: "ios",
        deviceId: "iphone-15",
        label: "iPhone 15",
      },
      triggeredFrom: "automation",
      timestamp: "2026-09-03T10:05:00Z",
    });
    const withRun = artifactReducer(initial, { type: "run/add", run });
    const laterLinked = artifactReducer(withRun, {
      type: "association/set",
      automationId: "AUTO-102",
      testPlanId: "TP-102",
    });

    expect(selectTestPlanRuns(laterLinked, "TP-102")).toEqual([]);
    expect(selectAutomationRuns(laterLinked, "AUTO-102")).toEqual([run]);
  });
});
