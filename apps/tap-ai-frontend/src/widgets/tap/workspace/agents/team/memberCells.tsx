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
  return row.ownerActorId ?? copy.agentTeam.platformOwner;
}

/** Run-derived values need only the copy and the runtime status. */
export type RunContext = Pick<CellContext, "copy" | "runtimeStatus">;

/** A run-derived value. The marker follows the runtime status: syncing
 * annotates a shown value, unavailable shows "—" plus the notice. A value
 * that is legitimately empty (e.g. never ran) is a plain "—". */
export function RunValue({
  value,
  ctx,
}: {
  value: string | null;
  ctx: RunContext;
}) {
  const missing = value === null || value === EMPTY;
  if (missing)
    return (
      <>
        {EMPTY}
        {ctx.runtimeStatus === "unavailable" ? (
          <RuntimeMark status="unavailable" copy={ctx.copy} />
        ) : null}
      </>
    );
  return (
    <>
      {value}
      {ctx.runtimeStatus === "syncing" ? (
        <RuntimeMark status="syncing" copy={ctx.copy} />
      ) : null}
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
    <>
      {row.status === null ? null : (
        <span
          className="tap-team-status-dot"
          data-status={row.status}
          aria-hidden="true"
        />
      )}
      <RunValue
        value={
          row.status === null ? null : ctx.copy.agentTeam.status[row.status]
        }
        ctx={ctx}
      />
    </>
  );
}

/** Report layout (spec 6.4): whether the Agent ran in the selected period,
 * not its trailing-30-day status; disabled still reads "disabled". */
export function PeriodActivityValue({
  row,
  ctx,
}: {
  row: AgentTeamMemberRow;
  ctx: CellContext;
}) {
  const text = ctx.copy.agentTeam;
  if (row.status === "disabled") return <>{text.status.disabled}</>;
  const value =
    row.runs === null
      ? null
      : row.runs > 0
        ? text.team.periodActivity.ran
        : text.team.periodActivity.noRuns;
  return <RunValue value={value} ctx={ctx} />;
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
