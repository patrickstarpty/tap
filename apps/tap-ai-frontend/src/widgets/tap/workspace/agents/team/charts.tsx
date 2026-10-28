import { useState, type KeyboardEvent } from "react";

import { formatCount } from "./format";

const SPARK_W = 120;
const SPARK_H = 32;

function scale(points: number[], width: number, height: number, pad: number) {
  const max = Math.max(...points, 0);
  const min = Math.min(...points, 0);
  const span = max - min || 1;
  const step = points.length > 1 ? (width - pad * 2) / (points.length - 1) : 0;
  return points.map((value, index) => ({
    x: points.length > 1 ? pad + index * step : width / 2,
    y: height - pad - ((value - min) / span) * (height - pad * 2),
  }));
}

/** Card-width run sparkline: a 1.5px brand line over an 8% wash. */
export function Sparkline({
  points,
  label,
}: {
  points: number[];
  label: string;
}) {
  const coords = scale(points, SPARK_W, SPARK_H, 2);
  const line = coords.map((c) => `${c.x},${c.y}`).join(" ");
  const last = coords.at(-1);
  return (
    <svg
      role="img"
      aria-label={label}
      className="tap-team-sparkline"
      viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
      preserveAspectRatio="none"
    >
      <line
        x1={0}
        x2={SPARK_W}
        y1={SPARK_H - 2}
        y2={SPARK_H - 2}
        stroke="var(--tap-line)"
        vectorEffect="non-scaling-stroke"
      />
      {coords.length === 0 ? null : coords.length === 1 ? (
        <circle
          cx={coords[0].x}
          cy={coords[0].y}
          r={2}
          fill="var(--tap-brand)"
        />
      ) : (
        <>
          <polyline
            fill="none"
            stroke="var(--tap-brand)"
            strokeWidth={1.5}
            strokeLinejoin="round"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
            points={line}
          />
          <polygon
            fill="var(--tap-brand)"
            fillOpacity={0.08}
            stroke="none"
            points={`${coords[0].x},${SPARK_H - 2} ${line} ${last?.x},${SPARK_H - 2}`}
          />
        </>
      )}
    </svg>
  );
}

export interface TrendSeries {
  label: string;
  /** `bucket` is the shared key; `label` (default: the key) is what the readout shows. */
  points: { bucket: string; label?: string; value: number }[];
}

/** Brand orange for the primary series, slate grey for the context series
 * (failures); colour never carries identity alone: legend + readout text. */
const SERIES_COLORS = [
  "var(--tap-brand)",
  "var(--tap-chart-secondary, #6a7f8c)",
  "var(--tap-accent)",
  "var(--tap-muted)",
];
const CHART_W = 480;
const CHART_H = 168;
const PAD_X = 6;
const PAD_TOP = 10;
const PAD_BOTTOM = 6;
const GRID_STEPS = 4;

export function TrendChart({
  series,
  unit,
  locale = "en",
  hint,
}: {
  series: TrendSeries[];
  unit: string;
  locale?: string;
  /** Localized keyboard instructions, announced with the chart. */
  hint?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const withUnit = (value: number) =>
    unit === ""
      ? formatCount(value, locale)
      : `${formatCount(value, locale)} ${unit}`;
  // Series share one x-axis keyed by bucket, not by array position.
  const keys = [
    ...new Set(series.flatMap((s) => s.points.map((p) => p.bucket))),
  ].sort();
  const indexOf = new Map(keys.map((key, index) => [key, index]));
  const length = keys.length;
  const all = series.flatMap((s) => s.points.map((p) => p.value));
  const max = Math.max(...all, 0);
  const span = max || 1;
  const plotH = CHART_H - PAD_TOP - PAD_BOTTOM;
  const x = (index: number) =>
    length > 1
      ? PAD_X + (index * (CHART_W - PAD_X * 2)) / (length - 1)
      : CHART_W / 2;
  const y = (value: number) => CHART_H - PAD_BOTTOM - (value / span) * plotH;
  const slot = length > 0 ? (CHART_W - PAD_X * 2) / Math.max(length - 1, 1) : 0;
  const hoverKey = hover === null ? undefined : keys[hover];
  const labelOf = (key: string) => {
    for (const s of series) {
      const point = s.points.find((p) => p.bucket === key);
      if (point !== undefined) return point.label ?? point.bucket;
    }
    return key;
  };
  // Arrow keys move the readout, so the chart is usable without a pointer.
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
  const readout = series.flatMap((s, si) => {
    const point = s.points.find((p) => p.bucket === hoverKey);
    return point === undefined
      ? []
      : [
          {
            label: s.label,
            color: SERIES_COLORS[si % SERIES_COLORS.length],
            bucket: point.label ?? point.bucket,
            value: point.value,
          },
        ];
  });
  const pct = (px: number) => `${(px / CHART_W) * 100}%`;
  const gridValues = Array.from(
    { length: GRID_STEPS + 1 },
    (_, i) => (max * (GRID_STEPS - i)) / GRID_STEPS,
  );
  const xTicks =
    length === 0
      ? []
      : length <= 2
        ? [...new Set([0, length - 1])]
        : [0, Math.floor((length - 1) / 2), length - 1];
  return (
    <div className="tap-team-trend">
      <div className="tap-team-trend-body">
        {length === 0 ? null : (
          <div className="tap-team-trend-yaxis" aria-hidden="true">
            {/* Top tick carries the unit; the rest are bare numbers. */}
            <div className="tap-team-trend-ymax">{withUnit(max)}</div>
            <span>{formatCount(max / 2, locale)}</span>
            <span>{formatCount(0, locale)}</span>
          </div>
        )}
        <div className="tap-team-trend-plot">
          <svg
            role="group"
            aria-roledescription="chart"
            aria-description={hint}
            tabIndex={0}
            aria-label={series.map((s) => s.label).join(", ")}
            viewBox={`0 0 ${CHART_W} ${CHART_H}`}
            width="100%"
            height={CHART_H}
            preserveAspectRatio="none"
            className="tap-team-trend-svg"
            onMouseLeave={() => setHover(null)}
            onKeyDown={onKeyDown}
            onFocus={() =>
              setHover((prev) => prev ?? (length > 0 ? length - 1 : null))
            }
            onBlur={() => setHover(null)}
          >
            {gridValues.map((value, i) => (
              <line
                key={i}
                x1={0}
                x2={CHART_W}
                y1={y(value)}
                y2={y(value)}
                stroke={
                  i === GRID_STEPS
                    ? "var(--tap-line-strong)"
                    : "var(--tap-line)"
                }
                vectorEffect="non-scaling-stroke"
              />
            ))}
            {series.length === 0 || series[0].points.length < 2 ? null : (
              <polygon
                fill={SERIES_COLORS[0]}
                fillOpacity={0.08}
                points={[
                  `${x(indexOf.get(series[0].points[0].bucket) ?? 0)},${y(0)}`,
                  ...series[0].points.map(
                    (p) => `${x(indexOf.get(p.bucket) ?? 0)},${y(p.value)}`,
                  ),
                  `${x(indexOf.get(series[0].points.at(-1)!.bucket) ?? 0)},${y(0)}`,
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
                    stroke={SERIES_COLORS[si % SERIES_COLORS.length]}
                    strokeWidth={1.5}
                    strokeLinejoin="round"
                    strokeLinecap="round"
                    vectorEffect="non-scaling-stroke"
                    points={s.points
                      .map(
                        (p) => `${x(indexOf.get(p.bucket) ?? 0)},${y(p.value)}`,
                      )
                      .join(" ")}
                  />
                ),
              )}
            {hover === null ? null : (
              <line
                x1={x(hover)}
                x2={x(hover)}
                y1={PAD_TOP / 2}
                y2={CHART_H - PAD_BOTTOM}
                stroke="var(--tap-line-strong)"
                vectorEffect="non-scaling-stroke"
              />
            )}
            {Array.from({ length }, (_, i) => (
              <rect
                key={i}
                data-testid="trend-hit"
                x={x(i) - Math.max(slot, 1) / 2}
                y={0}
                width={Math.max(slot, 1)}
                height={CHART_H}
                fill="transparent"
                onMouseEnter={() => setHover(i)}
              />
            ))}
          </svg>
          {/* HTML dots stay round however the plot stretches. */}
          {hover === null
            ? null
            : series.map((s, si) => {
                const point = s.points.find((p) => p.bucket === hoverKey);
                return point === undefined ? null : (
                  <span
                    key={s.label}
                    className="tap-team-trend-dot"
                    aria-hidden="true"
                    style={{
                      left: pct(x(hover)),
                      top: y(point.value),
                      background: SERIES_COLORS[si % SERIES_COLORS.length],
                    }}
                  />
                );
              })}
          <div
            className="tap-team-trend-readout"
            aria-live="polite"
            data-visible={readout.length > 0 ? "true" : "false"}
            data-side={
              hover !== null && hover > (length - 1) / 2 ? "left" : "right"
            }
            style={hover === null ? undefined : { left: pct(x(hover)) }}
          >
            {readout.map((r) => (
              <span key={r.label}>
                <i
                  className="tap-team-trend-swatch"
                  style={{ background: r.color }}
                  aria-hidden="true"
                />
                {r.bucket} · {r.label}: {withUnit(r.value)}
              </span>
            ))}
          </div>
          {length === 0 ? null : (
            <div className="tap-team-trend-axis" aria-hidden="true">
              {xTicks.map((i) => (
                <span
                  key={i}
                  style={{ left: pct(x(i)) }}
                  data-edge={
                    i === 0 ? "start" : i === length - 1 ? "end" : "mid"
                  }
                >
                  {labelOf(keys[i])}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
      {series.length > 1 ? (
        <ul className="tap-team-trend-legend" aria-hidden="true">
          {series.map((s, si) => (
            <li key={s.label}>
              <span
                className="tap-team-trend-swatch"
                style={{ background: SERIES_COLORS[si % SERIES_COLORS.length] }}
              />
              {s.label}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
