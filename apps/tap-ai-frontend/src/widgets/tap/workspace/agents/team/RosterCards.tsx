import type { AgentTeamMemberRow } from "../../../../../features/knowledge/api/agentTeam";
import { Avatar } from "./avatar";
import { Sparkline } from "./charts";
import {
  formatCost,
  formatCount,
  formatDuration,
  memberDisplayName,
} from "./format";
import {
  RelativeTime,
  RunValue,
  SatisfactionMeter,
  ServedStack,
  StatusPill,
  ownerName,
  type CellContext,
} from "./memberCells";

export interface RosterCardsProps {
  rows: AgentTeamMemberRow[];
  ctx: CellContext;
  sparklineByAgentKey: Map<string, number[]>;
  onOpenMember: (agentKey: string) => void;
}

/** Compact Agent cards with the leaderboard's numbers. The name button
 * stretches over the whole card, so the card is one click/focus target
 * without nesting interactive elements. */
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
      {rows.map((row) => (
        <li
          key={row.agentKey}
          className="tap-team-card"
          data-status={row.status ?? "unknown"}
        >
          <div className="tap-team-card-head">
            <Avatar name={memberDisplayName(row, text)} />
            <div className="tap-team-card-id">
              <button
                type="button"
                className="tap-team-card-name"
                onClick={() => onOpenMember(row.agentKey)}
              >
                {memberDisplayName(row, text)}
              </button>
              <p data-empty={row.jobTitle === null ? "true" : undefined}>
                {row.jobTitle ?? labels.noJobTitle}
              </p>
            </div>
            <dl className="tap-team-card-status">
              <dt className="tap-team-sr-only">{labels.columns.status}</dt>
              <dd>
                <StatusPill row={row} ctx={ctx} />
              </dd>
            </dl>
          </div>
          <div className="tap-team-card-hero">
            <dl>
              <dt>{labels.runsInPeriod}</dt>
              <dd>
                <RunValue value={count(row.runs)} ctx={ctx} />
              </dd>
            </dl>
            {/* The sparkline is always the last 30 days, whatever period
                the numbers cover; the caption says so. */}
            <div className="tap-team-card-spark">
              {row.runs === null ? (
                <span className="tap-team-sparkline-empty" aria-hidden="true" />
              ) : (
                <Sparkline
                  points={sparklineByAgentKey.get(row.agentKey) ?? []}
                  label={`${memberDisplayName(row, text)} ${labels.runsTrend}`}
                  height={34}
                />
              )}
              <small className="tap-team-sparkline-caption">
                {labels.sparklineCaption}
              </small>
            </div>
          </div>
          <dl className="tap-team-card-stats">
            <div>
              <dt>{labels.columns.satisfaction}</dt>
              <dd>
                <SatisfactionMeter row={row} ctx={ctx} />
              </dd>
            </div>
            <div>
              <dt>{labels.columns.latencyP50}</dt>
              <dd>
                <RunValue
                  value={
                    row.latencyP50Ms === null
                      ? null
                      : formatDuration(
                          row.latencyP50Ms,
                          ctx.locale,
                          text.durationUnits,
                        )
                  }
                  ctx={ctx}
                />
              </dd>
            </div>
            <div>
              <dt>{labels.columns.cost}</dt>
              <dd>
                <RunValue
                  value={
                    row.costUsd === null
                      ? null
                      : formatCost(row.costUsd, ctx.locale)
                  }
                  ctx={ctx}
                />
              </dd>
            </div>
          </dl>
          <dl className="tap-team-card-meta">
            <div>
              <dt>{labels.ownerLabel}</dt>
              <dd>{ownerName(row, ctx.copy)}</dd>
            </div>
            <div>
              <dt>{labels.topServed}</dt>
              <dd>
                <ServedStack row={row} ctx={ctx} />
              </dd>
            </div>
            <div>
              <dt>{labels.lastRun}</dt>
              <dd>
                <RelativeTime value={row.lastRunAt} ctx={ctx} />
              </dd>
            </div>
            <div>
              <dt>{labels.lastMaintenance}</dt>
              <dd>
                <RelativeTime
                  value={row.lastMaintenanceAt}
                  ctx={ctx}
                  runDerived={false}
                />
              </dd>
            </div>
          </dl>
        </li>
      ))}
    </ul>
  );
}
