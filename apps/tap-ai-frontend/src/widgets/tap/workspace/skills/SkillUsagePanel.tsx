import { Button } from "antd";

import type { TeamPeriod } from "../../../../features/knowledge/api/agentTeam";
import { useAgents } from "../../../../features/knowledge/api/aiAssets";
import {
  useSkillHubDetail,
  useSkillHubUsage,
  type SkillHubUsageView,
} from "../../../../features/knowledge/api/skillHub";
import type { WorkspaceCopy } from "../copy";
import { bucketAxis, fillBuckets } from "../agents/team/buckets";
import { TrendChart, type TrendSeries } from "../agents/team/charts";
import {
  formatCost,
  formatCount,
  formatDateTime,
  formatDuration,
  formatPercent,
  teamLocale,
} from "../agents/team/format";
import { RuntimeBadge } from "../agents/team/RuntimeBadge";
import "../agents/team/team.css";

export interface SkillUsagePanelProps {
  projectId: string;
  skillId: string;
  period: TeamPeriod;
  copy: WorkspaceCopy;
}

type Mode = "manual" | "pinned" | "auto";
const MODES: Mode[] = ["manual", "pinned", "auto"];

/** One line per activation mode over every bucket of the period: empty
 * buckets read zero and the first/last buckets are labelled with the dates
 * they actually cover. */
function modeSeries(
  view: SkillHubUsageView,
  labels: Record<Mode, string>,
): TrendSeries[] {
  const axis = bucketAxis(view.period, view.granularity);
  return MODES.map((mode) => ({
    label: labels[mode],
    points: fillBuckets(
      axis,
      view.data.points
        .filter((point) => point.mode === mode)
        .map((point) => ({
          bucketStart: point.bucketStart,
          value: point.runs,
        })),
    ),
  }));
}

interface Tile {
  key: string;
  label: string;
  value: string;
}

/** Period usage of one Skill: metric tiles, the per-mode trend and who uses
 * it. Run freshness comes from the detail response. */
export function SkillUsagePanel({
  projectId,
  skillId,
  period,
  copy,
}: SkillUsagePanelProps) {
  const text = copy.skillHub.usage;
  const locale = teamLocale(copy);
  const detailQuery = useSkillHubDetail(projectId, skillId, period);
  const usageQuery = useSkillHubUsage(projectId, skillId, period);
  const agents = useAgents(projectId).data ?? [];
  const view = detailQuery.data;

  if (view === undefined) {
    return detailQuery.isError ? (
      <div role="alert">
        <p>{text.loadFailed}</p>
        <Button onClick={() => void detailQuery.refetch()}>
          {copy.skillHub.retry}
        </Button>
      </div>
    ) : (
      <p role="status">{copy.skillHub.loading}</p>
    );
  }

  const { usage, topAgents, topCallers } = view.data;
  const timezone = view.timezone;
  const runsLabel = (count: number) => {
    const form = new Intl.PluralRules(locale).select(count);
    return (form === "one" ? text.runs.one : text.runs.other).replace(
      "{count}",
      formatCount(count, locale),
    );
  };
  const agentName = (key: string) => {
    if (key === "tapper_default") return text.tapperDefault;
    if (key === "unknown") return text.removedAgent;
    const agent =
      agents.find((item) => item.agentId === key && item.isLatest) ??
      agents.find((item) => item.agentId === key);
    return agent?.displayName ?? key;
  };

  const modeTiles: Tile[] = MODES.map((mode) => ({
    key: mode,
    label: text.modes[mode],
    value: formatCount(usage.byMode[mode], locale),
  }));
  const tiles: Tile[] = [
    {
      key: "users",
      label: text.users,
      value: formatCount(usage.users, locale),
    },
    {
      key: "satisfaction",
      label: text.satisfaction,
      value:
        usage.satisfactionSample === "insufficient"
          ? text.insufficientSample
          : formatPercent(usage.satisfaction, locale),
    },
    {
      key: "lastUsed",
      label: text.lastUsed,
      value:
        usage.lastUsedAt === null
          ? text.neverUsed
          : formatDateTime(usage.lastUsedAt, timezone, locale),
    },
    {
      key: "pinnedByAgents",
      label: text.pinnedByAgents,
      value: formatCount(usage.pinnedByAgents, locale),
    },
  ];
  const runtime = usage.runtime;
  const runtimeTiles: Tile[] =
    runtime === null
      ? []
      : [
          {
            key: "cost",
            label: text.cost,
            value: formatCost(runtime.costUsd, locale),
          },
          {
            key: "latency",
            label: text.latencyTotal,
            value: formatDuration(
              runtime.latencyTotalMs,
              locale,
              copy.agentTeam.durationUnits,
            ),
          },
          {
            key: "failures",
            label: text.failures,
            value: formatCount(runtime.failures, locale),
          },
        ];

  const usageView = usageQuery.data;
  const renderTiles = (items: Tile[]) =>
    items.map((tile) => (
      <div key={tile.key}>
        <dt>{tile.label}</dt>
        <dd>{tile.value}</dd>
      </div>
    ));
  const ranking = (
    label: string,
    rows: { key: string; runs: number }[],
    name: (key: string) => string,
  ) => (
    <section className="tap-skill-usage-ranking">
      <h3>{label}</h3>
      {rows.length === 0 ? (
        <p>{text.noRanking}</p>
      ) : (
        <ol aria-label={label}>
          {rows.map((row) => (
            <li key={row.key}>
              <span>{name(row.key)}</span>
              <strong>{runsLabel(row.runs)}</strong>
            </li>
          ))}
        </ol>
      )}
    </section>
  );

  return (
    <div className="tap-skill-usage">
      <RuntimeBadge
        status={view.runtimeStatus}
        computedAt={view.computedAt}
        timezone={timezone}
        copy={copy}
      />
      <section className="tap-skill-hub-summary">
        <dl>{renderTiles(modeTiles)}</dl>
        <p className="tap-skill-usage-note">{copy.skillHub.activationsHint}</p>
      </section>
      <section className="tap-skill-hub-summary">
        <dl>
          {renderTiles(tiles)}
          {renderTiles(runtimeTiles)}
        </dl>
        {runtime === null ? (
          <p className="tap-skill-usage-note">{text.runtimeUnavailable}</p>
        ) : null}
      </section>
      <section className="tap-skill-usage-trend">
        <h3>{text.trendHeading}</h3>
        {usageQuery.isError ? (
          <p role="alert">{text.loadFailed}</p>
        ) : usageView === undefined ? (
          <p role="status">{copy.skillHub.loading}</p>
        ) : usageView.data.points.length === 0 ? (
          <p>{text.noUsage}</p>
        ) : (
          <TrendChart
            series={modeSeries(usageView, text.modes)}
            unit={text.trendUnit}
            locale={locale}
            hint={copy.agentTeam.team.chartHint}
            height={150}
          />
        )}
      </section>
      <div className="tap-skill-usage-rankings">
        {ranking(text.topAgents, topAgents, agentName)}
        {ranking(text.topCallers, topCallers, (key) => key)}
      </div>
    </div>
  );
}
