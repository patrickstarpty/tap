import { Table, type TableProps } from "antd";

import type { AgentTeamPersonRow } from "../../../../../features/knowledge/api/agentTeam";
import type { WorkspaceCopy } from "../../copy";
import { EMPTY, formatCount } from "./format";
import { RuntimeMark, type RuntimeStatus } from "./RuntimeBadge";

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
  const run = (value: number | null) =>
    value === null ? (
      <>
        {EMPTY}
        <RuntimeMark status={runtimeStatus} copy={copy} />
      </>
    ) : (
      formatCount(value, locale)
    );
  const columnDefs: TableProps<AgentTeamPersonRow>["columns"] = [
    {
      title: cols.actor,
      dataIndex: "actorId",
      key: "actor_id",
      sorter: (a, b) => a.actorId.localeCompare(b.actorId),
    },
    {
      title: cols.ownedAgents,
      key: "owned_agents",
      sorter: (a, b) => compare(a.ownedAgents, b.ownedAgents),
      render: (_: unknown, row) => formatCount(row.ownedAgents, locale),
    },
    {
      title: cols.ownedRuns,
      key: "owned_runs",
      sorter: (a, b) => compare(a.ownedRuns, b.ownedRuns),
      render: (_: unknown, row) => run(row.ownedRuns),
    },
    {
      title: cols.servedByAgents,
      key: "served_by_agents",
      sorter: (a, b) => compare(a.servedByAgents, b.servedByAgents),
      render: (_: unknown, row) => run(row.servedByAgents),
    },
    {
      title: cols.calls,
      key: "runs",
      sorter: (a, b) => compare(a.runs, b.runs),
      render: (_: unknown, row) => run(row.runs),
    },
    {
      title: cols.maintenance,
      key: "maintenance",
      sorter: (a, b) => compare(a.maintenance, b.maintenance),
      render: (_: unknown, row) => formatCount(row.maintenance, locale),
    },
  ];
  const columns = readOnly
    ? columnDefs?.map((column) => ({ ...column, sorter: undefined }))
    : columnDefs;
  return (
    <Table<AgentTeamPersonRow>
      size="small"
      rowKey="actorId"
      pagination={false}
      dataSource={rows}
      columns={columns}
      showSorterTooltip={false}
      locale={{ emptyText: copy.agentTeam.team.noPeople }}
    />
  );
}
