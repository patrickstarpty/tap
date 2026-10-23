import { useMemo, useRef, useState, type ReactNode } from "react";

import {
  useTeamMembers,
  useTeamPeople,
  useTeamSummary,
  type MemberFilters,
  type TeamPeriod,
} from "../../../../../features/knowledge/api/agentTeam";
import type { WorkspaceCopy } from "../../copy";
import { TrendChart } from "./charts";
import {
  EMPTY,
  formatCost,
  formatCount,
  formatDuration,
  teamLocale,
} from "./format";
import type { CellContext } from "./memberCells";
import { PeopleTable } from "./PeopleTable";
import { RosterCards } from "./RosterCards";
import { RosterTable, type RosterSort } from "./RosterTable";
import { RuntimeBadge, RuntimeMark } from "./RuntimeBadge";

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
  // Owner options accumulate so a filtered roster still offers every owner.
  const knownOwners = useRef(new Set<string>());

  const rows = members.data?.data;
  for (const row of rows ?? [])
    if (row.ownerActorId !== null) knownOwners.current.add(row.ownerActorId);
  const owners = [...knownOwners.current].sort();

  const trendSeries = useMemo(() => {
    const byBucket = new Map<string, number>();
    for (const row of rows ?? [])
      for (const point of row.trend)
        byBucket.set(
          point.bucketStart,
          (byBucket.get(point.bucketStart) ?? 0) + point.runs,
        );
    return [...byBucket.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([bucket, value]) => ({ bucket, value }));
  }, [rows]);

  if (summary.isPending || members.isPending)
    return <p role="status">{text.loading}</p>;
  if (
    summary.isError ||
    summary.data === undefined ||
    members.isError ||
    members.data === undefined
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
      {trendSeries.length > 1 ? (
        <section>
          <h3>{labels.trendHeading}</h3>
          <TrendChart
            unit={text.metrics.runs}
            series={[{ label: text.metrics.runs, points: trendSeries }]}
          />
        </section>
      ) : null}
      <section>
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
                {labels.filterOwner}
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
                {labels.filterStatus}
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
                {labels.filterKind}
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
              <div role="group" aria-label={labels.viewLabel}>
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
            {view === "cards" ? (
              <RosterCards
                rows={rows ?? []}
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
              />
            )}
          </>
        )}
      </section>
      <section>
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
    </div>
  );
}
