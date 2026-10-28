import { Table, type TableProps } from "antd";

import type { AgentTeamPersonRow } from "../../../../../features/knowledge/api/agentTeam";
import type { WorkspaceCopy } from "../../copy";
import { Avatar } from "./avatar";
import { formatCount } from "./format";
import { RunValue } from "./memberCells";
import type { RuntimeStatus } from "./RuntimeBadge";

export interface PeopleTableProps {
  rows: AgentTeamPersonRow[];
  copy: WorkspaceCopy;
  locale: string;
  runtimeStatus: RuntimeStatus;
  /** Report layout: no sort affordances. */
  readOnly?: boolean;
}

// Nulls (run data unavailable) sort before any number.
function compare(a: number | null, b: number | null): number {
  return (a ?? -1) - (b ?? -1);
}

export function PeopleTable({
  rows,
  copy,
  locale,
  runtimeStatus,
  readOnly = false,
}: PeopleTableProps) {
  const cols = copy.agentTeam.team.columns;
  const run = (value: number | null) => (
    <RunValue
      value={value === null ? null : formatCount(value, locale)}
      ctx={{ copy, runtimeStatus }}
    />
  );
  const columnDefs: TableProps<AgentTeamPersonRow>["columns"] = [
    {
      title: cols.actor,
      dataIndex: "actorId",
      key: "actor_id",
      fixed: readOnly ? undefined : ("left" as const),
      className: "tap-team-col-name",
      sorter: (a, b) => a.actorId.localeCompare(b.actorId),
    },
    {
      title: cols.ownedAgents,
      key: "owned_agents",
      align: "right" as const,
      className: "tap-team-nowrap tap-team-num",
      sorter: (a, b) => compare(a.ownedAgents, b.ownedAgents),
      render: (_: unknown, row) => formatCount(row.ownedAgents, locale),
    },
    {
      title: cols.ownedRuns,
      key: "owned_runs",
      align: "right" as const,
      className: "tap-team-nowrap tap-team-num",
      sorter: (a, b) => compare(a.ownedRuns, b.ownedRuns),
      render: (_: unknown, row) => run(row.ownedRuns),
    },
    {
      title: cols.servedByAgents,
      key: "served_by_agents",
      align: "right" as const,
      className: "tap-team-nowrap tap-team-num",
      sorter: (a, b) => compare(a.servedByAgents, b.servedByAgents),
      render: (_: unknown, row) => run(row.servedByAgents),
    },
    {
      title: cols.calls,
      key: "runs",
      align: "right" as const,
      className: "tap-team-nowrap tap-team-num",
      sorter: (a, b) => compare(a.runs, b.runs),
      render: (_: unknown, row) => run(row.runs),
    },
    {
      title: cols.maintenance,
      key: "maintenance",
      align: "right" as const,
      className: "tap-team-nowrap tap-team-num",
      sorter: (a, b) => compare(a.maintenance, b.maintenance),
      render: (_: unknown, row) => formatCount(row.maintenance, locale),
    },
  ];
  const columns = readOnly
    ? columnDefs?.map((column) => ({ ...column, sorter: undefined }))
    : columnDefs;
  return (
    <div className="tap-team-table-scroll">
      <Table<AgentTeamPersonRow>
        className="tap-team-table"
        size="small"
        rowKey="actorId"
        pagination={false}
        dataSource={rows}
        columns={columns}
        showSorterTooltip={false}
        scroll={readOnly ? undefined : { x: "max-content" }}
        locale={{ emptyText: copy.agentTeam.team.noPeople }}
      />
    </div>
  );
}

/** People ranked by the calls they made, as proportional bars; ownership,
 * usage breadth and maintenance ride along as chips. */
export function PeopleRanking({
  rows,
  copy,
  locale,
  runtimeStatus,
}: Omit<PeopleTableProps, "readOnly">) {
  const labels = copy.agentTeam.team;
  if (rows.length === 0)
    return <p className="tap-team-empty">{labels.noPeople}</p>;
  // Without run data, rank by ownership instead of an unknown call count.
  const ranked = [...rows].sort(
    (a, b) =>
      (b.runs ?? -1) - (a.runs ?? -1) ||
      b.ownedAgents - a.ownedAgents ||
      a.actorId.localeCompare(b.actorId),
  );
  const max = Math.max(...rows.map((row) => row.runs ?? 0), 1);
  const ctx = { copy, runtimeStatus };
  const chip = (template: string, value: number | null) =>
    value === null || value === 0
      ? null
      : template.replace("{count}", formatCount(value, locale));
  return (
    <ol className="tap-team-people-ranking">
      {ranked.map((row, index) => {
        const chips = [
          chip(labels.ownsChip, row.ownedAgents),
          chip(labels.usesChip, row.servedByAgents),
          chip(labels.maintenanceChip, row.maintenance),
        ].filter((value): value is string => value !== null);
        return (
          <li key={row.actorId}>
            <span className="tap-team-rank">{index + 1}</span>
            <Avatar name={row.actorId} size="sm" />
            <span className="tap-team-people-id">
              <strong>{row.actorId}</strong>
              {chips.length === 0 ? null : (
                <span className="tap-team-chips">
                  {chips.map((text) => (
                    <span key={text} className="tap-team-chip">
                      {text}
                    </span>
                  ))}
                </span>
              )}
            </span>
            <span className="tap-team-people-bar" aria-hidden="true">
              {row.runs === null || row.runs === 0 ? null : (
                <span style={{ width: `${(row.runs / max) * 100}%` }} />
              )}
            </span>
            <span className="tap-team-people-value">
              <span className="tap-team-sr-only">{labels.columns.calls} </span>
              <RunValue
                value={row.runs === null ? null : formatCount(row.runs, locale)}
                ctx={ctx}
              />
            </span>
          </li>
        );
      })}
    </ol>
  );
}
