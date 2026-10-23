import type { AgentTeamMemberRow } from "../../../../../features/knowledge/api/agentTeam";
import type { WorkspaceCopy } from "../../copy";
import { EMPTY, formatPercent } from "./format";
import { RuntimeMark, type RuntimeStatus } from "./RuntimeBadge";

export interface CellContext {
  copy: WorkspaceCopy;
  locale: string;
  timezone: string;
  runtimeStatus: RuntimeStatus;
}

export function ownerName(row: AgentTeamMemberRow, copy: WorkspaceCopy) {
  return row.ownerActorId ?? copy.agentTeam.builtIn;
}

/** A run-derived value; "—" plus the runtime marker when it is missing. */
export function RunValue({
  value,
  ctx,
}: {
  value: string | null;
  ctx: CellContext;
}) {
  if (value !== null && value !== EMPTY) return <>{value}</>;
  return (
    <>
      {EMPTY}
      <RuntimeMark status={ctx.runtimeStatus} copy={ctx.copy} />
    </>
  );
}

export function StatusValue({
  row,
  ctx,
}: {
  row: AgentTeamMemberRow;
  ctx: CellContext;
}) {
  return (
    <RunValue
      value={row.status === null ? null : ctx.copy.agentTeam.status[row.status]}
      ctx={ctx}
    />
  );
}

export function SatisfactionValue({
  row,
  ctx,
}: {
  row: AgentTeamMemberRow;
  ctx: CellContext;
}) {
  if (row.satisfaction === null) {
    return row.satisfactionSample === "insufficient" ? (
      <>{ctx.copy.agentTeam.insufficientSample}</>
    ) : (
      <>{EMPTY}</>
    );
  }
  return <>{formatPercent(row.satisfaction, ctx.locale)}</>;
}
