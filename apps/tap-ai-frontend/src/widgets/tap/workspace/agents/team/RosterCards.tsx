import type { AgentTeamMemberRow } from "../../../../../features/knowledge/api/agentTeam";
import { Avatar } from "./avatar";
import { formatCount, formatDateTime } from "./format";
import { Sparkline } from "./charts";
import {
  RunValue,
  SatisfactionValue,
  StatusValue,
  ownerName,
  type CellContext,
} from "./memberCells";

export interface RosterCardsProps {
  rows: AgentTeamMemberRow[];
  ctx: CellContext;
  sparklineByAgentKey: Map<string, number[]>;
  onOpenMember: (agentKey: string) => void;
}

/** Employee-badge cards. The name button stretches over the whole card, so
 * the card is one click/focus target without nesting interactive elements. */
export function RosterCards({
  rows,
  ctx,
  sparklineByAgentKey,
  onOpenMember,
}: RosterCardsProps) {
  const text = ctx.copy.agentTeam;
  const labels = text.team;
  if (rows.length === 0)
    return <p className="tap-team-empty">{labels.noMatch}</p>;
  const count = (value: number | null) =>
    value === null ? null : formatCount(value, ctx.locale);
  return (
    <ul className="tap-team-cards">
      {rows.map((row) => {
        const lastRun =
          row.lastRunAt === null
            ? null
            : formatDateTime(row.lastRunAt, ctx.timezone, ctx.locale);
        return (
          <li
            key={row.agentKey}
            className="tap-team-card"
            data-status={row.status ?? "unknown"}
          >
            <div className="tap-team-card-head">
              <Avatar name={row.name} />
              <div className="tap-team-card-id">
                <button
                  type="button"
                  className="tap-team-card-name"
                  onClick={() => onOpenMember(row.agentKey)}
                >
                  {row.name}
                </button>
                <p data-empty={row.jobTitle === null ? "true" : undefined}>
                  {row.jobTitle ?? labels.noJobTitle}
                </p>
              </div>
              <dl className="tap-team-card-status">
                <dt className="tap-team-sr-only">{labels.columns.status}</dt>
                <dd>
                  <StatusValue row={row} ctx={ctx} />
                </dd>
              </dl>
            </div>
            <dl className="tap-team-card-meta">
              <div>
                <dt>{labels.ownerLabel}</dt>
                <dd>{ownerName(row, ctx.copy)}</dd>
              </div>
              <div>
                <dt>{labels.topServed}</dt>
                <dd>
                  {row.topServed.length === 0 ? (
                    ctx.runtimeStatus === "unavailable" ? (
                      <RunValue value={null} ctx={ctx} />
                    ) : (
                      labels.noServed
                    )
                  ) : (
                    row.topServed
                      .slice(0, 3)
                      .map((served) => served.actorId)
                      .join(", ")
                  )}
                </dd>
              </div>
            </dl>
            {/* The sparkline is always the last 30 days, whatever period the
                stats below cover; the caption says so. */}
            <div className="tap-team-card-spark">
              <small className="tap-team-sparkline-caption">
                {labels.sparklineCaption}
              </small>
              {row.runs === null ? (
                <span className="tap-team-sparkline-empty" aria-hidden="true" />
              ) : (
                <Sparkline
                  points={sparklineByAgentKey.get(row.agentKey) ?? []}
                  label={`${row.name} ${labels.runsTrend}`}
                />
              )}
            </div>
            <dl className="tap-team-card-stats">
              <div>
                <dt>{labels.runsInPeriod}</dt>
                <dd>
                  <RunValue value={count(row.runs)} ctx={ctx} />
                </dd>
              </div>
              <div>
                <dt>{labels.columns.satisfaction}</dt>
                <dd>
                  <SatisfactionValue row={row} ctx={ctx} />
                </dd>
              </div>
              <div>
                <dt>{labels.columns.served}</dt>
                <dd>
                  <RunValue value={count(row.servedCount)} ctx={ctx} />
                </dd>
              </div>
            </dl>
            <dl className="tap-team-card-foot">
              <div>
                <dt>{labels.lastRun}</dt>
                <dd>
                  <RunValue value={lastRun} ctx={ctx} />
                </dd>
              </div>
              <div>
                <dt>{labels.lastMaintenance}</dt>
                <dd>
                  {formatDateTime(
                    row.lastMaintenanceAt,
                    ctx.timezone,
                    ctx.locale,
                  )}
                </dd>
              </div>
            </dl>
          </li>
        );
      })}
    </ul>
  );
}
