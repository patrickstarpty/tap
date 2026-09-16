import { useEffect, useReducer, useState } from "react";

import {
  createBlankAutomation,
  createGeneratedAutomation,
} from "./legacy/artifacts/fixtures";
import type { AutomationRun } from "./legacy/artifacts/model";
import { artifactReducer, createSimulatedRun } from "./legacy/artifacts/state";
import {
  loadAutomationState,
  saveAutomationState,
} from "./legacy/artifacts/persistence";
import {
  AutomationWorkspace,
  type AutomationWorkspaceView,
} from "./legacy/automation/AutomationWorkspace";
import { TestAnalyticsWorkspace } from "./legacy/TestAnalyticsWorkspace";
import { WorkspaceTransfer } from "./WorkspaceTransfer";
import "./styles.css";
import "./legacy/automation/AutomationWorkspace.css";

export function App() {
  const [section, setSection] = useState<"automation" | "analytics">(
    "automation",
  );
  const [state, dispatch] = useReducer(
    artifactReducer,
    undefined,
    loadAutomationState,
  );
  useEffect(() => saveAutomationState(state), [state]);
  const [view, setView] = useState<AutomationWorkspaceView>({
    kind: "library",
  });
  const [notice, setNotice] = useState("");

  return (
    <div className="tap-app">
      <header className="tap-app-header">
        <strong>TAP</strong>
        <nav aria-label="TAP modules">
          <button
            type="button"
            aria-current={section === "automation" ? "page" : undefined}
            onClick={() => setSection("automation")}
          >
            Low Code Automation
          </button>
          <button
            type="button"
            aria-current={section === "analytics" ? "page" : undefined}
            onClick={() => setSection("analytics")}
          >
            Test Analytics
          </button>
        </nav>
      </header>
      <main>
        <WorkspaceTransfer
          state={state}
          onRestore={(restored) => {
            dispatch({ type: "workspace/restore", state: restored });
            setView({ kind: "library" });
            setSection("automation");
          }}
        />
        {section === "analytics" ? (
          <TestAnalyticsWorkspace locale="en" />
        ) : (
          <AutomationWorkspace
            state={state}
            view={view}
            locale="en"
            onViewChange={setView}
            onUpdate={(automation) =>
              dispatch({ type: "automation/update", automation })
            }
            onCreate={(draft) => {
              const id = `AUTO-${String(Date.now())}`;
              const create =
                draft.mode === "blank"
                  ? createBlankAutomation
                  : createGeneratedAutomation;
              const automation = create({
                id,
                title: draft.title,
                goal: draft.goal,
                type: draft.type,
                testPlanId: draft.testPlanId,
              });
              dispatch({ type: "automation/create", automation });
              setView({ kind: "detail", automationId: id });
            }}
            onLink={(automationId, testPlanId) =>
              dispatch({ type: "association/set", automationId, testPlanId })
            }
            onOpenTestPlan={(testPlanId) =>
              setNotice(`Test plan ${testPlanId} is managed in Tap AI.`)
            }
            onRun={(
              automation,
              target,
              triggeredFrom: AutomationRun["triggeredFrom"],
            ) => {
              const run = createSimulatedRun({
                automation,
                id: `RUN-${String(Date.now())}`,
                target,
                triggeredFrom,
                timestamp: new Date().toISOString(),
              });
              dispatch({ type: "run/add", run });
            }}
          />
        )}
        {notice ? <p role="status">{notice}</p> : null}
      </main>
    </div>
  );
}
