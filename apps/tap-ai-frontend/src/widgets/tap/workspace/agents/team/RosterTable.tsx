import { Table, type TableProps } from "antd";
import type { ReactNode } from "react";

import type { AgentTeamMemberRow } from "../../../../../features/knowledge/api/agentTeam";
import { Avatar } from "./avatar";
import { Sparkline } from "./charts";
import {
  formatCost,
  formatCount,
  formatDate,
  formatDateTime,
  formatDuration,
} from "./format";
import {
  PeriodActivityValue,
  RelativeTime,
  RunValue,
  SatisfactionMeter,
  SatisfactionValue,
  ServedStack,
  StatusPill,
  ownerName,
  type CellContext,
} from "./memberCells";
import { runRanks } from "./series";

/** Short values (status, numbers, durations, dates) never wrap mid-value;
 * the table scrolls sideways instead. */
const NOWRAP = "tap-team-nowrap";
/** Numbers read down a right-aligned, tabular column. */
const NUMERIC = "tap-team-nowrap tap-team-num";

export interface RosterSort {
  sort?: string;
  order?: "asc" | "desc";
}

/** Every server sort key (spec 6.1), in the order the sort menu lists them. */
export const ROSTER_SORT_KEYS = [
  "runs",
  "name",
  "job_title",
  "owner_actor_id",
  "status",
  "kind",
  "failures",
  "latency_total_ms",
  "latency_p50_ms",
  "cost_usd",
  "served_count",
  "satisfaction",
  "last_run_at",
  "last_maintenance_at",
  "pending_feedback",
  "joined_at",
] as const;

export interface RosterTableProps {
  rows: AgentTeamMemberRow[];
  loading?: boolean;
  ctx: CellContext;
  sorting: RosterSort;
  onSort: (next: RosterSort) => void;
  onOpenMember: (agentKey: string) => void;
  /** Last-30-day runs per Agent for the trend column. */
  sparklineByAgentKey?: Map<string, number[]>;
  /** Report layout: plain names, no sorting, and no scroll container so every
   * column prints. */
  readOnly?: boolean;
}

/** Each leaderboard cell names its column in hidden text, so a row reads as
 * label–value pairs like a card (headers already label it on screen). */
function Cell({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <span className="tap-team-cell-label" aria-hidden="true">
        {label}
      </span>
      {children}
    </>
  );
}

export function RosterTable({
  rows,
  loading = false,
  ctx,
  sorting,
  onSort,
  onOpenMember,
  sparklineByAgentKey,
  readOnly = false,
}: RosterTableProps) {
  const labels = ctx.copy.agentTeam.team;
  const cols = labels.columns;
  const n = (value: number | null) =>
    value === null ? null : formatCount(value, ctx.locale);
  const duration = (value: number | null) =>
    value === null
      ? null
      : formatDuration(value, ctx.locale, ctx.copy.agentTeam.durationUnits);
  const sortable = (key: string) => (readOnly ? { key } : sortableColumn(key));
  const sortableColumn = (key: string) => ({
    sorter: true as const,
    sortOrder:
      sorting.sort === key
        ? sorting.order === "desc"
          ? ("descend" as const)
          : ("ascend" as const)
        : null,
    key,
  });

  const reportColumns: TableProps<AgentTeamMemberRow>["columns"] = [
    { title: cols.name, key: "name", render: (_: unknown, row) => row.name },
    {
      title: cols.jobTitle,
      key: "job_title",
      render: (_: unknown, row) => row.jobTitle ?? labels.noJobTitle,
    },
    {
      title: cols.owner,
      key: "owner_actor_id",
      render: (_: unknown, row) => ownerName(row, ctx.copy),
    },
    {
      title: labels.periodActivity.column,
      key: "period_activity",
      className: NOWRAP,
      render: (_: unknown, row) => <PeriodActivityValue row={row} ctx={ctx} />,
    },
    {
      title: cols.kind,
      key: "kind",
      className: NOWRAP,
      render: (_: unknown, row) => labels.kinds[row.kind],
    },
    {
      title: cols.runs,
      key: "runs",
      className: NUMERIC,
      align: "right" as const,
      render: (_: unknown, row) => <RunValue value={n(row.runs)} ctx={ctx} />,
    },
    {
      title: cols.failures,
      key: "failures",
      className: NUMERIC,
      align: "right" as const,
      render: (_: unknown, row) => (
        <RunValue value={n(row.failures)} ctx={ctx} />
      ),
    },
    {
      title: cols.latencyTotal,
      key: "latency_total_ms",
      className: NUMERIC,
      align: "right" as const,
      render: (_: unknown, row) => (
        <RunValue value={duration(row.latencyTotalMs)} ctx={ctx} />
      ),
    },
    {
      title: cols.latencyP50,
      key: "latency_p50_ms",
      className: NUMERIC,
      align: "right" as const,
      render: (_: unknown, row) => (
        <RunValue value={duration(row.latencyP50Ms)} ctx={ctx} />
      ),
    },
    {
      title: cols.cost,
      key: "cost_usd",
      className: NUMERIC,
      align: "right" as const,
      render: (_: unknown, row) => (
        <RunValue
          value={
            row.costUsd === null ? null : formatCost(row.costUsd, ctx.locale)
          }
          ctx={ctx}
        />
      ),
    },
    {
      title: cols.served,
      key: "served_count",
      className: NUMERIC,
      align: "right" as const,
      render: (_: unknown, row) => (
        <RunValue value={n(row.servedCount)} ctx={ctx} />
      ),
    },
    {
      title: cols.lastRun,
      key: "last_run_at",
      className: NOWRAP,
      render: (_: unknown, row) => (
        <RunValue
          value={
            row.lastRunAt === null
              ? null
              : formatDateTime(row.lastRunAt, ctx.timezone, ctx.locale)
          }
          ctx={ctx}
        />
      ),
    },
    {
      title: cols.satisfaction,
      key: "satisfaction",
      className: NOWRAP,
      render: (_: unknown, row) => <SatisfactionValue row={row} ctx={ctx} />,
    },
    {
      title: cols.joinedAt,
      key: "joined_at",
      className: NOWRAP,
      render: (_: unknown, row) =>
        formatDate(row.joinedAt, ctx.timezone, ctx.locale),
    },
  ];

  const ranks = runRanks(rows);
  const maxRuns = Math.max(...rows.map((row) => row.runs ?? 0), 1);
  const leaderboard: TableProps<AgentTeamMemberRow>["columns"] = [
    {
      title: <span title={labels.rank}>#</span>,
      key: "rank",
      fixed: "left" as const,
      width: 44,
      className: "tap-team-col-rank tap-team-num",
      render: (_: unknown, row) => {
        const rank = ranks.get(row.agentKey);
        return (
          <span
            className="tap-team-rank"
            data-top={rank !== undefined && rank <= 3 ? "true" : undefined}
          >
            <span className="tap-team-sr-only">{labels.rank} </span>
            {rank === undefined ? "—" : rank}
          </span>
        );
      },
    },
    {
      title: cols.name,
      ...sortable("name"),
      // The name stays in view while the metrics scroll sideways.
      fixed: "left" as const,
      className: "tap-team-col-name",
      render: (_: unknown, row) => (
        <span className="tap-team-agent-cell">
          <Avatar name={row.name} size="sm" />
          <span className="tap-team-agent-id">
            <button
              type="button"
              className="tap-team-card-name"
              onClick={() => onOpenMember(row.agentKey)}
            >
              {row.name}
            </button>
            <span
              className="tap-team-agent-title"
              data-empty={row.jobTitle === null ? "true" : undefined}
            >
              {row.jobTitle ?? labels.noJobTitle}
            </span>
          </span>
        </span>
      ),
    },
    {
      title: cols.owner,
      ...sortable("owner_actor_id"),
      className: NOWRAP,
      render: (_: unknown, row) => (
        <Cell label={labels.ownerLabel}>
          <span
            className="tap-team-owner"
            data-platform={row.ownerActorId === null ? "true" : undefined}
          >
            {ownerName(row, ctx.copy)}
          </span>
        </Cell>
      ),
    },
    {
      title: cols.status,
      ...sortable("status"),
      className: NOWRAP,
      render: (_: unknown, row) => (
        <Cell label={cols.status}>
          <StatusPill row={row} ctx={ctx} />
        </Cell>
      ),
    },
    {
      title: labels.runsInPeriod,
      ...sortable("runs"),
      className: "tap-team-nowrap tap-team-col-runs",
      render: (_: unknown, row) => (
        <Cell label={labels.runsInPeriod}>
          <span className="tap-team-runs">
            <span className="tap-team-runs-value">
              <RunValue value={n(row.runs)} ctx={ctx} />
            </span>
            {row.runs === null ? null : (
              <span className="tap-team-runs-bar" aria-hidden="true">
                {row.runs > 0 ? (
                  <span style={{ width: `${(row.runs / maxRuns) * 100}%` }} />
                ) : null}
              </span>
            )}
          </span>
        </Cell>
      ),
    },
    {
      title: cols.satisfaction,
      ...sortable("satisfaction"),
      className: NOWRAP,
      render: (_: unknown, row) => (
        <Cell label={cols.satisfaction}>
          <SatisfactionMeter row={row} ctx={ctx} />
        </Cell>
      ),
    },
    {
      title: cols.latencyP50,
      ...sortable("latency_p50_ms"),
      className: NUMERIC,
      align: "right" as const,
      render: (_: unknown, row) => (
        <Cell label={cols.latencyP50}>
          <RunValue value={duration(row.latencyP50Ms)} ctx={ctx} />
        </Cell>
      ),
    },
    {
      title: cols.cost,
      ...sortable("cost_usd"),
      className: NUMERIC,
      align: "right" as const,
      render: (_: unknown, row) => (
        <Cell label={cols.cost}>
          <RunValue
            value={
              row.costUsd === null ? null : formatCost(row.costUsd, ctx.locale)
            }
            ctx={ctx}
          />
        </Cell>
      ),
    },
    {
      title: cols.served,
      ...sortable("served_count"),
      className: NOWRAP,
      render: (_: unknown, row) => (
        <Cell label={labels.topServed}>
          <ServedStack row={row} ctx={ctx} />
        </Cell>
      ),
    },
    {
      title: labels.last30Column,
      key: "trend",
      className: "tap-team-col-spark",
      render: (_: unknown, row) =>
        row.runs === null || sparklineByAgentKey === undefined ? (
          <span className="tap-team-sparkline-empty" aria-hidden="true" />
        ) : (
          <Sparkline
            points={sparklineByAgentKey.get(row.agentKey) ?? []}
            label={`${row.name} ${labels.runsTrend}`}
            height={26}
          />
        ),
    },
    {
      title: cols.lastRun,
      ...sortable("last_run_at"),
      className: NOWRAP,
      render: (_: unknown, row) => (
        <Cell label={labels.lastRun}>
          <RelativeTime value={row.lastRunAt} ctx={ctx} />
        </Cell>
      ),
    },
  ];

  return (
    <Table<AgentTeamMemberRow>
      className="tap-team-table"
      data-variant={readOnly ? "report" : "leaderboard"}
      size="small"
      rowKey="agentKey"
      rowClassName={(row) =>
        readOnly
          ? ""
          : `tap-team-row tap-team-card${row.status === "disabled" ? " tap-team-row--disabled" : ""}`
      }
      pagination={false}
      loading={loading}
      locale={{ emptyText: labels.noMatch }}
      dataSource={rows}
      columns={readOnly ? reportColumns : leaderboard}
      scroll={readOnly ? undefined : { x: "max-content" }}
      showSorterTooltip={false}
      onChange={(_p, _f, sorter) => {
        const single = Array.isArray(sorter) ? sorter[0] : sorter;
        if (single?.order && typeof single.columnKey === "string") {
          onSort({
            sort: single.columnKey,
            order: single.order === "descend" ? "desc" : "asc",
          });
        } else {
          onSort({});
        }
      }}
    />
  );
}
