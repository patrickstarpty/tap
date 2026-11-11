import { ConfigProvider } from "antd";
import { LcaApplication } from "./LcaApplication";
import { TapProductPrototype } from "./widgets/tap/TapProductPrototype";
import { tapperTheme } from "./widgets/tap/PrototypeTheme";
import "./widgets/tap/PrototypeTheme.css";
import "./widgets/tap/TapProductPrototype.css";
import { PrototypeSidebar } from "./widgets/tap/prototype/PrototypeSidebar";
import { PROTOTYPE_COPY } from "./widgets/tap/prototype/copy";
import type { ProductModule } from "./widgets/tap/prototype/model";
import { useEffect, useLayoutEffect, useReducer, useState } from "react";

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
  const isPrototype = window.location.pathname === "/prototype";
  useLayoutEffect(() => {
    document.documentElement.dataset.tapPrototype = "true";
    return () => {
      delete document.documentElement.dataset.tapPrototype;
    };
  }, [isPrototype]);
  return (
    <ConfigProvider theme={tapperTheme}>
      {isPrototype ? (
        <TapProductPrototype />
      ) : window.location.pathname === "/prototype/legacy" ? (
        <LegacyTapApplication />
      ) : (
        <TapApplication />
      )}
    </ConfigProvider>
  );
}

function LegacyTapApplication() {
  const [section, setSection] = useState<"automation" | "analytics">(
    ["test-insights", "test-analytics"].includes(
      new URLSearchParams(window.location.search).get("module") ?? "",
    )
      ? "analytics"
      : "automation",
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
  const selectModule = (module: ProductModule) => {
    if (module === "test-insights" || module === "low-code") {
      const next = module === "test-insights" ? "analytics" : "automation";
      setSection(next);
      const url = new URL(window.location.href);
      url.searchParams.set("module", module);
      window.history.replaceState(null, "", url);
      return;
    }
    window.location.assign(`/prototype?module=${module}`);
  };

  return (
    <div className="tap-product-shell tap-runtime-shell">
      <PrototypeSidebar
        activeConversationId=""
        activeModule={section === "analytics" ? "test-insights" : "low-code"}
        collapsed
        conversations={[]}
        copy={PROTOTYPE_COPY.en}
        locale="en"
        onLocaleChange={() => {}}
        onModuleChange={selectModule}
        onNewChat={() => selectModule("tapper")}
        onSelectConversation={() => {}}
        onToggleCollapsed={() => {}}
        showFooter={false}
      />
      <main className="tap-product-main tap-runtime-main">
        {section === "automation" ? <a href="/lca">Real debug flows</a> : null}
        {section === "automation" ? (
          <WorkspaceTransfer
            state={state}
            onRestore={(restored) => {
              dispatch({ type: "workspace/restore", state: restored });
              setView({ kind: "library" });
              setSection("automation");
            }}
          />
        ) : null}
        {section === "analytics" ? (
          <TestAnalyticsWorkspace
            locale="en"
            initialQueryId={
              new URLSearchParams(window.location.search).get("queryId") ??
              undefined
            }
          />
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
              setNotice(`Test plan ${testPlanId} is managed in Tapper.`)
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

/** Real product entry. Prototype/localStorage runs never appear on this route. */
function TapApplication() {
  const sectionFromUrl = () =>
    new URLSearchParams(window.location.search).get("module") ===
    "test-insights"
      ? "analytics"
      : "automation";
  const [section, setSection] = useState(sectionFromUrl);
  const [collapsed, setCollapsed] = useState(true);
  useEffect(() => {
    const restore = () => setSection(sectionFromUrl());
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);
  const selectModule = (module: ProductModule) => {
    if (module === "low-code" || module === "test-insights") {
      setSection(module === "low-code" ? "automation" : "analytics");
      const url = new URL(window.location.href);
      url.pathname = "/";
      url.searchParams.set("module", module);
      window.history.pushState(null, "", url);
    } else {
      const destination = new URL(
        import.meta.env.VITE_TAPPER_URL || "/tapper",
        window.location.origin,
      );
      destination.searchParams.set("module", module);
      window.location.assign(destination.href);
    }
  };
  return (
    <div className="tap-product-shell tap-runtime-shell">
      <PrototypeSidebar
        activeConversationId=""
        activeModule={section === "automation" ? "low-code" : "test-insights"}
        collapsed={collapsed}
        conversations={[]}
        copy={PROTOTYPE_COPY.en}
        locale="en"
        onLocaleChange={() => {}}
        onModuleChange={selectModule}
        onNewChat={() => selectModule("tapper")}
        onSelectConversation={() => {}}
        onToggleCollapsed={() => setCollapsed((value) => !value)}
        showFooter={false}
      />
      <main className="tap-product-main tap-runtime-main">
        {/* Keep the editor mounted: navigating within TAP must not discard drafts or recording state. */}
        <div hidden={section !== "automation"}>
          <LcaApplication />
        </div>
        {section === "analytics" && <TestAnalyticsWorkspace locale="en" />}
      </main>
    </div>
  );
}
