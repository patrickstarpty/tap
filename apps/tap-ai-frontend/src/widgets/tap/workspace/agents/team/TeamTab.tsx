import { useMemo, useRef, useState, type ReactNode } from "react";

import {
  useOrgChart,
  useTeamMembers,
  useTeamPeople,
  useTeamSummary,
  type MemberFilters,
  type TeamPeriod,
} from "../../../../../features/knowledge/api/agentTeam";
import type { WorkspaceCopy } from "../../copy";
import { formatCost, formatCount, formatDuration, teamLocale } from "./format";
import {
  EmptyRoster,
  LoadError,
  TeamSkeleton,
  defaultHeader,
  type RenderHeader,
} from "./layout";
import { RunValue, type CellContext } from "./memberCells";
import { OrgChart } from "./OrgChart";
import { PeopleTable } from "./PeopleTable";
import { RosterCards } from "./RosterCards";
import { RosterTable, type RosterSort } from "./RosterTable";
import { RuntimeBadge } from "./RuntimeBadge";
import { useSparklines } from "./useSparklines";

export interface TeamTabProps {
  projectId: string;
  copy: WorkspaceCopy;
  period: TeamPeriod;
  onOpenMember: (agentKey: string) => void;
  onCreateAgent: () => void;
  renderHeader?: RenderHeader;
}

type RosterView = "cards" | "table";
type StatusFilter = NonNullable<MemberFilters["status"]>;
type KindFilter = NonNullable<MemberFilters["kind"]>;

export function TeamTab({
  projectId,
  copy,
  period,
  onOpenMember,
  onCreateAgent,
  renderHeader = defaultHeader,
}: TeamTabProps) {
  const text = copy.agentTeam;
  const labels = text.team;
  const locale = teamLocale(copy);
  const [view, setView] = useState<RosterView>("cards");
  const [owner, setOwner] = useState("");
  const [status, setStatus] = useState<StatusFilter | "">("");
  const [kind, setKind] = useState<KindFilter | "">("");
  const [sorting, setSorting] = useState<RosterSort>({});
  const filters = useMemo<MemberFilters>(
    () => ({
      owner: owner || null,
      status: status || null,
      kind: kind || null,
      sort: sorting.sort,
      order: sorting.order,
    }),
    [owner, status, kind, sorting],
  );
  const summary = useTeamSummary(projectId, period);
  const members = useTeamMembers(projectId, period, filters);
  const people = useTeamPeople(projectId, period);
  const orgChart = useOrgChart(projectId, period);
  // Cards always show the last 30 days, whatever period is selected.
  const { query: spark, byAgentKey: sparklineByAgentKey } =
    useSparklines(projectId);

  // Keep the last answer while a changed filter's query has no data yet, so
  // the filter controls stay mounted (and focused) instead of being replaced
  // by the loading state.
  const lastMembers = useRef(members.data);
  if (members.data !== undefined) lastMembers.current = members.data;
  const membersData = members.data ?? lastMembers.current;
  const rows = membersData?.data;
  const owners = [
    ...new Set([
      ...(owner === "" ? [] : [owner]),
      ...(people.data?.data ?? [])
        .filter((person) => person.ownedAgents > 0)
        .map((person) => person.actorId),
    ]),
  ].sort();
  const agentNames = useMemo(
    () =>
      Object.fromEntries(
        [...(spark.data?.data ?? []), ...(rows ?? [])].map((r) => [
          r.agentKey,
          r.name,
        ]),
      ),
    [spark.data, rows],
  );
  // A failed filtered query keeps the filters on screen (error below them)
  // so the user can change or clear the filter instead of being trapped.
  const membersFailed = members.isError && members.data === undefined;
  const refreshing =
    members.isPlaceholderData ||
    summary.isPlaceholderData ||
    (members.data === undefined && membersData !== undefined && !membersFailed);

  if (
    (summary.data === undefined || membersData === undefined) &&
    !summary.isError &&
    !members.isError
  )
    return (
      <div className="tap-team-tab">
        {renderHeader(null)}
        <TeamSkeleton copy={copy} />
      </div>
    );
  if (summary.data === undefined || membersData === undefined)
    return (
      <div className="tap-team-tab">
        {renderHeader(null)}
        <LoadError
          copy={copy}
          onRetry={() => {
            void summary.refetch();
            void members.refetch();
          }}
        />
      </div>
    );

  const view_ = summary.data;
  const sum = view_.data;
  const ctx: CellContext = {
    copy,
    locale,
    timezone: view_.timezone,
    runtimeStatus: view_.runtimeStatus,
  };
  const runValue = (value: string | null) => (
    <RunValue value={value} ctx={ctx} />
  );
  const nullable = (v: number | null | undefined) =>
    v === null || v === undefined ? null : formatCount(v, locale);
  // Four headline numbers with a caption, then point-in-time counts as a
  // compact strip.
  const primary: { label: string; value: ReactNode; caption: string }[] = [
    {
      label: text.metrics.runs,
      value: runValue(nullable(sum.runs)),
      caption: text.kpiCaptions.runs,
    },
    {
      label: text.metrics.cost,
      value: runValue(
        sum.costUsd === null || sum.costUsd === undefined
          ? null
          : formatCost(sum.costUsd, locale),
      ),
      caption: text.kpiCaptions.cost,
    },
    {
      label: text.metrics.latency,
      value: runValue(
        sum.latencyTotalMs === null || sum.latencyTotalMs === undefined
          ? null
          : formatDuration(sum.latencyTotalMs, locale, text.durationUnits),
      ),
      caption: text.kpiCaptions.latency,
    },
    {
      label: text.metrics.active,
      value: runValue(nullable(sum.active)),
      caption: text.kpiCaptions.active.replace(
        "{total}",
        formatCount(sum.agentTotal, locale),
      ),
    },
  ];
  const secondary: { label: string; value: ReactNode }[] = [
    {
      label: text.metrics.agentTotal,
      value: formatCount(sum.agentTotal, locale),
    },
    { label: text.metrics.idle, value: runValue(nullable(sum.idle)) },
    {
      label: text.metrics.ownersAvg,
      value: formatCount(sum.ownersAvg, locale),
    },
    {
      label: text.metrics.servedAvg,
      // Its denominator is this period's callers, which is run data.
      value: runValue(nullable(sum.servedAvg)),
    },
    {
      label: text.metrics.maintenance,
      value: formatCount(sum.maintenanceTotal, locale),
    },
  ];
  const filtering = owner !== "" || status !== "" || kind !== "";
  const empty = (rows?.length ?? 0) === 0 && !filtering;

  return (
    <div className="tap-team-tab">
      {renderHeader(
        <RuntimeBadge
          status={view_.runtimeStatus}
          computedAt={view_.computedAt}
          timezone={view_.timezone}
          copy={copy}
        />,
      )}
      <dl className="tap-team-kpis">
        {primary.map((metric) => (
          <div key={metric.label} className="tap-team-kpi">
            <dt>{metric.label}</dt>
            <dd>
              <span className="tap-team-kpi-value">{metric.value}</span>
              <span className="tap-team-kpi-caption">{metric.caption}</span>
            </dd>
          </div>
        ))}
      </dl>
      <dl className="tap-team-stats">
        {secondary.map((metric) => (
          <div key={metric.label}>
            <dt>{metric.label}</dt>
            <dd>{metric.value}</dd>
          </div>
        ))}
      </dl>
      <section className="tap-team-section">
        <div className="tap-team-section-head">
          <h3>{labels.rosterHeading}</h3>
          {empty ? null : (
            <span className="tap-team-section-count">
              {formatCount(rows?.length ?? 0, locale)}
            </span>
          )}
          {refreshing && view === "cards" ? (
            <p role="status" className="tap-team-refreshing">
              {text.loading}
            </p>
          ) : null}
        </div>
        {empty ? (
          <EmptyRoster copy={copy} onCreateAgent={onCreateAgent} />
        ) : (
          <>
            <div className="tap-team-filters">
              <label>
                <span>{labels.filterOwner}</span>
                <select
                  value={owner}
                  onChange={(e) => setOwner(e.target.value)}
                >
                  <option value="">{labels.allOwners}</option>
                  {owners.map((id) => (
                    <option key={id} value={id}>
                      {id}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>{labels.filterStatus}</span>
                <select
                  value={status}
                  onChange={(e) =>
                    setStatus(e.target.value as StatusFilter | "")
                  }
                >
                  <option value="">{labels.allStatuses}</option>
                  {(["active", "idle", "disabled"] as const).map((id) => (
                    <option key={id} value={id}>
                      {text.status[id]}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>{labels.filterKind}</span>
                <select
                  value={kind}
                  onChange={(e) => setKind(e.target.value as KindFilter | "")}
                >
                  <option value="">{labels.allKinds}</option>
                  {(["custom", "built_in", "tapper_default"] as const).map(
                    (id) => (
                      <option key={id} value={id}>
                        {labels.kinds[id]}
                      </option>
                    ),
                  )}
                </select>
              </label>
              <div
                role="group"
                aria-label={labels.viewLabel}
                className="tap-team-segmented"
              >
                {(["cards", "table"] as const).map((id) => (
                  <button
                    key={id}
                    type="button"
                    aria-pressed={view === id}
                    onClick={() => setView(id)}
                  >
                    <ViewIcon kind={id} />
                    {id === "cards" ? labels.viewCards : labels.viewTable}
                  </button>
                ))}
              </div>
            </div>
            {membersFailed ? (
              <LoadError copy={copy} onRetry={() => void members.refetch()} />
            ) : view === "cards" ? (
              <RosterCards
                rows={rows ?? []}
                sparklineByAgentKey={sparklineByAgentKey}
                ctx={ctx}
                onOpenMember={onOpenMember}
              />
            ) : (
              <RosterTable
                loading={refreshing}
                rows={rows ?? []}
                ctx={ctx}
                sorting={sorting}
                onSort={setSorting}
                onOpenMember={onOpenMember}
              />
            )}
          </>
        )}
      </section>
      <section className="tap-team-section">
        <div className="tap-team-section-head">
          <h3>{labels.peopleHeading}</h3>
          {people.data === undefined ? null : (
            <span className="tap-team-section-count">
              {formatCount(people.data.data.length, locale)}
            </span>
          )}
        </div>
        {people.isPending ? (
          <TeamSkeleton copy={copy} kpis={false} cards={1} />
        ) : people.isError || people.data === undefined ? (
          <LoadError copy={copy} onRetry={() => void people.refetch()} />
        ) : (
          <PeopleTable
            rows={people.data.data}
            copy={copy}
            locale={locale}
            runtimeStatus={people.data.runtimeStatus}
          />
        )}
      </section>
      <section className="tap-team-section">
        <div className="tap-team-section-head">
          <h3>{labels.orgChartHeading}</h3>
        </div>
        {orgChart.isPending ? (
          <TeamSkeleton copy={copy} kpis={false} cards={2} />
        ) : orgChart.isError || orgChart.data === undefined ? (
          <LoadError copy={copy} onRetry={() => void orgChart.refetch()} />
        ) : (
          <OrgChart
            chart={orgChart.data.data}
            agentNames={agentNames}
            copy={copy}
            onOpenMember={onOpenMember}
          />
        )}
      </section>
    </div>
  );
}

function ViewIcon({ kind }: { kind: "cards" | "table" }) {
  return (
    <svg aria-hidden="true" width="14" height="14" viewBox="0 0 14 14">
      {kind === "cards" ? (
        <g fill="none" stroke="currentColor" strokeWidth="1.3">
          <rect x="1.5" y="1.5" width="4.5" height="4.5" rx="1" />
          <rect x="8" y="1.5" width="4.5" height="4.5" rx="1" />
          <rect x="1.5" y="8" width="4.5" height="4.5" rx="1" />
          <rect x="8" y="8" width="4.5" height="4.5" rx="1" />
        </g>
      ) : (
        <g stroke="currentColor" strokeWidth="1.3" strokeLinecap="round">
          <line x1="2" y1="3.5" x2="12" y2="3.5" />
          <line x1="2" y1="7" x2="12" y2="7" />
          <line x1="2" y1="10.5" x2="12" y2="10.5" />
        </g>
      )}
    </svg>
  );
}
