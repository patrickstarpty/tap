import { useState } from "react";

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
        aria-label={series.map((s) => s.label).join(", ") + ` (${unit})`}
        viewBox={`0 0 ${CHART_W} ${CHART_H}`}
        width="100%"
        onMouseLeave={() => setHover(null)}
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
