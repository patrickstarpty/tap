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
          render: (_: unknown, row) => (
            <PeriodActivityValue row={row} ctx={ctx} />
          ),
        }
      : {
          title: cols.status,
          ...sortable("status"),
          render: (_: unknown, row) => <StatusValue row={row} ctx={ctx} />,
        },
    {
      title: cols.kind,
      ...sortable("kind"),
      render: (_: unknown, row) => labels.kinds[row.kind],
    },
    {
      title: cols.runs,
      ...sortable("runs"),
      render: (_: unknown, row) => <RunValue value={n(row.runs)} ctx={ctx} />,
    },
    {
      title: cols.failures,
      ...sortable("failures"),
      render: (_: unknown, row) => (
        <RunValue value={n(row.failures)} ctx={ctx} />
      ),
    },
    {
      title: cols.latencyTotal,
      ...sortable("latency_total_ms"),
      render: (_: unknown, row) => (
        <RunValue
          value={
            row.latencyTotalMs === null
              ? null
              : formatDuration(row.latencyTotalMs, ctx.locale)
          }
          ctx={ctx}
        />
      ),
    },
    {
      title: cols.latencyP50,
      ...sortable("latency_p50_ms"),
      render: (_: unknown, row) => (
        <RunValue
          value={
            row.latencyP50Ms === null
              ? null
              : formatDuration(row.latencyP50Ms, ctx.locale)
          }
          ctx={ctx}
        />
      ),
    },
    {
      title: cols.cost,
      ...sortable("cost_usd"),
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
      render: (_: unknown, row) => (
        <RunValue value={n(row.servedCount)} ctx={ctx} />
      ),
    },
    {
      title: cols.lastRun,
      ...sortable("last_run_at"),
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
      render: (_: unknown, row) => <SatisfactionValue row={row} ctx={ctx} />,
    },
    {
      title: cols.lastMaintenance,
      ...sortable("last_maintenance_at"),
      render: (_: unknown, row) =>
        formatDateTime(row.lastMaintenanceAt, ctx.timezone, ctx.locale),
    },
    {
      title: cols.pendingFeedback,
      ...sortable("pending_feedback"),
      render: (_: unknown, row) => formatCount(row.pendingFeedback, ctx.locale),
    },
    {
      title: cols.joinedAt,
      ...sortable("joined_at"),
      render: (_: unknown, row) =>
        formatDate(row.joinedAt, ctx.timezone, ctx.locale),
    },
  ];
  return (
    <Table<AgentTeamMemberRow>
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
