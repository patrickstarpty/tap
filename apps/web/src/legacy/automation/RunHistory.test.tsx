import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { createLifeAutomation } from "../artifacts/fixtures";
import { createSimulatedRun } from "../artifacts/state";
import { readAutomationSnapshot } from "../artifacts/persistence";
import { AutomationRunPanel } from "./AutomationWorkspace";

afterEach(cleanup);

function fixture() {
  const automation = createLifeAutomation("AUTO-history", null, "Manual");
  automation.revision = 7;
  const scenario = automation.feature.scenarios[0]!;
  scenario.title = "Original scenario";
  scenario.steps[0]!.text = "Original step";
  scenario.steps[0]!.actions[0]!.action = "Navigate";
  const run = createSimulatedRun({
    automation, id: "RUN-history", triggeredFrom: "automation",
    target: { kind: "web", executionAgentId: "agent", label: "Original target" },
    timestamp: "2026-11-10T00:00:00Z",
  });
  return { automation, run };
}

it("preserves run definitions after nested edits, deletion and storage reload", () => {
  const { automation, run } = fixture();
  automation.revision = 8;
  automation.feature.scenarios[0]!.title = "Changed scenario";
  automation.feature.scenarios[0]!.steps[0]!.text = "Changed step";
  automation.feature.scenarios[0]!.steps[0]!.actions[0]!.action = "Click";
  automation.feature = { title: "Changed feature", scenarios: [] };
  const restored = readAutomationSnapshot(JSON.stringify({ version: 1, state: {
    automations: [automation], testPlans: [], runs: [run],
  } }))!;
  render(<AutomationRunPanel automation={restored.automations[0]!} runs={restored.runs}
    locale="en" triggeredFrom="automation" historyLabel="History" onRun={() => {}} />);
  const history = within(screen.getByRole("region", { name: "History" }));
  expect(history.getByText("Original scenario")).toBeVisible();
  expect(history.getByText(/Original step/)).toBeVisible();
  expect(history.getByText(/Revision 7/)).toBeVisible();
  expect(history.getAllByText(/Navigate/)[0]).toBeVisible();
  expect(history.queryByText(/Changed/)).not.toBeInTheDocument();
  expect(restored.runs[0]!.definitionSnapshot?.feature.scenarios[0]?.steps[0]?.actions[0]?.action).toBe("Navigate");
});

it("shows legacy result identifiers without borrowing current definitions", () => {
  const { automation, run } = fixture();
  delete run.definitionSnapshot;
  const restored = readAutomationSnapshot(JSON.stringify({ version: 1, state: {
    automations: [automation], testPlans: [], runs: [run],
  } }))!;
  render(<AutomationRunPanel automation={automation} runs={restored.runs} locale="en"
    triggeredFrom="automation" historyLabel="History" onRun={() => {}} />);
  const history = within(screen.getByRole("region", { name: "History" }));
  expect(history.getByText("Historical definition unavailable")).toBeVisible();
  expect(history.getByText(run.scenarioResults[0]!.bddScenarioId)).toBeVisible();
  expect(history.getByText(run.scenarioResults[0]!.steps[0]!.bddStepId)).toBeVisible();
  expect(history.getByText(new RegExp(run.scenarioResults[0]!.steps[0]!.actions[0]!.actionId))).toBeVisible();
  expect(history.queryByText("Original scenario")).not.toBeInTheDocument();
});

it.each([null, {}, { revision: -1, feature: {} }, { revision: 1, feature: { title: "bad", scenarios: [null] } }])(
  "rejects malformed supplied run definitions: %j", (definitionSnapshot) => {
    const { automation, run } = fixture();
    expect(readAutomationSnapshot(JSON.stringify({ version: 1, state: {
      automations: [automation], testPlans: [], runs: [{ ...run, definitionSnapshot }],
    } }))).toBeNull();
  },
);
