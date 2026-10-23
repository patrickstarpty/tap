import { Fragment, useEffect, useMemo, useState, type ReactNode } from "react";

import {
  resolvePeriod,
  useTeamSummary,
} from "../../../../../features/knowledge/api/agentTeam";
import type { WorkspaceCopy } from "../../copy";
import { AgentProfileDrawer } from "./AgentProfileDrawer";
import { PeriodPicker, type PeriodSelection } from "./PeriodPicker";
import { RuntimeBadge } from "./RuntimeBadge";
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
              <TeamFreshness
                projectId={projectId}
                copy={copy}
                from={period.from}
                to={period.to}
              />
            )}
          </>
        )}
      </div>
      {profileKey === null ? null : (
        <AgentProfileDrawer
          projectId={projectId}
          agentKey={profileKey}
          period={period}
          copy={copy}
          onClose={() => setProfileKey(null)}
          onViewConfig={viewConfig}
        />
      )}
    </section>
  );
}

function TeamFreshness({
  projectId,
  copy,
  from,
  to,
}: {
  projectId: string;
  copy: WorkspaceCopy;
  from: string;
  to: string;
}) {
  const text = copy.agentTeam;
  const period = useMemo(() => ({ from, to }), [from, to]);
  const summary = useTeamSummary(projectId, period);
  if (summary.isPending) return <p role="status">{text.loading}</p>;
  if (summary.isError || summary.data === undefined)
    return (
      <div role="alert">
        <p>{text.loadFailed}</p>
        <button type="button" onClick={() => void summary.refetch()}>
          {text.retry}
        </button>
      </div>
    );
  const view = summary.data;
  return (
    <RuntimeBadge
      status={view.runtimeStatus}
      computedAt={view.computedAt}
      timezone={view.timezone}
      copy={copy}
    />
  );
}
