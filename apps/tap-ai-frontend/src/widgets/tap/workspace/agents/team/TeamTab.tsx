import { useMemo, useRef, useState } from "react";

import {
  useOrgChart,
  useTeamMembers,
  useTeamPeople,
  useTeamReport,
  useTeamSummary,
  type MemberFilters,
  type PeriodPreset,
  type TeamPeriod,
} from "../../../../../features/knowledge/api/agentTeam";
import type { WorkspaceCopy } from "../../copy";
import { bucketAxis } from "./buckets";
import { formatCount, memberDisplayName, teamLocale } from "./format";
import { ActivityHeatmap } from "./Heatmap";
import { KpiStrip } from "./KpiStrip";
import {
  EmptyRoster,
  LoadError,
  TeamSkeleton,
  defaultHeader,
  type RenderHeader,
} from "./layout";
import { RunValue, type CellContext } from "./memberCells";
import { OrgChart, type OrgAgentStat } from "./OrgChart";
import { PeopleRanking, PeopleTable } from "./PeopleTable";
import { RosterCards } from "./RosterCards";
import { ROSTER_SORT_KEYS, RosterTable, type RosterSort } from "./RosterTable";
import { RuntimeBadge } from "./RuntimeBadge";
import { TrendPanel } from "./TrendPanel";
import { useSparklines } from "./useSparklines";

export interface TeamTabProps {
  projectId: string;
  copy: WorkspaceCopy;
  period: TeamPeriod;
  /** The picker choice behind `period`, so the comparison uses the right
   * previous period (spec 6.4). */
  preset?: PeriodPreset;
  onOpenMember: (agentKey: string) => void;
  onCreateAgent: () => void;
  renderHeader?: RenderHeader;
}

type RosterView = "table" | "cards";
type PeopleView = "ranking" | "details";
type StatusFilter = NonNullable<MemberFilters["status"]>;
type KindFilter = NonNullable<MemberFilters["kind"]>;
type SortKey = (typeof ROSTER_SORT_KEYS)[number];

const NO_FILTERS: MemberFilters = {};
/** Text-like keys read A→Z first; metrics and dates read largest first. */
const ASCENDING_FIRST = new Set<SortKey>([
  "name",
  "job_title",
  "owner_actor_id",
  "status",
  "kind",
]);

export function TeamTab({
  projectId,
  copy,
  period,
  preset,
  onOpenMember,
  onCreateAgent,
  renderHeader = defaultHeader,
}: TeamTabProps) {
  const text = copy.agentTeam;
  const labels = text.team;
  const locale = teamLocale(copy);
  const [view, setView] = useState<RosterView>("table");
  const [peopleView, setPeopleView] = useState<PeopleView>("ranking");
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
  // The whole team (unfiltered) feeds the headline numbers and charts; the
  // roster below has its own filtered, sorted query. Unfiltered, they share
  // one request.
  const team = useTeamMembers(projectId, period, NO_FILTERS);
  const members = useTeamMembers(projectId, period, filters);
  // The report carries the previous period for the deltas.
  const report = useTeamReport(projectId, period, preset);
  const people = useTeamPeople(projectId, period);
  const orgChart = useOrgChart(projectId, period);
  // The trend column and cards always show the last 30 days.
  const { query: spark, byAgentKey: sparklineByAgentKey } =
    useSparklines(projectId);

  // Keep the last answer while a changed filter's query has no data yet, so
  // the filter controls stay mounted (and focused) instead of being replaced
  // by the loading state.
  const lastMembers = useRef(members.data);
  if (members.data !== undefined) lastMembers.current = members.data;
  const membersData = members.data ?? lastMembers.current;
  const rows = membersData?.data;
  const teamRows = team.data?.data;
  const owners = [
    ...new Set([
      ...(owner === "" ? [] : [owner]),
      ...(people.data?.data ?? [])
        .filter((person) => person.ownedAgents > 0)
        .map((person) => person.actorId),
    ]),
  ].sort();
  const agentStats = useMemo(() => {
    const stats: Record<string, OrgAgentStat> = {};
    for (const row of [
      ...(spark.data?.data ?? []),
      ...(rows ?? []),
      ...(teamRows ?? []),
    ])
      stats[row.agentKey] = {
        name: memberDisplayName(row, text),
        runs: row.runs,
        status: row.status,
      };
    return stats;
  }, [spark.data, rows, teamRows, text]);
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
  const axis = bucketAxis(view_.period, view_.granularity);
  const reportData = report.data?.data;
  // Compare only when the report answers for the period on screen (a
  // placeholder is the previous period's report).
  const reportMatches =
    report.data !== undefined && report.isPlaceholderData !== true;
  const previous = reportMatches
    ? reportData!.previous.summary
    : report.isError
      ? null
      : undefined;
  const nullable = (v: number | null | undefined) =>
    v === null || v === undefined ? null : formatCount(v, locale);
  const runValue = (value: string | null) => (
    <RunValue value={value} ctx={ctx} />
  );
  // Point-in-time counts as a compact strip under the headline cards.
  const secondary = [
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
  const sortLabel = (key: SortKey): string => {
    const cols = labels.columns;
    const names: Record<SortKey, string> = {
      runs: labels.runsInPeriod,
      name: cols.name,
      job_title: cols.jobTitle,
      owner_actor_id: cols.owner,
      status: cols.status,
      kind: cols.kind,
      failures: cols.failures,
      latency_total_ms: cols.latencyTotal,
      latency_p50_ms: cols.latencyP50,
      cost_usd: cols.cost,
      served_count: cols.served,
      satisfaction: cols.satisfaction,
      last_run_at: cols.lastRun,
      last_maintenance_at: cols.lastMaintenance,
      pending_feedback: cols.pendingFeedback,
      joined_at: cols.joinedAt,
    };
    return names[key];
  };

  return (
    <div
      className="tap-team-tab"
      data-refreshing={refreshing || team.isPlaceholderData ? "true" : "false"}
    >
      {renderHeader(
        <RuntimeBadge
          status={view_.runtimeStatus}
          computedAt={view_.computedAt}
          timezone={view_.timezone}
          copy={copy}
        />,
      )}
      <KpiStrip
        summary={sum}
        previous={previous}
        members={teamRows}
        previousMembers={
          reportMatches ? reportData!.previous.members : undefined
        }
        previousPeriod={reportMatches ? reportData!.previousPeriod : undefined}
        axis={axis}
        ctx={ctx}
      />
      <dl className="tap-team-stats">
        {secondary.map((metric) => (
          <div key={metric.label}>
            <dt>{metric.label}</dt>
            <dd>{metric.value}</dd>
          </div>
        ))}
      </dl>
      <div className="tap-team-charts">
        <TrendPanel members={teamRows} axis={axis} ctx={ctx} />
        <section
          className="tap-team-panel-card tap-team-heatmap-card"
          aria-labelledby="team-heatmap-title"
        >
          <header className="tap-team-panel-card-head">
            <div>
              <h3 id="team-heatmap-title">{text.heatmap.heading}</h3>
              <p>{text.heatmap.subtitle}</p>
            </div>
          </header>
          {teamRows === undefined && view_.runtimeStatus !== "unavailable" ? (
            <div className="tap-team-chart-skeleton" aria-hidden="true" />
          ) : (
            <ActivityHeatmap
              members={teamRows}
              period={view_.period}
              granularity={view_.granularity}
              copy={copy}
              locale={locale}
              runtimeStatus={view_.runtimeStatus}
            />
          )}
        </section>
      </div>
      <section className="tap-team-section">
        <div className="tap-team-section-head">
          <h3>{labels.rosterHeading}</h3>
          {empty ? null : (
            <span className="tap-team-section-count">
              {formatCount(rows?.length ?? 0, locale)}
            </span>
          )}
          {refreshing ? (
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
              <span className="tap-team-filters-divider" aria-hidden="true" />
              <label>
                <span>{labels.sortLabel}</span>
                <select
                  value={sorting.sort ?? ""}
                  onChange={(e) => {
                    const key = e.target.value as SortKey | "";
                    setSorting(
                      key === ""
                        ? {}
                        : {
                            sort: key,
                            order: ASCENDING_FIRST.has(key) ? "asc" : "desc",
                          },
                    );
                  }}
                >
                  <option value="">{labels.sortDefault}</option>
                  {ROSTER_SORT_KEYS.map((key) => (
                    <option key={key} value={key}>
                      {sortLabel(key)}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="tap-team-icon-button"
                disabled={sorting.sort === undefined}
                aria-label={
                  sorting.order === "asc"
                    ? labels.sortAscending
                    : labels.sortDescending
                }
                title={
                  sorting.order === "asc"
                    ? labels.sortAscending
                    : labels.sortDescending
                }
                onClick={() =>
                  setSorting((prev) => ({
                    ...prev,
                    order: prev.order === "asc" ? "desc" : "asc",
                  }))
                }
              >
                <SortIcon ascending={sorting.order === "asc"} />
              </button>
              <div
                role="group"
                aria-label={labels.viewLabel}
                className="tap-team-segmented"
              >
                {(["table", "cards"] as const).map((id) => (
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
                rows={rows ?? []}
                ctx={ctx}
                sorting={sorting}
                onSort={setSorting}
                onOpenMember={onOpenMember}
                sparklineByAgentKey={sparklineByAgentKey}
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
          <div
            role="group"
            aria-label={labels.peopleViewLabel}
            className="tap-team-segmented tap-team-section-tools"
          >
            {(["ranking", "details"] as const).map((id) => (
              <button
                key={id}
                type="button"
                aria-pressed={peopleView === id}
                onClick={() => setPeopleView(id)}
              >
                <ViewIcon kind={id === "ranking" ? "bars" : "table"} />
                {id === "ranking" ? labels.viewRanking : labels.viewDetails}
              </button>
            ))}
          </div>
        </div>
        {people.isPending ? (
          <TeamSkeleton copy={copy} kpis={false} cards={1} />
        ) : people.isError || people.data === undefined ? (
          <LoadError copy={copy} onRetry={() => void people.refetch()} />
        ) : peopleView === "ranking" ? (
          <PeopleRanking
            rows={people.data.data}
            copy={copy}
            locale={locale}
            runtimeStatus={people.data.runtimeStatus}
          />
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
            agentStats={agentStats}
            personRuns={Object.fromEntries(
              (people.data?.data ?? []).map((p) => [p.actorId, p.runs]),
            )}
            copy={copy}
            locale={locale}
            onOpenMember={onOpenMember}
          />
        )}
      </section>
    </div>
  );
}

function SortIcon({ ascending }: { ascending: boolean }) {
  return (
    <svg aria-hidden="true" width="14" height="14" viewBox="0 0 14 14">
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {ascending ? (
          <path d="M4 11V3M1.8 5.2 4 3l2.2 2.2M8.5 4h4M8.5 7h3M8.5 10h2" />
        ) : (
          <path d="M4 3v8M1.8 8.8 4 11l2.2-2.2M8.5 4h2M8.5 7h3M8.5 10h4" />
        )}
      </g>
    </svg>
  );
}

function ViewIcon({ kind }: { kind: "cards" | "table" | "bars" }) {
  return (
    <svg aria-hidden="true" width="14" height="14" viewBox="0 0 14 14">
      {kind === "cards" ? (
        <g fill="none" stroke="currentColor" strokeWidth="1.3">
          <rect x="1.5" y="1.5" width="4.5" height="4.5" rx="1" />
          <rect x="8" y="1.5" width="4.5" height="4.5" rx="1" />
          <rect x="1.5" y="8" width="4.5" height="4.5" rx="1" />
          <rect x="8" y="8" width="4.5" height="4.5" rx="1" />
        </g>
      ) : kind === "bars" ? (
        <g stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
          <line x1="2" y1="3.5" x2="12" y2="3.5" />
          <line x1="2" y1="7" x2="9" y2="7" />
          <line x1="2" y1="10.5" x2="6" y2="10.5" />
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
