import { useEffect, useId, useRef, useState } from "react";

export interface ChartSeries {
  name: string;
  color: string;
  values: readonly (number | null)[];
}
export function TestAnalyticsChart({
  title,
  note,
  dates,
  series,
  max,
  unit,
  hover,
  onHover,
  onSelect,
  selectLabel,
  minPlotWidth = 440,
}: {
  title: string;
  note: string;
  dates: readonly string[];
  series: readonly ChartSeries[];
  max: number;
  unit: string;
  hover: string | null;
  onHover: (date: string | null) => void;
  onSelect?: (date: string) => void;
  selectLabel?: (date: string) => string;
  minPlotWidth?: number;
}) {
  const id = useId();
  const chartRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(660);
  useEffect(() => {
    const element = chartRef.current;
    if (!element) return;
    const observer = new ResizeObserver(() =>
      setWidth(Math.max(minPlotWidth, element.clientWidth)),
    );
    setWidth(Math.max(minPlotWidth, element.clientWidth));
    observer.observe(element);
    return () => observer.disconnect();
  }, [minPlotWidth]);
  const axisMax = unit === "" ? Math.ceil(max / 3) * 3 : max;
  const tickCount = unit === "%" ? 4 : 3;
  const bottom = 167;
  const height = 132;
  const x = (index: number) =>
    44 + index * ((width - 86) / Math.max(dates.length - 1, 1));
  const y = (value: number) => bottom - (value / axisMax) * height;
  const labelCount = Math.max(2, Math.floor((width - 86) / 65));
  const hitWidth = Math.min(30, (width - 86) / Math.max(dates.length - 1, 1));
  const active = hover ? dates.indexOf(hover) : -1;
  return (
    <article className="ta-report-chart">
      <header>
        <h3>{title}</h3>
        <span>{note}</span>
      </header>
      <div className="ta-chart-scroll" ref={chartRef}>
        <svg viewBox={`0 0 ${width} 220`} aria-labelledby={id} role="group">
          <title id={id}>{title}</title>
          {Array.from({ length: tickCount + 1 }, (_, i) => i).map((i) => (
            <g key={i}>
              <line
                className="ta-chart-grid"
                x1="44"
                x2={width - 38}
                y1={bottom - (i * height) / tickCount}
                y2={bottom - (i * height) / tickCount}
              />
              <text
                x="34"
                y={bottom - (i * height) / tickCount + 4}
                textAnchor="end"
              >
                {Number(((axisMax * i) / tickCount).toFixed(1))}
                {unit}
              </text>
            </g>
          ))}
          {series.map((s) => {
            const points = s.values.map((v, i) =>
              v === null ? null : `${x(i)},${y(v)}`,
            );
            return (
              <g key={s.name}>
                {s.values.every((v) => v !== null) && (
                  <polygon
                    points={`${x(0)},${bottom} ${points.join(" ")} ${x(dates.length - 1)},${bottom}`}
                    fill={s.color}
                    opacity="0.09"
                  />
                )}
                {points.map((point, i) =>
                  point && i > 0 && points[i - 1] ? (
                    <line
                      key={i}
                      x1={x(i - 1)}
                      y1={y(s.values[i - 1]!)}
                      x2={x(i)}
                      y2={y(s.values[i]!)}
                      stroke={s.color}
                      strokeWidth="1.8"
                    />
                  ) : null,
                )}
                {points.map(
                  (p, i) =>
                    p && (
                      <circle
                        key={i}
                        cx={x(i)}
                        cy={y(s.values[i]!)}
                        r={dates.length === 1 ? 4 : 2}
                        fill={s.color}
                      />
                    ),
                )}
              </g>
            );
          })}
          {active >= 0 && (
            <line
              x1={x(active)}
              x2={x(active)}
              y1="24"
              y2={bottom}
              className="ta-chart-cursor"
            />
          )}
          {dates.map((date, i) => (
            <g key={date}>
              {((i % Math.ceil(dates.length / labelCount) === 0 &&
                (i < dates.length - 2 || dates.length <= 2)) ||
                i === dates.length - 1) && (
                <text x={x(i)} y="190" textAnchor="middle">
                  {date.slice(5)}
                </text>
              )}
              <rect
                className="ta-chart-hit"
                x={x(i) - hitWidth / 2}
                y="25"
                width={hitWidth}
                height="145"
                fill="transparent"
                role={onSelect ? "button" : "img"}
                tabIndex={0}
                aria-label={
                  onSelect
                    ? selectLabel?.(date)
                    : `${date} · ${series.map((s) => `${s.name}: ${s.values[i] === null ? "—" : s.values[i]!.toFixed(1)}${unit}`).join(" · ")}`
                }
                onMouseEnter={() => onHover(date)}
                onMouseLeave={() => onHover(null)}
                onFocus={() => onHover(date)}
                onBlur={() => onHover(null)}
                onClick={() => onSelect?.(date)}
                onKeyDown={(event) => {
                  if (onSelect && ["Enter", " "].includes(event.key)) {
                    event.preventDefault();
                    onSelect(date);
                  }
                }}
              >
                <title>{`${date} · ${series.map((s) => `${s.name}: ${s.values[i] === null ? "—" : s.values[i]!.toFixed(1)}${unit}`).join(" · ")}`}</title>
              </rect>
            </g>
          ))}
        </svg>
      </div>
      <div className="ta-chart-legend" aria-live="polite">
        {active >= 0 && <span>{dates[active]}</span>}
        {series.map((s) => (
          <span key={s.name}>
            <i style={{ background: s.color }} />
            {s.name}
            {active >= 0 && (
              <strong>
                {s.values[active] === null
                  ? "—"
                  : `${s.values[active]!.toFixed(1)}${unit}`}
              </strong>
            )}
          </span>
        ))}
      </div>
    </article>
  );
}
