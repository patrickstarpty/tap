import { createInitialArtifactState } from "./fixtures";
import type { ArtifactState } from "./model";

export const AUTOMATION_STORAGE_KEY = "tap.automation.workspace.v1";
const AUTOMATION_BACKUP_PREFIX = "tap.automation.workspace.backup.";

type Validator = (value: unknown) => boolean;
const string: Validator = (value) => typeof value === "string";
const nullableString: Validator = (value) => value === null || string(value);
const oneOf =
  (...values: unknown[]): Validator =>
  (value) =>
    values.includes(value);
const array =
  (validate: Validator): Validator =>
  (value) =>
    Array.isArray(value) && value.every(validate);
const object =
  (fields: Record<string, Validator>): Validator =>
  (value) =>
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    Object.entries(fields).every(([key, validate]) =>
      validate((value as Record<string, unknown>)[key]),
    );
const keyword = oneOf("Given", "When", "Then", "And");
const resultStatus = oneOf("completed", "error");
const origin = oneOf("Tapper", "Manual");
const action = object({
  id: string,
  bddStepId: string,
  action: oneOf("Navigate", "Click", "Send keys", "Select", "Wait", "Assert"),
  target: string,
  value: string,
});
const target: Validator = (value) =>
  object({ kind: oneOf("web"), executionAgentId: string, label: string })(
    value,
  ) ||
  object({
    kind: oneOf("mobile"),
    platform: oneOf("ios", "android"),
    deviceId: string,
    label: string,
  })(value);
const validState = object({
  automations: array(
    object({
      id: string,
      title: string,
      goal: string,
      type: oneOf("web", "mobile"),
      source: origin,
      status: oneOf("draft", "ready"),
      testPlanId: nullableString,
      updatedAt: string,
      revision: (value) =>
        Number.isSafeInteger(value) && (value as number) >= 0,
      supportedPlatforms: array(oneOf("ios", "android")),
      feature: object({
        title: string,
        scenarios: array(
          object({
            id: string,
            title: string,
            sourceTestPlanScenarioId: nullableString,
            steps: array(
              object({
                id: string,
                keyword,
                text: string,
                sourceTestPlanStepId: nullableString,
                actions: array(action),
              }),
            ),
          }),
        ),
      }),
    }),
  ),
  testPlans: array(
    object({
      id: string,
      title: string,
      source: origin,
      status: oneOf("draft", "ready"),
      automationId: nullableString,
      updatedAt: string,
      scenarios: array(
        object({
          id: string,
          title: string,
          steps: array(object({ id: string, keyword, text: string })),
        }),
      ),
    }),
  ),
  runs: array(
    object({
      id: string,
      automationId: string,
      testPlanIdAtRun: nullableString,
      triggeredFrom: oneOf("automation", "test-plan"),
      target,
      status: resultStatus,
      executionMode: oneOf("simulated"),
      evidenceKind: oneOf("none"),
      startedAt: string,
      finishedAt: string,
      logs: array(string),
      scenarioResults: array(
        object({
          bddScenarioId: string,
          status: resultStatus,
          steps: array(
            object({
              bddStepId: string,
              status: resultStatus,
              actions: array(
                object({ actionId: string, status: resultStatus }),
              ),
            }),
          ),
        }),
      ),
    }),
  ),
});

export function readAutomationSnapshot(
  serialized: string | null,
): ArtifactState | null {
  try {
    const snapshot: unknown = JSON.parse(serialized ?? "null");
    if (object({ version: oneOf(1), state: validState })(snapshot)) {
      return (snapshot as { state: ArtifactState }).state;
    }
    if (object({ version: oneOf(2), artifacts: validState })(snapshot)) {
      return (snapshot as { artifacts: ArtifactState }).artifacts;
    }
  } catch {
    // Malformed imports and browser snapshots must not reach the workspace.
  }
  return null;
}

export function loadAutomationState(): ArtifactState {
  try {
    const current = window.localStorage.getItem(AUTOMATION_STORAGE_KEY);
    const restored = readAutomationSnapshot(
      current ?? window.localStorage.getItem("tap.prototype.workspace.v2"),
    );
    if (restored !== null) return restored;
  } catch {
    // Storage may be unavailable, or a previous snapshot may be corrupt.
  }
  return createInitialArtifactState();
}

export function loadLegacyAutomationState(): ArtifactState | null {
  try {
    return readAutomationSnapshot(
      window.localStorage.getItem("tap.prototype.workspace.v2"),
    );
  } catch {
    return null;
  }
}

export function saveAutomationState(state: ArtifactState): void {
  try {
    window.localStorage.setItem(
      AUTOMATION_STORAGE_KEY,
      JSON.stringify({ version: 1, state }),
    );
  } catch {
    // Editing and simulated execution remain available without browser storage.
  }
}

export function backupAndRestoreAutomationState(
  current: ArtifactState,
  restored: ArtifactState,
): boolean {
  try {
    const backupKey = `${AUTOMATION_BACKUP_PREFIX}${new Date().toISOString()}.${crypto.randomUUID()}`;
    window.localStorage.setItem(
      backupKey,
      JSON.stringify({ version: 1, state: current }),
    );
    window.localStorage.setItem(
      AUTOMATION_STORAGE_KEY,
      JSON.stringify({ version: 1, state: restored }),
    );
    return true;
  } catch {
    // Never replace the visible state unless both backup and replacement persisted.
    return false;
  }
}

export function loadPreviousAutomationState(): ArtifactState | null {
  try {
    const last = Object.keys(window.localStorage)
      .filter((key) => key.startsWith(AUTOMATION_BACKUP_PREFIX))
      .sort()
      .at(-1);
    return last === undefined
      ? null
      : readAutomationSnapshot(window.localStorage.getItem(last));
  } catch {
    return null;
  }
}
