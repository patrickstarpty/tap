import {
  Fragment,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type SyntheticEvent,
} from "react";

import { resolvePeriod } from "../../../../../features/knowledge/api/agentTeam";
import type { WorkspaceCopy } from "../../copy";
import { AgentProfileDrawer } from "./AgentProfileDrawer";
import { MyAgentsTab } from "./MyAgentsTab";
import { PeriodPicker, type PeriodSelection } from "./PeriodPicker";
import { ReportTab } from "./ReportTab";
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
  const period = useMemo(
    () => resolvePeriod(selection.preset, todayUtc(), selection.custom),
    [selection],
  );

  const [profileKey, setProfileKey] = useState<string | null>(null);
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

  const rangeInvalid =
    selection.preset === "custom" &&
    selection.custom.from > selection.custom.to;
  useEffect(() => {
    if (initialTab !== undefined) writeUrlTab(initialTab);
  }, [initialTab]);

  const select = (next: AgentsTab) => {
    setTab(next);
    writeUrlTab(next);
  };

  const openMember = (agentKey: string) => {
    setProfileOpener(lastControl.current);
    setProfileKey(agentKey);
    onOpenMember(agentKey);
  };
  const viewConfig = (revisionId: string) => {
    setProfileKey(null);
    setConfigRequest((prev) => ({
      revisionId,
      nonce: (prev?.nonce ?? 0) + 1,
      consumed: false,
    }));
    select("config");
  };

  return (
    <section className="tap-agents-page">
      <div role="tablist" aria-label={text.tablistLabel}>
        {TAB_ORDER.map((id) => (
          <button
            key={id}
            id={`agents-tab-${id}`}
            type="button"
            role="tab"
            aria-selected={tab === id}
            aria-controls={`agents-panel-${id}`}
            onClick={() => select(id)}
          >
            {text.tabs[id]}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`agents-panel-${tab}`}
        aria-labelledby={`agents-tab-${tab}`}
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
            <header className="tap-module-heading tap-team-panel-heading">
              <div>
                <h1>{text.heading}</h1>
                <p>{text.descriptions[tab]}</p>
              </div>
            </header>
            {tab === "report" ? (
              <ReportTab projectId={projectId} copy={copy} />
            ) : (
              <>
                <PeriodPicker
                  copy={copy}
                  value={selection}
                  onChange={setSelection}
                />
                {rangeInvalid ? null : tab === "team" ? (
                  <TeamTab
                    projectId={projectId}
                    copy={copy}
                    period={period}
                    onOpenMember={openMember}
                    onCreateAgent={() => select("config")}
                  />
                ) : (
                  <MyAgentsTab
                    projectId={projectId}
                    copy={copy}
                    period={period}
                    onOpenMember={openMember}
                  />
                )}
              </>
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
          onClose={() => setProfileKey(null)}
          onViewConfig={viewConfig}
        />
      )}
    </section>
  );
}
