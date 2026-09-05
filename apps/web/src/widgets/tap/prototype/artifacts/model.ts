export type ArtifactOrigin = "Tapper" | "Manual";
export type AutomationType = "web" | "mobile";
export type MobilePlatform = "ios" | "android";
export type BddKeyword = "Given" | "When" | "Then" | "And";

export type ImplementationActionType =
  "Navigate" | "Click" | "Send keys" | "Select" | "Wait" | "Assert";

export interface ImplementationAction {
  id: string;
  bddStepId: string;
  action: ImplementationActionType;
  target: string;
  value: string;
}

export interface BddStep {
  id: string;
  keyword: BddKeyword;
  text: string;
  sourceTestPlanStepId: string | null;
  actions: readonly ImplementationAction[];
}

export interface BddScenario {
  id: string;
  title: string;
  sourceTestPlanScenarioId: string | null;
  steps: readonly BddStep[];
}

export interface BddFeature {
  title: string;
  scenarios: readonly BddScenario[];
}

export interface Automation {
  id: string;
  title: string;
  goal: string;
  type: AutomationType;
  source: ArtifactOrigin;
  status: "draft" | "ready";
  testPlanId: string | null;
  feature: BddFeature;
  supportedPlatforms: readonly MobilePlatform[];
  revision: number;
  updatedAt: string;
}

export interface TestPlanStep {
  id: string;
  keyword: BddKeyword;
  text: string;
}

export interface TestPlanScenario {
  id: string;
  title: string;
  steps: readonly TestPlanStep[];
}

export interface TestPlan {
  id: string;
  title: string;
  source: ArtifactOrigin;
  status: "draft" | "ready";
  automationId: string | null;
  scenarios: readonly TestPlanScenario[];
  updatedAt: string;
}

export type ExecutionTarget =
  | {
      kind: "web";
      executionAgentId: string;
      label: string;
    }
  | {
      kind: "mobile";
      platform: MobilePlatform;
      deviceId: string;
      label: string;
    };

export interface RunActionResult {
  actionId: string;
  status: "completed" | "error";
}

export interface RunStepResult {
  bddStepId: string;
  status: "completed" | "error";
  actions: readonly RunActionResult[];
}

export interface RunScenarioResult {
  bddScenarioId: string;
  status: "completed" | "error";
  steps: readonly RunStepResult[];
}

export interface AutomationRun {
  id: string;
  automationId: string;
  testPlanIdAtRun: string | null;
  triggeredFrom: "automation" | "test-plan";
  target: ExecutionTarget;
  status: "completed" | "error";
  executionMode: "simulated";
  evidenceKind: "none";
  startedAt: string;
  finishedAt: string;
  scenarioResults: readonly RunScenarioResult[];
  logs: readonly string[];
}

export interface ArtifactState {
  automations: readonly Automation[];
  testPlans: readonly TestPlan[];
  runs: readonly AutomationRun[];
}

export interface ExecutionAgent {
  id: string;
  name: string;
  status: "online" | "busy" | "offline";
}

export interface MobileDevice {
  id: string;
  name: string;
  platform: MobilePlatform;
  status: "available" | "busy" | "offline";
}
