import { Table, type TableProps } from "antd";

import type { AgentTeamMemberRow } from "../../../../../features/knowledge/api/agentTeam";
import {
  formatCost,
  formatCount,
  formatDate,
  formatDateTime,
  formatDuration,
} from "./format";
import {
  PeriodActivityValue,
  RunValue,
  SatisfactionValue,
  StatusValue,
  ownerName,
  type CellContext,
} from "./memberCells";

/** Short values (status, numbers, durations, dates) never wrap mid-value;
 * the table scrolls sideways instead. */
const NOWRAP = "tap-team-nowrap";
/** Numbers read down a right-aligned, tabular column. */
const NUMERIC = "tap-team-nowrap tap-team-num";

export interface RosterSort {
  sort?: string;
  order?: "asc" | "desc";
}

export interface RosterTableProps {
  rows: AgentTeamMemberRow[];
  loading?: boolean;
  ctx: CellContext;
  sorting: RosterSort;
  onSort: (next: RosterSort) => void;
  onOpenMember: (agentKey: string) => void;
  /** Report layout: plain names, no sorting, and no scroll container so every
   * column prints. */
  readOnly?: boolean;
}

export function RosterTable({
  rows,
  loading = false,
  ctx,
  sorting,
  onSort,
  onOpenMember,
  readOnly = false,
}: RosterTableProps) {
  const labels = ctx.copy.agentTeam.team;
  const cols = labels.columns;
  const n = (value: number | null) =>
    value === null ? null : formatCount(value, ctx.locale);
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
  const columns: TableProps<AgentTeamMemberRow>["columns"] = [
    {
      title: cols.name,
      ...sortable("name"),
      // The name stays in view while the metrics scroll sideways.
      fixed: readOnly ? undefined : ("left" as const),
      className: "tap-team-col-name",
      render: (_: unknown, row) =>
        readOnly ? (
          row.name
        ) : (
          <button
            type="button"
            className="tap-team-card-name"
            onClick={() => onOpenMember(row.agentKey)}
          >
            {row.name}
          </button>
        ),
    },
    {
      title: cols.jobTitle,
      ...sortable("job_title"),
      render: (_: unknown, row) => row.jobTitle ?? labels.noJobTitle,
    },
    {
      title: cols.owner,
      ...sortable("owner_actor_id"),
      render: (_: unknown, row) => ownerName(row, ctx.copy),
    },
    readOnly
      ? {
          title: labels.periodActivity.column,
          key: "period_activity",
          className: NOWRAP,
          render: (_: unknown, row) => (
            <PeriodActivityValue row={row} ctx={ctx} />
          ),
        }
      : {
          title: cols.status,
          ...sortable("status"),
          className: NOWRAP,
          render: (_: unknown, row) => <StatusValue row={row} ctx={ctx} />,
        },
    {
      title: cols.kind,
      ...sortable("kind"),
      className: NOWRAP,
      render: (_: unknown, row) => labels.kinds[row.kind],
    },
    {
      title: cols.runs,
      ...sortable("runs"),
      className: NUMERIC,
      align: "right" as const,
      render: (_: unknown, row) => <RunValue value={n(row.runs)} ctx={ctx} />,
    },
    {
      title: cols.failures,
      ...sortable("failures"),
      className: NUMERIC,
      align: "right" as const,
      render: (_: unknown, row) => (
        <RunValue value={n(row.failures)} ctx={ctx} />
      ),
    },
    {
      title: cols.latencyTotal,
      ...sortable("latency_total_ms"),
      className: NUMERIC,
      align: "right" as const,
      render: (_: unknown, row) => (
        <RunValue
          value={
            row.latencyTotalMs === null
              ? null
              : formatDuration(
                  row.latencyTotalMs,
                  ctx.locale,
                  ctx.copy.agentTeam.durationUnits,
                )
          }
          ctx={ctx}
        />
      ),
    },
    {
      title: cols.latencyP50,
      ...sortable("latency_p50_ms"),
      className: NUMERIC,
      align: "right" as const,
      render: (_: unknown, row) => (
        <RunValue
          value={
            row.latencyP50Ms === null
              ? null
              : formatDuration(
                  row.latencyP50Ms,
                  ctx.locale,
                  ctx.copy.agentTeam.durationUnits,
                )
          }
          ctx={ctx}
        />
      ),
    },
    {
      title: cols.cost,
      ...sortable("cost_usd"),
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
      ...sortable("served_count"),
      className: NUMERIC,
      align: "right" as const,
      render: (_: unknown, row) => (
        <RunValue value={n(row.servedCount)} ctx={ctx} />
      ),
    },
    {
      title: cols.lastRun,
      ...sortable("last_run_at"),
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
      ...sortable("satisfaction"),
      className: NOWRAP,
      render: (_: unknown, row) => <SatisfactionValue row={row} ctx={ctx} />,
    },
    {
      title: cols.lastMaintenance,
      ...sortable("last_maintenance_at"),
      className: NOWRAP,
      render: (_: unknown, row) =>
        formatDateTime(row.lastMaintenanceAt, ctx.timezone, ctx.locale),
    },
    {
      title: cols.pendingFeedback,
      ...sortable("pending_feedback"),
      className: NUMERIC,
      align: "right" as const,
      render: (_: unknown, row) => formatCount(row.pendingFeedback, ctx.locale),
    },
    {
      title: cols.joinedAt,
      ...sortable("joined_at"),
      className: NOWRAP,
      render: (_: unknown, row) =>
        formatDate(row.joinedAt, ctx.timezone, ctx.locale),
    },
  ];
  return (
    <Table<AgentTeamMemberRow>
      className="tap-team-table"
      size="small"
      rowKey="agentKey"
      pagination={false}
      loading={loading}
      locale={{ emptyText: labels.noMatch }}
      dataSource={rows}
      columns={columns}
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
