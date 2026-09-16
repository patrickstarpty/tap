import type {
  ArtifactState,
  Automation,
  AutomationRun,
  BddFeature,
  ExecutionTarget,
  TestPlan,
} from "./model";

export type ArtifactAction =
  | { type: "automation/create"; automation: Automation }
  | { type: "automation/update"; automation: Automation }
  | { type: "test-plan/create"; testPlan: TestPlan }
  | {
      type: "association/set";
      automationId: string;
      testPlanId: string | null;
    }
  | { type: "run/add"; run: AutomationRun };

function replaceAutomation(
  state: ArtifactState,
  automation: Automation,
): ArtifactState {
  return {
    ...state,
    automations: state.automations.map((item) =>
      item.id === automation.id ? automation : item,
    ),
  };
}

export function artifactReducer(
  state: ArtifactState,
  action: ArtifactAction,
): ArtifactState {
  if (action.type === "automation/create") {
    if (state.automations.some(({ id }) => id === action.automation.id)) {
      return state;
    }
    return { ...state, automations: [action.automation, ...state.automations] };
  }
  if (action.type === "automation/update") {
    if (!state.automations.some(({ id }) => id === action.automation.id)) {
      return state;
    }
    return replaceAutomation(state, action.automation);
  }
  if (action.type === "test-plan/create") {
    if (state.testPlans.some(({ id }) => id === action.testPlan.id))
      return state;
    return { ...state, testPlans: [action.testPlan, ...state.testPlans] };
  }
  if (action.type === "run/add") {
    if (state.runs.some(({ id }) => id === action.run.id)) return state;
    return { ...state, runs: [action.run, ...state.runs] };
  }

  const automation = state.automations.find(
    ({ id }) => id === action.automationId,
  );
  if (automation === undefined) return state;

  if (action.testPlanId === null) {
    if (automation.testPlanId === null) return state;
    return {
      ...state,
      automations: state.automations.map((item) =>
        item.id === automation.id ? { ...item, testPlanId: null } : item,
      ),
      testPlans: state.testPlans.map((plan) =>
        plan.id === automation.testPlanId
          ? { ...plan, automationId: null }
          : plan,
      ),
    };
  }

  const testPlan = state.testPlans.find(({ id }) => id === action.testPlanId);
  if (testPlan === undefined) return state;
  if (
    (automation.testPlanId !== null && automation.testPlanId !== testPlan.id) ||
    (testPlan.automationId !== null && testPlan.automationId !== automation.id)
  ) {
    return state;
  }
  if (
    automation.testPlanId === testPlan.id &&
    testPlan.automationId === automation.id
  ) {
    return state;
  }
  return {
    ...state,
    automations: state.automations.map((item) =>
      item.id === automation.id ? { ...item, testPlanId: testPlan.id } : item,
    ),
    testPlans: state.testPlans.map((plan) =>
      plan.id === testPlan.id ? { ...plan, automationId: automation.id } : plan,
    ),
  };
}

export function selectAutomationRuns(
  state: ArtifactState,
  automationId: string,
): readonly AutomationRun[] {
  return state.runs.filter((run) => run.automationId === automationId);
}

export function selectTestPlanRuns(
  state: ArtifactState,
  testPlanId: string,
): readonly AutomationRun[] {
  return state.runs.filter((run) => run.testPlanIdAtRun === testPlanId);
}

export function selectAvailableTestPlans(
  state: ArtifactState,
  automationId: string,
): readonly TestPlan[] {
  return state.testPlans.filter(
    (plan) => plan.automationId === null || plan.automationId === automationId,
  );
}

export function selectAvailableAutomations(
  state: ArtifactState,
  testPlanId: string,
): readonly Automation[] {
  return state.automations.filter(
    (automation) =>
      automation.testPlanId === null || automation.testPlanId === testPlanId,
  );
}

export function createSimulatedRun({
  automation,
  id,
  target,
  triggeredFrom,
  timestamp,
}: {
  automation: Automation;
  id: string;
  target: ExecutionTarget;
  triggeredFrom: AutomationRun["triggeredFrom"];
  timestamp: string;
}): AutomationRun {
  return {
    id,
    automationId: automation.id,
    testPlanIdAtRun: automation.testPlanId,
    triggeredFrom,
    target,
    status: "completed",
    executionMode: "simulated",
    evidenceKind: "none",
    startedAt: timestamp,
    finishedAt: timestamp,
    scenarioResults: automation.feature.scenarios.map((scenario) => ({
      bddScenarioId: scenario.id,
      status: "completed",
      steps: scenario.steps.map((step) => ({
        bddStepId: step.id,
        status: "completed",
        actions: step.actions.map((action) => ({
          actionId: action.id,
          status: "completed",
        })),
      })),
    })),
    logs: [
      `Target selected: ${target.label}`,
      "Scenario and implementation-action graph resolved.",
      "Simulation completed. No provider or device was contacted.",
    ],
  };
}

export function cloneFeatureForAutomation(
  feature: BddFeature,
  automationId: string,
): BddFeature {
  let stepOrdinal = 0;
  return {
    ...feature,
    scenarios: feature.scenarios.map((scenario, scenarioIndex) => ({
      ...scenario,
      id: `${automationId}-SC-${String(scenarioIndex + 1).padStart(2, "0")}`,
      steps: scenario.steps.map((step) => {
        stepOrdinal += 1;
        const id = `${automationId}-ST-${String(stepOrdinal).padStart(2, "0")}`;
        return {
          ...step,
          id,
          actions: step.actions.map((action, actionIndex) => ({
            ...action,
            id: `${id}-ACT-${String(actionIndex + 1).padStart(2, "0")}`,
            bddStepId: id,
          })),
        };
      }),
    })),
  };
}
