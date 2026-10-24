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
import {
  EMPTY,
  formatCost,
  formatCount,
  formatDuration,
  teamLocale,
} from "./format";
import type { CellContext } from "./memberCells";
import { OrgChart } from "./OrgChart";
import { PeopleTable } from "./PeopleTable";
import { RosterCards } from "./RosterCards";
import { RosterTable, type RosterSort } from "./RosterTable";
import { RuntimeBadge, RuntimeMark } from "./RuntimeBadge";
import { useSparklines } from "./useSparklines";

export interface TeamTabProps {
  projectId: string;
  copy: WorkspaceCopy;
  period: TeamPeriod;
  onOpenMember: (agentKey: string) => void;
  onCreateAgent: () => void;
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
  const refreshing =
    members.isPlaceholderData ||
    summary.isPlaceholderData ||
    (members.data === undefined && membersData !== undefined);

  if (
    (summary.data === undefined || membersData === undefined) &&
    !summary.isError &&
    !members.isError
  )
    return <p role="status">{text.loading}</p>;
  if (
    summary.data === undefined ||
    membersData === undefined ||
    (members.isError && members.data === undefined)
  )
    return (
      <div role="alert">
        <p>{text.loadFailed}</p>
        <button
          type="button"
          onClick={() => {
            void summary.refetch();
            void members.refetch();
          }}
        >
          {text.retry}
        </button>
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
    <>
      {value ?? EMPTY}
      {value === null ? (
        <RuntimeMark status={view_.runtimeStatus} copy={copy} />
      ) : null}
    </>
  );
  const nullable = (v: number | null | undefined) =>
    v === null || v === undefined ? null : formatCount(v, locale);
  const metrics: { label: string; value: ReactNode }[] = [
    {
      label: text.metrics.agentTotal,
      value: formatCount(sum.agentTotal, locale),
    },
    { label: text.metrics.active, value: runValue(nullable(sum.active)) },
    { label: text.metrics.idle, value: runValue(nullable(sum.idle)) },
    {
      label: text.metrics.ownersAvg,
      value: formatCount(sum.ownersAvg, locale),
    },
    {
      label: text.metrics.servedAvg,
      value: formatCount(sum.servedAvg, locale),
    },
    { label: text.metrics.runs, value: runValue(nullable(sum.runs)) },
    {
      label: text.metrics.latency,
      value: runValue(
        sum.latencyTotalMs === null || sum.latencyTotalMs === undefined
          ? null
          : formatDuration(sum.latencyTotalMs, locale),
      ),
    },
    {
      label: text.metrics.cost,
      value: runValue(
        sum.costUsd === null || sum.costUsd === undefined
          ? null
          : formatCost(sum.costUsd, locale),
      ),
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
      <RuntimeBadge
        status={view_.runtimeStatus}
        computedAt={view_.computedAt}
        timezone={view_.timezone}
        copy={copy}
      />
      <dl className="tap-team-summary">
        {metrics.map((metric) => (
          <div key={metric.label}>
            <dt>{metric.label}</dt>
            <dd>{metric.value}</dd>
          </div>
        ))}
      </dl>
      <section className="tap-team-section">
        <h3>{labels.rosterHeading}</h3>
        {empty ? (
          <div className="tap-team-empty">
            <p>{text.emptyState}</p>
            <button type="button" onClick={onCreateAgent}>
              {text.createAgent}
            </button>
          </div>
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
                    {id === "cards" ? labels.viewCards : labels.viewTable}
                  </button>
                ))}
              </div>
            </div>
            {refreshing && view === "cards" ? (
              <p role="status" className="tap-team-refreshing">
                {text.loading}
              </p>
            ) : null}
            {view === "cards" ? (
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
        <h3>{labels.peopleHeading}</h3>
        {people.isPending ? (
          <p role="status">{text.loading}</p>
        ) : people.isError || people.data === undefined ? (
          <div role="alert">
            <p>{text.loadFailed}</p>
            <button type="button" onClick={() => void people.refetch()}>
              {text.retry}
            </button>
          </div>
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
        <h3>{labels.orgChartHeading}</h3>
        {orgChart.isPending ? (
          <p role="status">{text.loading}</p>
        ) : orgChart.isError || orgChart.data === undefined ? (
          <div role="alert">
            <p>{text.loadFailed}</p>
            <button type="button" onClick={() => void orgChart.refetch()}>
              {text.retry}
            </button>
          </div>
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
