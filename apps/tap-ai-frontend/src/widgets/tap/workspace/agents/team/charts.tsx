import { useState, type KeyboardEvent } from "react";

import { formatCount } from "./format";

const WIDTH = 120;
const HEIGHT = 28;

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

export function Sparkline({
  points,
  label,
}: {
  points: number[];
  label: string;
}) {
  const coords = scale(points, WIDTH, HEIGHT, 2);
  return (
    <svg
      role="img"
      aria-label={label}
      className="tap-team-sparkline"
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      width={WIDTH}
      height={HEIGHT}
    >
      {coords.length === 0 ? null : coords.length === 1 ? (
        <circle
          cx={coords[0].x}
          cy={coords[0].y}
          r={2}
          fill="var(--tap-brand)"
        />
      ) : (
        <polyline
          fill="none"
          stroke="var(--tap-brand)"
          strokeWidth={1.5}
          strokeLinejoin="round"
          points={coords.map((c) => `${c.x},${c.y}`).join(" ")}
        />
      )}
    </svg>
  );
}

export interface TrendSeries {
  label: string;
  /** `bucket` is the shared key; `label` (default: the key) is what the readout shows. */
  points: { bucket: string; label?: string; value: number }[];
}

const SERIES_COLORS = [
  "var(--tap-brand)",
  "var(--tap-ink)",
  "var(--tap-accent)",
  "var(--tap-muted)",
];
const CHART_W = 480;
const CHART_H = 160;
const PAD = 12;

export function TrendChart({
  series,
  unit,
  locale = "en",
}: {
  series: TrendSeries[];
  unit: string;
  locale?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  // Series share one x-axis keyed by bucket, not by array position.
  const keys = [
    ...new Set(series.flatMap((s) => s.points.map((p) => p.bucket))),
  ].sort();
  const indexOf = new Map(keys.map((key, index) => [key, index]));
  const length = keys.length;
  const all = series.flatMap((s) => s.points.map((p) => p.value));
  const max = Math.max(...all, 0);
  const span = max || 1;
  const x = (index: number) =>
    length > 1
      ? PAD + (index * (CHART_W - PAD * 2)) / (length - 1)
      : CHART_W / 2;
  const y = (value: number) =>
    CHART_H - PAD - (value / span) * (CHART_H - PAD * 2);
  const slot = length > 0 ? (CHART_W - PAD * 2) / Math.max(length - 1, 1) : 0;
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
  const readout = series.flatMap((s) => {
    const point = s.points.find((p) => p.bucket === hoverKey);
    return point === undefined
      ? []
      : [
          {
            label: s.label,
            bucket: point.label ?? point.bucket,
            value: point.value,
          },
        ];
  });
  return (
    <div className="tap-team-trend">
      <svg
        role="img"
        tabIndex={0}
        aria-label={series.map((s) => s.label).join(", ") + ` (${unit})`}
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
        <line
          x1={PAD}
          x2={CHART_W - PAD}
          y1={CHART_H - PAD}
          y2={CHART_H - PAD}
          stroke="var(--tap-line-strong)"
        />
        {series.map((s, si) =>
          s.points.length === 0 ? null : (
            <polyline
              key={s.label}
              fill="none"
              stroke={SERIES_COLORS[si % SERIES_COLORS.length]}
              strokeWidth={2}
              strokeLinejoin="round"
              vectorEffect="non-scaling-stroke"
              points={s.points
                .map((p) => `${x(indexOf.get(p.bucket) ?? 0)},${y(p.value)}`)
                .join(" ")}
            />
          ),
        )}
        {hover === null ? null : (
          <line
            x1={x(hover)}
            x2={x(hover)}
            y1={PAD}
            y2={CHART_H - PAD}
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
      {length === 0 ? null : (
        <div className="tap-team-trend-axis" aria-hidden="true">
          <span>{labelOf(keys[0])}</span>
          <span>{formatCount(max, locale)}</span>
          <span>{labelOf(keys[length - 1])}</span>
        </div>
      )}
      <div className="tap-team-trend-readout" aria-live="polite">
        {readout.map((r) => (
          <span key={r.label}>
            {r.bucket} · {r.label}: {formatCount(r.value, locale)} ({unit})
          </span>
        ))}
      </div>
    </div>
  );
}
