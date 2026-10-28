import { useId, useState, type KeyboardEvent } from "react";

import type {
  AgentTeamMemberRow,
  TeamPeriod,
} from "../../../../../features/knowledge/api/agentTeam";
import type { WorkspaceCopy } from "../../copy";
import { bucketAxis, type Granularity } from "./buckets";
import { EMPTY, formatCount, formatWeekday } from "./format";
import { RuntimeMark, type RuntimeStatus } from "./RuntimeBadge";
import { memberSeries } from "./series";

const DAY_MS = 86_400_000;

function parse(key: string): number {
  return Date.parse(`${key}T00:00:00Z`);
}

function iso(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

interface Cell {
  key: string;
  /** Inside the selected period (days padding a week are outside). */
  inPeriod: boolean;
  value: number;
  label: string;
}

export interface HeatScale {
  /** Quartiles (25th, 50th, 75th percentile) of the days that had runs. */
  quartiles: number[];
  max: number;
}

/** Four steps spread across the period's own range of active days, so a
 * quiet team still shows its busier days. 0 always means no runs. */
export function heatThresholds(values: number[]): HeatScale {
  const active = values.filter((value) => value > 0).sort((a, b) => a - b);
  if (active.length === 0) return { quartiles: [0, 0, 0], max: 0 };
  const at = (q: number) => {
    const position = q * (active.length - 1);
    const low = Math.floor(position);
    const high = Math.min(low + 1, active.length - 1);
    return active[low] + (active[high] - active[low]) * (position - low);
  };
  return { quartiles: [at(0.25), at(0.5), at(0.75)], max: active.at(-1)! };
}

export function heatLevel(value: number, scale: HeatScale): number {
  if (value <= 0) return 0;
  // The busiest day is always the darkest step.
  if (value >= scale.max) return 4;
  return 1 + scale.quartiles.filter((limit) => value > limit).length;
}

export interface ActivityHeatmapProps {
  members: AgentTeamMemberRow[] | undefined;
  period: TeamPeriod;
  granularity: Granularity;
  copy: WorkspaceCopy;
  locale: string;
  runtimeStatus: RuntimeStatus;
  /** Report layout: no stats column, tighter frame. */
  compact?: boolean;
}

/** Runs per day as a calendar (weeks as columns, Monday on top), GitHub
 * style. Longer periods (weekly or monthly buckets) get one cell per bucket. */
export function ActivityHeatmap({
  members,
  period,
  granularity,
  copy,
  locale,
  runtimeStatus,
  compact = false,
}: ActivityHeatmapProps) {
  const text = copy.agentTeam;
  const labels = text.heatmap;
  const id = useId();
  const [active, setActive] = useState<number | null>(null);
  const axis = bucketAxis(period, granularity);
  const totals = axis.map(() => 0);
  for (const row of members ?? [])
    memberSeries(row, axis, "runs").forEach((value, i) => {
      totals[i] += value;
    });
  const byKey = new Map(axis.map((bucket, i) => [bucket.key, totals[i]]));
  const daily = granularity === "day";

  // Calendar cells: whole weeks from the Monday on or before the start.
  const cells: Cell[] = [];
  if (daily && axis.length > 0) {
    const from = parse(period.from);
    const to = parse(period.to);
    const start = from - ((new Date(from).getUTCDay() + 6) % 7) * DAY_MS;
    const end = to + ((7 - new Date(to).getUTCDay()) % 7) * DAY_MS;
    for (let ms = start; ms <= end; ms += DAY_MS) {
      const key = iso(ms);
      const inPeriod = ms >= from && ms <= to;
      cells.push({
        key,
        inPeriod,
        value: inPeriod ? (byKey.get(key) ?? 0) : 0,
        label: `${key} ${formatWeekday(key, locale)}`,
      });
    }
  } else {
    axis.forEach((bucket, i) =>
      cells.push({
        key: bucket.key,
        inPeriod: true,
        value: totals[i],
        label: bucket.label,
      }),
    );
  }
  const inPeriod = cells.filter((cell) => cell.inPeriod);
  const thresholds = heatThresholds(inPeriod.map((cell) => cell.value));
  const total = inPeriod.reduce((sum, cell) => sum + cell.value, 0);
  const activeDays = inPeriod.filter((cell) => cell.value > 0).length;
  const busiest = inPeriod.reduce<Cell | null>(
    (best, cell) => (best === null || cell.value > best.value ? cell : best),
    null,
  );
  const weeks = daily ? Math.ceil(cells.length / 7) : 0;
  const columns = daily ? weeks : Math.min(cells.length, 13);
  const unit = text.profile.runsUnit;
  const runs = (value: number) =>
    unit === ""
      ? formatCount(value, locale)
      : `${formatCount(value, locale)} ${unit}`;

  const firstIn = cells.findIndex((cell) => cell.inPeriod);
  const lastIn =
    cells.length - 1 - [...cells].reverse().findIndex((c) => c.inPeriod);
  const move = (event: KeyboardEvent) => {
    if (inPeriod.length === 0) return;
    const current = active ?? lastIn;
    const step: Record<string, number> = daily
      ? { ArrowRight: 7, ArrowLeft: -7, ArrowDown: 1, ArrowUp: -1 }
      : { ArrowRight: 1, ArrowLeft: -1, ArrowDown: columns, ArrowUp: -columns };
    let next: number | null = null;
    if (event.key in step) {
      const candidate = current + step[event.key];
      next = Math.min(Math.max(candidate, firstIn), lastIn);
    } else if (event.key === "Home") next = firstIn;
    else if (event.key === "End") next = lastIn;
    else if (event.key === "Escape") {
      setActive(null);
      event.preventDefault();
      return;
    } else return;
    event.preventDefault();
    setActive(next);
  };

  const focused = active === null ? null : (cells[active] ?? null);
  // A column is labelled when its first day starts a new month (the first
  // column always names its month).
  const monthName = (key: string) =>
    new Intl.DateTimeFormat(locale, { timeZone: "UTC", month: "short" }).format(
      parse(key),
    );
  const months: string[] = [];
  if (daily) {
    let shown = "";
    for (let w = 0; w < weeks; w += 1) {
      const first = cells.slice(w * 7, w * 7 + 7).find((c) => c.inPeriod);
      const month = first === undefined ? "" : first.key.slice(0, 7);
      months.push(month !== "" && month !== shown ? monthName(first!.key) : "");
      if (month !== "") shown = month;
    }
  }
  const weekdayRows = daily
    ? cells
        .slice(0, 7)
        .map((cell, i) => (i % 2 === 0 ? formatWeekday(cell.key, locale) : ""))
    : [];

  const unavailable = runtimeStatus === "unavailable";
  return (
    <div
      className="tap-team-heatmap"
      data-compact={compact ? "true" : "false"}
      // A short calendar leaves room beside it for the stats.
      data-layout={daily && weeks <= 7 && !compact ? "side" : "stack"}
    >
      {unavailable ? (
        <p className="tap-team-chart-empty">
          {EMPTY}
          <RuntimeMark status="unavailable" copy={copy} />
        </p>
      ) : (
        <>
          <div className="tap-team-heatmap-body">
            <div
              className="tap-team-heatmap-frame"
              data-daily={daily ? "true" : "false"}
              style={{ ["--heat-columns" as string]: columns }}
            >
              {daily ? (
                <>
                  <span aria-hidden="true" />
                  <div className="tap-team-heat-months" aria-hidden="true">
                    {months.map((month, i) => (
                      <span key={i}>{month}</span>
                    ))}
                  </div>
                  <div className="tap-team-heat-weekdays" aria-hidden="true">
                    {weekdayRows.map((day, i) => (
                      <span key={i}>{day}</span>
                    ))}
                  </div>
                </>
              ) : null}
              <div
                role="group"
                aria-roledescription={labels.roleDescription}
                aria-label={labels.label}
                aria-description={labels.hint}
                tabIndex={0}
                className="tap-team-heat-grid"
                onKeyDown={move}
                onFocus={() => setActive((prev) => prev ?? lastIn)}
                onBlur={() => setActive(null)}
                onMouseLeave={() => setActive(null)}
              >
                {cells.map((cell, i) => (
                  <span
                    key={cell.key}
                    className="tap-team-heat-cell"
                    data-level={
                      cell.inPeriod
                        ? heatLevel(cell.value, thresholds)
                        : undefined
                    }
                    data-outside={cell.inPeriod ? undefined : "true"}
                    data-active={active === i ? "true" : undefined}
                    onMouseEnter={() =>
                      cell.inPeriod ? setActive(i) : setActive(null)
                    }
                  />
                ))}
                {focused === null || !focused.inPeriod ? null : (
                  <span
                    className="tap-team-heat-tip"
                    aria-hidden="true"
                    data-side={
                      (daily ? Math.floor(active! / 7) : active! % columns) >
                      columns / 2
                        ? "left"
                        : "right"
                    }
                    style={{
                      left: `${(((daily ? Math.floor(active! / 7) : active! % columns) + 0.5) / columns) * 100}%`,
                      top: `${((daily ? active! % 7 : Math.floor(active! / columns)) / (daily ? 7 : Math.ceil(cells.length / columns))) * 100}%`,
                    }}
                  >
                    <strong>{runs(focused.value)}</strong>
                    <span>{focused.label}</span>
                  </span>
                )}
              </div>
            </div>
            <div className="tap-team-heat-legend" aria-hidden="true">
              <span>{labels.less}</span>
              {[0, 1, 2, 3, 4].map((level) => (
                <i
                  key={level}
                  className="tap-team-heat-cell"
                  data-level={level}
                />
              ))}
              <span>{labels.more}</span>
            </div>
            <p
              className="tap-team-sr-only"
              aria-live="polite"
              id={`${id}-live`}
            >
              {focused === null || !focused.inPeriod
                ? ""
                : `${focused.label} · ${runs(focused.value)}`}
            </p>
          </div>
          {compact ? null : (
            <dl className="tap-team-heat-stats">
              <div>
                <dt>{labels.total}</dt>
                <dd>{formatCount(total, locale)}</dd>
              </div>
              <div>
                <dt>{daily ? labels.perDay : labels.perBucket}</dt>
                <dd>
                  {inPeriod.length === 0
                    ? EMPTY
                    : formatCount(total / inPeriod.length, locale)}
                </dd>
              </div>
              <div>
                <dt>{daily ? labels.activeDays : labels.activeBuckets}</dt>
                <dd>
                  {formatCount(activeDays, locale)}
                  <small> / {formatCount(inPeriod.length, locale)}</small>
                </dd>
              </div>
              <div>
                <dt>{daily ? labels.busiestDay : labels.busiestBucket}</dt>
                <dd>
                  {busiest === null || busiest.value === 0 ? (
                    EMPTY
                  ) : (
                    <>
                      {formatCount(busiest.value, locale)}
                      <small>{busiest.label}</small>
                    </>
                  )}
                </dd>
              </div>
            </dl>
          )}
        </>
      )}
    </div>
  );
}
