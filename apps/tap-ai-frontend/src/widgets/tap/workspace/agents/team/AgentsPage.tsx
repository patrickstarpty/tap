import {
  Fragment,
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type SyntheticEvent,
} from "react";
import { createPortal } from "react-dom";

import { resolvePeriod } from "../../../../../features/knowledge/api/agentTeam";
import type { WorkspaceCopy } from "../../copy";
import { AgentProfileDrawer, type ProfileTab } from "./AgentProfileDrawer";
import { MyAgentsTab } from "./MyAgentsTab";
import {
  PeriodPicker,
  useValidPeriod,
  type PeriodSelection,
} from "./PeriodPicker";
import { ReportTab } from "./ReportTab";
import type { RenderHeader } from "./layout";
import { handleTablistKey } from "./tabs";
import { TeamTab } from "./TeamTab";
import "./team.css";

export type AgentsTab = "mine" | "team" | "report" | "config";

const TAB_ORDER: AgentsTab[] = ["mine", "team", "report", "config"];
const TAB_PARAM = "agentsTab";

export interface ConfigOpenRequest {
  initialDetailRevisionId: string | null;
  onInitialDetailConsumed: () => void;
}

export interface AgentsPageProps {
  projectId: string;
  copy: WorkspaceCopy;
  /** Either the node, or a render function that receives the revision the
   * profile drawer asked to open ("View configuration"). */
  configTab: ReactNode | ((open: ConfigOpenRequest) => ReactNode);
  initialTab?: AgentsTab;
  onOpenMember?: (agentKey: string) => void;
}

function isTab(value: string | null): value is AgentsTab {
  return value !== null && (TAB_ORDER as string[]).includes(value);
}

function readUrlTab(): AgentsTab | null {
  const value = new URLSearchParams(window.location.search).get(TAB_PARAM);
  return isTab(value) ? value : null;
}

function writeUrlTab(tab: AgentsTab) {
  const url = new URL(window.location.href);
  url.searchParams.set(TAB_PARAM, tab);
  window.history.replaceState(window.history.state, "", url);
}

const tabId = (id: AgentsTab) => `agents-tab-${id}`;

function todayUtc(): Date {
  return new Date();
}

export function AgentsPage({
  projectId,
  copy,
  configTab,
  initialTab,
  onOpenMember = () => undefined,
}: AgentsPageProps) {
  const text = copy.agentTeam;
  const [tab, setTab] = useState<AgentsTab>(
    () => initialTab ?? readUrlTab() ?? "team",
  );
  const [selection, setSelection] = useState<PeriodSelection>(() => ({
    preset: "last30",
    custom: resolvePeriod("last30", todayUtc()),
  }));
  // An inverted or half-typed custom range never queries; the last valid
  // period's view stays on screen under the inline message.
  const { period, preset } = useValidPeriod(selection);
  // The tab bar carries the period picker and freshness, so they stay in
  // view (sticky) while the analytics scroll.
  const [toolbarSlot, setToolbarSlot] = useState<HTMLDivElement | null>(null);

  const [profileKey, setProfileKey] = useState<string | null>(null);
  const [profileTab, setProfileTab] = useState<ProfileTab | undefined>();
  // The control that opened the profile, so closing it restores focus there.
  // Captured at the panel, so cards, table links, org-chart nodes and
  // feedback items all work without each passing an element up.
  const lastControl = useRef<HTMLElement | null>(null);
  const [profileOpener, setProfileOpener] = useState<HTMLElement | null>(null);
  const rememberControl = (event: SyntheticEvent) => {
    const target = event.target;
    if (target instanceof Element) {
      const control = target.closest<HTMLElement>(
        'button, [role="button"], a[href]',
      );
      if (control !== null) lastControl.current = control;
    }
  };
  const [configRequest, setConfigRequest] = useState<{
    revisionId: string;
    nonce: number;
    consumed: boolean;
  } | null>(null);

  useEffect(() => {
    if (initialTab !== undefined) writeUrlTab(initialTab);
  }, [initialTab]);
  // "View configuration" unmounts the card that opened the profile, so the
  // dialog cannot hand focus back to it; land on the Configuration tab.
  const [configFocus, setConfigFocus] = useState(0);
  useEffect(() => {
    if (configFocus > 0) document.getElementById(tabId("config"))?.focus();
  }, [configFocus]);

  const select = (next: AgentsTab) => {
    setTab(next);
    writeUrlTab(next);
  };

  const openMember = (agentKey: string, profileSection?: ProfileTab) => {
    setProfileOpener(lastControl.current);
    // Consumed: a later open from a non-click path must not reuse it.
    lastControl.current = null;
    setProfileTab(profileSection);
    setProfileKey(agentKey);
    onOpenMember(agentKey);
  };
  const viewConfig = (revisionId: string) => {
    setProfileKey(null);
    setConfigFocus((prev) => prev + 1);
    setConfigRequest((prev) => ({
      revisionId,
      nonce: (prev?.nonce ?? 0) + 1,
      consumed: false,
    }));
    select("config");
  };

  const heading = (toolbar: ReactNode) => (
    <header className="tap-module-heading tap-team-panel-heading">
      <div>
        <h1>{text.heading}</h1>
        <p>{text.descriptions[tab === "config" ? "team" : tab]}</p>
      </div>
      {toolbar === null ? null : (
        <div className="tap-team-toolbar">{toolbar}</div>
      )}
    </header>
  );
  // Freshness (from the tab's data) and the period sit in the sticky tab
  // bar; the heading keeps the title.
  const withPicker: RenderHeader = (freshness) => (
    <>
      {heading(null)}
      {toolbarSlot === null
        ? null
        : createPortal(
            <div className="tap-team-toolbar">
              {freshness}
              <PeriodPicker
                copy={copy}
                value={selection}
                onChange={setSelection}
              />
            </div>,
            toolbarSlot,
          )}
    </>
  );

  return (
    <section className="tap-agents-page">
      <div className="tap-agents-tabbar">
        <div
          role="tablist"
          aria-label={text.tablistLabel}
          className="tap-section-tabs tap-agents-tabs"
          onKeyDown={(event) =>
            handleTablistKey(event, TAB_ORDER, tab, select, tabId)
          }
        >
          {TAB_ORDER.map((id) => (
            <button
              key={id}
              id={tabId(id)}
              type="button"
              role="tab"
              aria-selected={tab === id}
              // Only the selected panel is rendered.
              aria-controls={tab === id ? `agents-panel-${id}` : undefined}
              tabIndex={tab === id ? 0 : -1}
              onClick={() => select(id)}
            >
              {text.tabs[id]}
            </button>
          ))}
        </div>
        <div ref={setToolbarSlot} className="tap-agents-toolbar-slot" />
      </div>
      <div
        role="tabpanel"
        id={`agents-panel-${tab}`}
        aria-labelledby={tabId(tab)}
        onClickCapture={rememberControl}
        onKeyDownCapture={rememberControl}
      >
        {tab === "config" ? (
          typeof configTab === "function" ? (
            <Fragment key={configRequest?.nonce ?? 0}>
              {configTab({
                initialDetailRevisionId:
                  configRequest === null || configRequest.consumed
                    ? null
                    : configRequest.revisionId,
                onInitialDetailConsumed: () =>
                  setConfigRequest((prev) =>
                    prev === null ? prev : { ...prev, consumed: true },
                  ),
              })}
            </Fragment>
          ) : (
            configTab
          )
        ) : (
          <div className="tap-module tap-team-panel">
            {tab === "report" ? (
              <>
                {heading(null)}
                <ReportTab projectId={projectId} copy={copy} />
              </>
            ) : tab === "team" ? (
              <TeamTab
                projectId={projectId}
                copy={copy}
                period={period}
                preset={preset}
                renderHeader={withPicker}
                onOpenMember={openMember}
                onCreateAgent={() => select("config")}
              />
            ) : (
              <MyAgentsTab
                projectId={projectId}
                copy={copy}
                period={period}
                renderHeader={withPicker}
                onOpenMember={openMember}
              />
            )}
          </div>
        )}
      </div>
      {profileKey === null ? null : (
        <AgentProfileDrawer
          projectId={projectId}
          agentKey={profileKey}
          period={period}
          copy={copy}
          opener={profileOpener}
          initialTab={profileTab}
          onClose={() => setProfileKey(null)}
          onViewConfig={viewConfig}
        />
      )}
    </section>
  );
}
