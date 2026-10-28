import type { AgentTeamMemberRow } from "../../../../../features/knowledge/api/agentTeam";
import type { WorkspaceCopy } from "../../copy";
import { avatarTone, initialOf } from "./avatar";
import { EMPTY, formatDateTime, formatPercent, formatRelative } from "./format";
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

/** Status as a tinted pill with a dot; unknown status (runtime outage) is a
 * dash with the notice. */
export function StatusPill({
  row,
  ctx,
}: {
  row: AgentTeamMemberRow;
  ctx: CellContext;
}) {
  if (row.status === null) return <RunValue value={null} ctx={ctx} />;
  return (
    <>
      <span className="tap-team-status-pill" data-status={row.status}>
        <span
          className="tap-team-status-dot"
          data-status={row.status}
          aria-hidden="true"
        />
        {ctx.copy.agentTeam.status[row.status]}
      </span>
      {ctx.runtimeStatus === "syncing" ? (
        <RuntimeMark status="syncing" copy={ctx.copy} />
      ) : null}
    </>
  );
}

/** Satisfaction as a small ring plus the percentage; too few ratings stays
 * text. */
export function SatisfactionMeter({
  row,
  ctx,
}: {
  row: AgentTeamMemberRow;
  ctx: CellContext;
}) {
  if (row.satisfaction === null)
    return <SatisfactionValue row={row} ctx={ctx} />;
  const r = 7;
  const c = 2 * Math.PI * r;
  const share = Math.min(Math.max(row.satisfaction, 0), 1);
  return (
    <span className="tap-team-meter">
      <svg aria-hidden="true" width="18" height="18" viewBox="0 0 18 18">
        <circle cx="9" cy="9" r={r} className="tap-team-meter-track" />
        <circle
          cx="9"
          cy="9"
          r={r}
          className="tap-team-meter-fill"
          data-level={share >= 0.8 ? "high" : share >= 0.5 ? "mid" : "low"}
          strokeDasharray={`${share * c} ${c}`}
          transform="rotate(-90 9 9)"
        />
      </svg>
      <span>{formatPercent(row.satisfaction, ctx.locale)}</span>
    </span>
  );
}

/** The first three people served as overlapping initials, then "+n"; the
 * names are readable text for assistive technology. */
export function ServedStack({
  row,
  ctx,
}: {
  row: AgentTeamMemberRow;
  ctx: CellContext;
}) {
  const labels = ctx.copy.agentTeam.team;
  if (row.topServed.length === 0)
    return ctx.runtimeStatus === "unavailable" || row.servedCount === null ? (
      <RunValue value={null} ctx={ctx} />
    ) : (
      <span className="tap-team-muted">{labels.noServed}</span>
    );
  const shown = row.topServed.slice(0, 3);
  const more = Math.max((row.servedCount ?? shown.length) - shown.length, 0);
  return (
    <span
      className="tap-team-stack"
      title={row.topServed
        .map((served) => `${served.actorId} · ${served.runs}`)
        .join("\n")}
    >
      <span className="tap-team-stack-faces" aria-hidden="true">
        {shown.map((served) => (
          <span
            key={served.actorId}
            className="tap-team-avatar"
            data-size="xs"
            data-tone={avatarTone(served.actorId)}
          >
            {initialOf(served.actorId)}
          </span>
        ))}
        {more > 0 ? (
          <span className="tap-team-stack-more">
            {labels.servedMore.replace("{count}", String(more))}
          </span>
        ) : null}
      </span>
      <span className="tap-team-sr-only">
        {shown.map((served) => served.actorId).join(", ")}
      </span>
    </span>
  );
}

/** "3 minutes ago", the exact time on hover; run-derived, so an outage reads
 * as unknown rather than never. */
export function RelativeTime({
  value,
  ctx,
  runDerived = true,
}: {
  value: string | null;
  ctx: CellContext;
  runDerived?: boolean;
}) {
  if (value === null)
    return runDerived ? <RunValue value={null} ctx={ctx} /> : <>{EMPTY}</>;
  const absolute = formatDateTime(value, ctx.timezone, ctx.locale);
  const time = (
    <time dateTime={value} title={absolute} className="tap-team-relative">
      {formatRelative(value, ctx.locale)}
    </time>
  );
  return runDerived && ctx.runtimeStatus === "syncing" ? (
    <>
      {time}
      <RuntimeMark status="syncing" copy={ctx.copy} />
    </>
  ) : (
    time
  );
}
