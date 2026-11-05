import {
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import { formatCount, formatShortDate } from "./format";
import { niceScale } from "./series";

// ---------------------------------------------------------------------------
// Shared chart plumbing
// ---------------------------------------------------------------------------

/** Width of a chart container in CSS pixels, so marks are drawn 1:1 (round
 * dots and crisp corners) instead of stretched. Falls back when unmeasured. */
export function useMeasuredWidth<T extends HTMLElement>(fallback: number) {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(fallback);
  useLayoutEffect(() => {
    const element = ref.current;
    if (element === null) return;
    const update = () => {
      const next = Math.floor(element.clientWidth);
      if (next > 0) setWidth(next);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

/** Pointer and keyboard position over a chart's buckets. Arrow keys move,
 * Home/End jump, Escape clears; focus lands on the latest bucket. */
export function useCrosshair(length: number) {
  const [hover, setHover] = useState<number | null>(null);
  const onKeyDown = (event: KeyboardEvent) => {
    if (length === 0) return;
    const current = hover ?? length - 1;
    if (event.key === "ArrowRight") setHover(Math.min(current + 1, length - 1));
    else if (event.key === "ArrowLeft") setHover(Math.max(current - 1, 0));
    else if (event.key === "Home") setHover(0);
    else if (event.key === "End") setHover(length - 1);
    else if (event.key === "Escape") setHover(null);
    else return;
    event.preventDefault();
  };
  return {
    hover: hover !== null && hover < length ? hover : null,
    setHover,
    handlers: {
      onKeyDown,
      onFocus: () =>
        setHover((prev) => prev ?? (length > 0 ? length - 1 : null)),
      onBlur: () => setHover(null),
      onMouseLeave: () => setHover(null),
    },
  };
}

export interface TooltipRow {
  key: string;
  label: string;
  value: string;
  color?: string;
  /** Dimmed row, e.g. a hidden series. */
  muted?: boolean;
  strong?: boolean;
}

/** Floating readout: the bucket on top, one row per series with a short line
 * key, values leading. Purely visual; pair it with a live region. */
export function ChartTooltip({
  title,
  rows,
  x,
  width,
  footer,
}: {
  title: string;
  rows: TooltipRow[];
  /** Anchor in px from the plot's left edge. */
  x: number;
  /** Plot width, to flip the card before it overflows. */
  width: number;
  footer?: ReactNode;
}) {
  const side = x > width * 0.6 ? "left" : "right";
  return (
    <div
      className="tap-chart-tooltip"
      data-side={side}
      style={{ left: x }}
      aria-hidden="true"
    >
      <p className="tap-chart-tooltip-title">{title}</p>
      <div className="tap-chart-tooltip-rows">
        {rows.map((row) => (
          <div
            key={row.key}
            className="tap-chart-tooltip-row"
            data-muted={row.muted ? "true" : undefined}
            data-strong={row.strong ? "true" : undefined}
          >
            {row.color === undefined ? (
              <i className="tap-chart-key" data-blank="true" />
            ) : (
              <i className="tap-chart-key" style={{ background: row.color }} />
            )}
            <span className="tap-chart-tooltip-label">{row.label}</span>
            <strong>{row.value}</strong>
          </div>
        ))}
      </div>
      {footer === undefined ? null : (
        <p className="tap-chart-tooltip-footer">{footer}</p>
      )}
    </div>
  );
}

/** Indices of up to `count` evenly spaced x-axis labels, always including
 * both ends. */
export function axisTicks(length: number, count = 5): number[] {
  if (length <= 0) return [];
  if (length <= count) return Array.from({ length }, (_, i) => i);
  const ticks = new Set<number>();
  for (let i = 0; i < count; i += 1)
    ticks.add(Math.round((i * (length - 1)) / (count - 1)));
  return [...ticks];
}

// ---------------------------------------------------------------------------
// Sparkline
// ---------------------------------------------------------------------------

export type SparkTone = "brand" | "slate" | "muted" | "good" | "bad";

const SPARK_STROKE: Record<SparkTone, string> = {
  brand: "var(--tap-brand)",
  slate: "#6a7f8c",
  muted: "var(--tap-muted)",
  good: "var(--tap-good)",
  bad: "var(--tap-bad)",
};

const SPARK_W = 120;

/** Trend at a glance: a 1.5px line over a gradient wash with an end dot.
 * Stretches to its box; the dot is HTML so it stays round. */
export function Sparkline({
  points,
  label,
  tone = "brand",
  height = 32,
}: {
  points: number[];
  label: string;
  tone?: SparkTone;
  height?: number;
}) {
  const gradientId = `spark-${useId().replace(/:/g, "")}`;
  const pad = 3;
  const max = Math.max(...points, 0);
  const min = Math.min(...points, 0);
  const span = max - min || 1;
  const step = points.length > 1 ? SPARK_W / (points.length - 1) : 0;
  const coords = points.map((value, index) => ({
    x: points.length > 1 ? index * step : SPARK_W / 2,
    y: height - pad - ((value - min) / span) * (height - pad * 2),
  }));
  const line = coords.map((c) => `${c.x},${c.y}`).join(" ");
  const last = coords.at(-1);
  const color = SPARK_STROKE[tone];
  return (
    <span className="tap-team-sparkline-box" style={{ height }}>
      <svg
        role="img"
        aria-label={label}
        className="tap-team-sparkline"
        viewBox={`0 0 ${SPARK_W} ${height}`}
        preserveAspectRatio="none"
        height={height}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" style={{ stopColor: color, stopOpacity: 0.22 }} />
            <stop offset="100%" style={{ stopColor: color, stopOpacity: 0 }} />
          </linearGradient>
        </defs>
        <line
          x1={0}
          x2={SPARK_W}
          y1={height - pad}
          y2={height - pad}
          stroke="var(--tap-grid)"
          vectorEffect="non-scaling-stroke"
        />
        {coords.length < 2 ? null : (
          <>
            <polygon
              fill={`url(#${gradientId})`}
              stroke="none"
              points={`${coords[0].x},${height - pad} ${line} ${last?.x},${height - pad}`}
            />
            <polyline
              fill="none"
              stroke={color}
              strokeWidth={1.5}
              strokeLinejoin="round"
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
              points={line}
            />
          </>
        )}
      </svg>
      {last === undefined ? null : (
        <span
          className="tap-team-sparkline-dot"
          aria-hidden="true"
          style={{
            left: `${(last.x / SPARK_W) * 100}%`,
            top: last.y,
            background: color,
          }}
        />
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Trend chart (lines over time; the first series carries the area)
// ---------------------------------------------------------------------------

export interface TrendSeries {
  label: string;
  /** `bucket` is the shared key; `label` (default: the key) is what the readout shows. */
  points: { bucket: string; label?: string; value: number }[];
}

/** Brand orange leads; the context series (failures) is a quiet slate line.
 * Identity never rests on colour alone: legend and readout carry the names. */
const LINE_COLORS = [
  "var(--tap-series-1)",
  "#6a7f8c",
  "var(--tap-series-2)",
  "var(--tap-series-4)",
];

const PLOT_H = 168;
const GUTTER = 48;
const TOP = 10;
const BOTTOM = 26;

export function TrendChart({
  series,
  unit,
  locale = "en",
  hint,
  formatValue,
  integer = true,
  height = PLOT_H,
}: {
  series: TrendSeries[];
  unit: string;
  locale?: string;
  /** Localized keyboard instructions, announced with the chart. */
  hint?: string;
  /** Formats values for ticks and readouts (default: a count with the unit). */
  formatValue?: (value: number) => string;
  integer?: boolean;
  height?: number;
}) {
  const gradientId = `trend-${useId().replace(/:/g, "")}`;
  const [boxRef, width] = useMeasuredWidth<HTMLDivElement>(520);
  const withUnit =
    formatValue ??
    ((value: number) =>
      unit === ""
        ? formatCount(value, locale)
        : `${formatCount(value, locale)} ${unit}`);
  const bare = formatValue ?? ((value: number) => formatCount(value, locale));
  // Series share one x-axis keyed by bucket, not by array position.
  const keys = [
    ...new Set(series.flatMap((s) => s.points.map((p) => p.bucket))),
  ].sort();
  const length = keys.length;
  const { hover, setHover, handlers } = useCrosshair(length);
  const all = series.flatMap((s) => s.points.map((p) => p.value));
  const scale = niceScale(Math.max(...all, 0), integer);
  const chartH = height + TOP + BOTTOM;
  const plotW = Math.max(width - GUTTER - 8, 40);
  const x = (index: number) =>
    GUTTER + (length > 1 ? (index * plotW) / (length - 1) : plotW / 2);
  const y = (value: number) => TOP + height - (value / scale.top) * height;
  const slot = length > 1 ? plotW / (length - 1) : plotW;
  const labelOf = (key: string) => {
    for (const s of series) {
      const point = s.points.find((p) => p.bucket === key);
      if (point !== undefined) return point.label ?? point.bucket;
    }
    return key;
  };
  const hoverKey = hover === null ? undefined : keys[hover];
  const readout = series.flatMap((s, si) => {
    const point = s.points.find((p) => p.bucket === hoverKey);
    return point === undefined
      ? []
      : [
          {
            label: s.label,
            color: LINE_COLORS[si % LINE_COLORS.length],
            bucket: point.label ?? point.bucket,
            value: point.value,
          },
        ];
  });
  const path = (s: TrendSeries) =>
    s.points
      .map((p) => {
        const index = keys.indexOf(p.bucket);
        return `${x(index)},${y(p.value)}`;
      })
      .join(" ");
  const first = series[0];
  return (
    <div className="tap-team-trend">
      <div className="tap-team-trend-plot" ref={boxRef}>
        <svg
          role="group"
          aria-roledescription="chart"
          aria-description={hint}
          tabIndex={0}
          aria-label={series.map((s) => s.label).join(", ")}
          width="100%"
          height={chartH}
          viewBox={`0 0 ${width} ${chartH}`}
          className="tap-team-trend-svg"
          {...handlers}
        >
          <defs>
            <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
              <stop
                offset="0%"
                style={{ stopColor: LINE_COLORS[0], stopOpacity: 0.24 }}
              />
              <stop
                offset="100%"
                style={{ stopColor: LINE_COLORS[0], stopOpacity: 0.01 }}
              />
            </linearGradient>
          </defs>
          {length === 0
            ? null
            : scale.ticks.map((tick, i) => (
                <g key={tick} className="tap-chart-grid">
                  <line
                    x1={GUTTER}
                    x2={GUTTER + plotW}
                    y1={y(tick)}
                    y2={y(tick)}
                    data-baseline={i === 0 ? "true" : undefined}
                  />
                  <text
                    x={GUTTER - 8}
                    y={y(tick)}
                    textAnchor="end"
                    dominantBaseline="central"
                    className="tap-chart-tick"
                  >
                    {i === scale.ticks.length - 1 ? withUnit(tick) : bare(tick)}
                  </text>
                </g>
              ))}
          {first === undefined || first.points.length < 2 ? null : (
            <polygon
              fill={`url(#${gradientId})`}
              points={[
                `${x(keys.indexOf(first.points[0].bucket))},${y(0)}`,
                path(first),
                `${x(keys.indexOf(first.points.at(-1)!.bucket))},${y(0)}`,
              ].join(" ")}
            />
          )}
          {series
            .map((s, si) => ({ s, si }))
            .reverse()
            .map(({ s, si }) =>
              s.points.length === 0 ? null : (
                <polyline
                  key={s.label}
                  fill="none"
                  stroke={LINE_COLORS[si % LINE_COLORS.length]}
                  strokeWidth={si === 0 ? 2 : 1.5}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  points={path(s)}
                />
              ),
            )}
          {hover === null ? null : (
            <line
              className="tap-chart-crosshair"
              x1={x(hover)}
              x2={x(hover)}
              y1={TOP - 4}
              y2={TOP + height}
            />
          )}
          {hover === null
            ? null
            : series.map((s, si) => {
                const point = s.points.find((p) => p.bucket === hoverKey);
                return point === undefined ? null : (
                  <circle
                    key={s.label}
                    className="tap-chart-dot"
                    cx={x(hover)}
                    cy={y(point.value)}
                    r={4}
                    fill={LINE_COLORS[si % LINE_COLORS.length]}
                  />
                );
              })}
          {axisTicks(length, width < 420 ? 3 : 5).map((i) => (
            <text
              key={i}
              x={x(i)}
              y={TOP + height + 18}
              className="tap-chart-tick"
              textAnchor={
                length > 1 && i === 0
                  ? "start"
                  : length > 1 && i === length - 1
                    ? "end"
                    : "middle"
              }
            >
              {formatShortDate(keys[i], locale)}
            </text>
          ))}
          {keys.map((key, i) => (
            <rect
              key={key}
              data-testid="trend-hit"
              x={x(i) - slot / 2}
              y={0}
              width={Math.max(slot, 1)}
              height={TOP + height}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
            />
          ))}
        </svg>
        {hover === null || hoverKey === undefined ? null : (
          <ChartTooltip
            title={labelOf(hoverKey)}
            x={x(hover)}
            width={width}
            rows={readout.map((r) => ({
              key: r.label,
              label: r.label,
              value: withUnit(r.value),
              color: r.color,
            }))}
          />
        )}
        <div className="tap-team-sr-only" aria-live="polite">
          {readout.map((r) => (
            <span key={r.label}>
              {r.bucket} · {r.label}: {withUnit(r.value)}
            </span>
          ))}
        </div>
      </div>
      {series.length > 1 ? (
        <ul className="tap-team-trend-legend" aria-hidden="true">
          {series.map((s, si) => (
            <li key={s.label}>
              <span
                className="tap-chart-key"
                style={{ background: LINE_COLORS[si % LINE_COLORS.length] }}
              />
              {s.label}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
