import { useId, useMemo, useState } from "react";

import type { AgentTeamMemberRow } from "../../../../../features/knowledge/api/agentTeam";
import type { Bucket } from "./buckets";
import {
  ChartTooltip,
  axisTicks,
  useCrosshair,
  useMeasuredWidth,
} from "./charts";
import {
  EMPTY,
  formatCompact,
  formatCost,
  formatCount,
  formatDuration,
  formatShortDate,
  memberDisplayName,
} from "./format";
import type { CellContext } from "./memberCells";
import { RuntimeMark } from "./RuntimeBadge";
import {
  TEAM_METRICS,
  memberSeries,
  niceScale,
  rowTotal,
  splitTop,
  type TeamMetric,
} from "./series";

/** Categorical slots in their validated order; colour follows the Agent's
 * place in the period's top five by runs, so switching the metric or hiding
 * a series never repaints the others. */
export const SERIES_COLORS = [
  "var(--tap-series-1)",
  "var(--tap-series-2)",
  "var(--tap-series-3)",
  "var(--tap-series-4)",
  "var(--tap-series-5)",
];
export const OTHER_COLOR = "var(--tap-series-other)";
const TOP_COUNT = SERIES_COLORS.length;

interface Series {
  key: string;
  label: string;
  color: string;
  values: number[];
  total: number | null;
}

const PLOT_H = 216;
const GUTTER = 56;
const TOP = 12;
const BOTTOM = 26;
const GAP = 2;
const BAR_MAX = 24;

/** Rectangle with rounded top corners only (bars grow from the baseline). */
function topRounded(x: number, y: number, w: number, h: number, r: number) {
  const radius = Math.min(r, w / 2, h);
  return [
    `M ${x} ${y + h}`,
    `V ${y + radius}`,
    `Q ${x} ${y} ${x + radius} ${y}`,
    `H ${x + w - radius}`,
    `Q ${x + w} ${y} ${x + w} ${y + radius}`,
    `V ${y + h}`,
    "Z",
  ].join(" ");
}

export function metricFormatter(metric: TeamMetric, ctx: CellContext) {
  const units = ctx.copy.agentTeam.durationUnits;
  switch (metric) {
    case "runs":
    case "failures":
      return (value: number) => formatCount(value, ctx.locale);
    case "cost":
      return (value: number) => formatCost(String(value), ctx.locale);
    case "latency":
      return (value: number) => formatDuration(value, ctx.locale, units);
  }
}

/** Compact tick labels: 1.2K, US$0.5, 30 min. */
function tickFormatter(metric: TeamMetric, ctx: CellContext) {
  const units = ctx.copy.agentTeam.durationUnits;
  switch (metric) {
    case "runs":
    case "failures":
      return (value: number) => formatCompact(value, ctx.locale);
    case "cost":
      return (value: number) =>
        new Intl.NumberFormat(ctx.locale, {
          style: "currency",
          currency: "USD",
          maximumFractionDigits: value < 1 && value > 0 ? 2 : 0,
        }).format(value);
    case "latency":
      return (value: number) => {
        if (value === 0) return "0";
        if (value < 60_000)
          return `${formatCompact(value / 1000, ctx.locale)} ${units.s}`;
        if (value < 3_600_000)
          return `${formatCompact(value / 60_000, ctx.locale)} ${units.m}`;
        return `${formatCompact(value / 3_600_000, ctx.locale)} ${units.h}`;
      };
  }
}

export interface TrendPanelProps {
  /** Every member in the period, unfiltered. */
  members: AgentTeamMemberRow[] | undefined;
  axis: Bucket[];
  ctx: CellContext;
}

/** Runs (or failures, cost, latency) per bucket, stacked by Agent: the top
 * five by runs plus everyone else. */
export function TrendPanel({ members, axis, ctx }: TrendPanelProps) {
  const text = ctx.copy.agentTeam;
  const labels = text.trend;
  const [metric, setMetric] = useState<TeamMetric>("runs");
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());
  const switchId = useId();
  const unavailable = ctx.runtimeStatus === "unavailable";

  const series: Series[] = useMemo(() => {
    if (members === undefined) return [];
    const { top, rest } = splitTop(
      members.filter((row) => row.runs !== null),
      TOP_COUNT,
    );
    const list: Series[] = top.map((row, i) => ({
      key: row.agentKey,
      label: memberDisplayName(row, text),
      color: SERIES_COLORS[i],
      values: memberSeries(row, axis, metric),
      total: rowTotal(row, metric),
    }));
    if (rest.length > 0) {
      const values = axis.map(() => 0);
      let total = 0;
      for (const row of rest) {
        memberSeries(row, axis, metric).forEach((value, i) => {
          values[i] += value;
        });
        total += rowTotal(row, metric) ?? 0;
      }
      list.push({
        key: "__other",
        label: labels.others.replace("{count}", String(rest.length)),
        color: OTHER_COLOR,
        values,
        total,
      });
    }
    return list;
  }, [members, axis, metric, labels.others, text]);

  const format = metricFormatter(metric, ctx);
  const visible = series.filter((s) => !hidden.has(s.key));
  const totals = axis.map((_, i) =>
    visible.reduce((sum, s) => sum + s.values[i], 0),
  );
  const grand = series.reduce((sum, s) => sum + (s.total ?? 0), 0);
  const peakIndex = totals.reduce(
    (best, value, i) => (value > totals[best] ? i : best),
    0,
  );
  const toggle = (key: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <section
      className="tap-team-panel-card tap-team-mix"
      aria-labelledby={`${switchId}-title`}
    >
      <header className="tap-team-panel-card-head">
        <div>
          <h3 id={`${switchId}-title`}>{text.team.trendHeading}</h3>
          <p>{labels.byAgent}</p>
        </div>
        <div
          role="group"
          aria-label={labels.metricLabel}
          className="tap-team-segmented tap-team-segmented--quiet"
        >
          {TEAM_METRICS.map((id) => (
            <button
              key={id}
              type="button"
              aria-pressed={metric === id}
              onClick={() => setMetric(id)}
            >
              {labels.metrics[id]}
            </button>
          ))}
        </div>
      </header>
      {unavailable ? (
        <p className="tap-team-chart-empty">
          {EMPTY}
          <RuntimeMark status="unavailable" copy={ctx.copy} />
        </p>
      ) : members === undefined ? (
        <div className="tap-team-chart-skeleton" aria-hidden="true" />
      ) : (
        <>
          <div className="tap-team-mix-summary">
            <p className="tap-team-mix-total">
              <strong>{format(grand)}</strong>
              <span>{labels.periodTotal}</span>
            </p>
            {grand > 0 && axis.length > 0 ? (
              <p className="tap-team-mix-peak">
                {labels.peak
                  .replace("{value}", format(totals[peakIndex]))
                  .replace("{date}", axis[peakIndex].label)}
              </p>
            ) : null}
          </div>
          {series.length === 0 ? null : (
            <ul
              className="tap-team-legend-chips"
              aria-label={labels.legendLabel}
            >
              {series.map((s) => (
                <li key={s.key}>
                  <button
                    type="button"
                    aria-pressed={!hidden.has(s.key)}
                    aria-label={`${s.label} ${s.total === null ? EMPTY : format(s.total)}`}
                    onClick={() => toggle(s.key)}
                    title={s.label}
                  >
                    <i
                      className="tap-chart-swatch"
                      style={{ background: s.color }}
                      aria-hidden="true"
                    />
                    <span className="tap-team-legend-name">{s.label}</span>
                    <span className="tap-team-legend-value">
                      {s.total === null ? EMPTY : format(s.total)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <StackedBars
            axis={axis}
            series={series}
            visible={visible}
            totals={totals}
            metric={metric}
            ctx={ctx}
            empty={grand === 0 && totals.every((value) => value === 0)}
          />
        </>
      )}
    </section>
  );
}

function StackedBars({
  axis,
  series,
  visible,
  totals,
  metric,
  ctx,
  empty,
}: {
  axis: Bucket[];
  series: Series[];
  visible: Series[];
  totals: number[];
  metric: TeamMetric;
  ctx: CellContext;
  empty: boolean;
}) {
  const text = ctx.copy.agentTeam;
  const gradientPrefix = `mix-${useId().replace(/:/g, "")}`;
  const [boxRef, width] = useMeasuredWidth<HTMLDivElement>(640);
  const length = axis.length;
  const { hover, setHover, handlers } = useCrosshair(length);
  const format = metricFormatter(metric, ctx);
  const tick = tickFormatter(metric, ctx);
  const scale = niceScale(
    Math.max(...totals, 0),
    metric === "runs" || metric === "failures",
  );
  const chartH = PLOT_H + TOP + BOTTOM;
  const plotW = Math.max(width - GUTTER - 4, 40);
  const slot = length > 0 ? plotW / length : plotW;
  const barW = Math.max(Math.min(BAR_MAX, slot * 0.62), 2);
  const xCenter = (i: number) => GUTTER + slot * (i + 0.5);
  const y = (value: number) => TOP + PLOT_H - (value / scale.top) * PLOT_H;
  const hoverBucket = hover === null ? undefined : axis[hover];
  const rows =
    hover === null
      ? []
      : series.map((s) => ({
          key: s.key,
          label: s.label,
          value: format(s.values[hover]),
          color: s.color,
          muted: !visible.includes(s),
        }));
  return (
    <div className="tap-team-mix-plot" ref={boxRef}>
      <svg
        role="group"
        aria-roledescription="chart"
        aria-label={`${text.team.trendHeading}: ${text.trend.metrics[metric]}`}
        aria-description={text.team.chartHint}
        tabIndex={0}
        width="100%"
        height={chartH}
        viewBox={`0 0 ${width} ${chartH}`}
        className="tap-team-mix-svg"
        data-hovering={hover === null ? "false" : "true"}
        {...handlers}
      >
        <defs>
          {series.map((s, si) => (
            <linearGradient
              key={s.key}
              id={`${gradientPrefix}-${si}`}
              x1="0"
              x2="0"
              y1="0"
              y2="1"
            >
              <stop
                offset="0%"
                style={{ stopColor: s.color, stopOpacity: 1 }}
              />
              <stop
                offset="100%"
                style={{ stopColor: s.color, stopOpacity: 0.72 }}
              />
            </linearGradient>
          ))}
        </defs>
        {scale.ticks.map((value, i) => (
          <g key={value} className="tap-chart-grid">
            <line
              x1={GUTTER}
              x2={GUTTER + plotW}
              y1={y(value)}
              y2={y(value)}
              data-baseline={i === 0 ? "true" : undefined}
            />
            <text
              x={GUTTER - 10}
              y={y(value)}
              textAnchor="end"
              dominantBaseline="central"
              className="tap-chart-tick"
            >
              {tick(value)}
            </text>
          </g>
        ))}
        {hover === null ? null : (
          <rect
            className="tap-chart-band"
            x={GUTTER + slot * hover + 1}
            y={TOP - 4}
            width={Math.max(slot - 2, 1)}
            height={PLOT_H + 4}
            rx={6}
          />
        )}
        {axis.map((bucket, i) => {
          let base = TOP + PLOT_H;
          const segments = visible
            .map((s) => ({
              s,
              si: series.indexOf(s),
              h: (s.values[i] / scale.top) * PLOT_H,
            }))
            .filter((seg) => seg.h > 0);
          return (
            <g
              key={bucket.key}
              className="tap-chart-column"
              data-active={hover === i ? "true" : undefined}
            >
              {segments.map((seg, k) => {
                const top = base - seg.h;
                const last = k === segments.length - 1;
                // A 2px surface gap separates stacked segments.
                const h = Math.max(seg.h - (k === 0 ? 0 : GAP), 0.75);
                const shape = last
                  ? topRounded(xCenter(i) - barW / 2, top, barW, h, 4)
                  : `M ${xCenter(i) - barW / 2} ${top} h ${barW} v ${h} h ${-barW} Z`;
                base = top;
                return (
                  <path
                    key={seg.s.key}
                    d={shape}
                    fill={`url(#${gradientPrefix}-${seg.si})`}
                  />
                );
              })}
            </g>
          );
        })}
        {axisTicks(length, width < 520 ? 4 : 7).map((i) => (
          <text
            key={axis[i].key}
            x={xCenter(i)}
            y={TOP + PLOT_H + 18}
            textAnchor="middle"
            className="tap-chart-tick"
          >
            {formatShortDate(axis[i].key, ctx.locale)}
          </text>
        ))}
        {axis.map((bucket, i) => (
          <rect
            key={bucket.key}
            data-testid="mix-hit"
            x={GUTTER + slot * i}
            y={0}
            width={Math.max(slot, 1)}
            height={TOP + PLOT_H}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
          />
        ))}
      </svg>
      {empty ? (
        <p className="tap-team-chart-overlay">{text.trend.empty}</p>
      ) : null}
      {hover === null || hoverBucket === undefined ? null : (
        <ChartTooltip
          title={hoverBucket.label}
          x={xCenter(hover)}
          width={width}
          rows={[
            ...rows,
            {
              key: "__total",
              label: text.trend.total,
              value: format(totals[hover]),
              strong: true,
            },
          ]}
        />
      )}
      <div className="tap-team-sr-only" aria-live="polite">
        {hoverBucket === undefined || hover === null
          ? null
          : `${hoverBucket.label} · ${rows
              .filter((r) => !r.muted)
              .map((r) => `${r.label}: ${r.value}`)
              .join("; ")} · ${text.trend.total}: ${format(totals[hover])}`}
      </div>
    </div>
  );
}
