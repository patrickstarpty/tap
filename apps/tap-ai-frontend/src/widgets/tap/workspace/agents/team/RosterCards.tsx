import type { AgentTeamMemberRow } from "../../../../../features/knowledge/api/agentTeam";
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
  onOpenMember: (agentKey: string) => void;
}

export function RosterCards({ rows, ctx, onOpenMember }: RosterCardsProps) {
  const text = ctx.copy.agentTeam;
  const labels = text.team;
  return (
    <ul className="tap-team-cards">
      {rows.map((row) => {
        const lastRun =
          row.lastRunAt === null
            ? null
            : formatDateTime(row.lastRunAt, ctx.timezone, ctx.locale);
        return (
          <li key={row.agentKey} className="tap-team-card">
            <div className="tap-team-card-head">
              <span className="tap-team-avatar" aria-hidden="true">
                {Array.from(row.name)[0] ?? ""}
              </span>
              <div>
                <button
                  type="button"
                  className="tap-team-card-name"
                  onClick={() => onOpenMember(row.agentKey)}
                >
                  {row.name}
                </button>
                <p>{row.jobTitle ?? labels.noJobTitle}</p>
              </div>
            </div>
            <dl>
              <div>
                <dt>{labels.ownerLabel}</dt>
                <dd>{ownerName(row, ctx.copy)}</dd>
              </div>
              <div>
                <dt>{labels.columns.status}</dt>
                <dd>
                  <StatusValue row={row} ctx={ctx} />
                </dd>
              </div>
              <div>
                <dt>{labels.runsTrend}</dt>
                <dd>
                  {row.runs === null ? (
                    <RunValue value={null} ctx={ctx} />
                  ) : (
                    <>
                      {formatCount(row.runs, ctx.locale)}
                      <Sparkline
                        points={row.trend.map((point) => point.runs)}
                        label={`${row.name} ${labels.runsTrend}`}
                      />
                    </>
                  )}
                </dd>
              </div>
              <div>
                <dt>{labels.columns.satisfaction}</dt>
                <dd>
                  <SatisfactionValue row={row} ctx={ctx} />
                </dd>
              </div>
              <div>
                <dt>{labels.topServed}</dt>
                <dd>
                  {row.topServed.length === 0
                    ? labels.noServed
                    : row.topServed
                        .slice(0, 3)
                        .map((served) => served.actorId)
                        .join(", ")}
                </dd>
              </div>
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
