import { useLayoutEffect, useRef, useState } from "react";

import type {
  AgentTeamMemberRow,
  AgentTeamSummaryView,
  TeamPeriod,
} from "../../../../../features/knowledge/api/agentTeam";
import type { Bucket } from "./buckets";
import { Sparkline, type SparkTone } from "./charts";
import { DeltaBadge, type Polarity } from "./delta";
import {
  EMPTY,
  formatCost,
  formatCount,
  formatDuration,
  formatPercent,
} from "./format";
import { RunValue, type CellContext } from "./memberCells";
import { RuntimeMark } from "./RuntimeBadge";
import {
  activeSeries,
  downsample,
  sumRows,
  teamSeries,
  type TeamMetric,
} from "./series";

type Summary = AgentTeamSummaryView["data"];

const MOTION_QUERY = "(prefers-reduced-motion: no-preference)";

function motionAllowed(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia(MOTION_QUERY).matches
  );
}

/** Eases a number toward its target: from zero on first show, then from the
 * last shown value. Jumps straight there when the reader prefers less
 * motion. */
export function useCountUp(target: number, duration = 700): number {
  const [shown, setShown] = useState(target);
  const from = useRef<number | null>(null);
  useLayoutEffect(() => {
    if (!motionAllowed()) {
      setShown(target);
      from.current = target;
      return;
    }
    const start = from.current ?? 0;
    from.current = target;
    if (start === target) {
      setShown(target);
      return;
    }
    let frame = 0;
    const began = performance.now();
    const tick = (now: number) => {
      const t = Math.min((now - began) / duration, 1);
      const eased = 1 - (1 - t) ** 3;
      setShown(start + (target - start) * eased);
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    setShown(start);
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target, duration]);
  return shown;
}

function AnimatedValue({
  value,
  format,
}: {
  value: number;
  format: (value: number) => string;
}) {
  const shown = useCountUp(value);
  return <span className="tap-team-kpi-number">{format(shown)}</span>;
}

/** A run-derived number: animated when known, marked by the runtime status. */
function RunNumber({
  value,
  format,
  ctx,
}: {
  value: number | null;
  format: (value: number) => string;
  ctx: CellContext;
}) {
  if (value === null) return <RunValue value={null} ctx={ctx} />;
  return (
    <>
      <AnimatedValue value={value} format={format} />
      {ctx.runtimeStatus === "syncing" ? (
        <RuntimeMark status="syncing" copy={ctx.copy} />
      ) : null}
    </>
  );
}

interface Kpi {
  id: string;
  label: string;
  value: number | null;
  previous: number | null | undefined;
  format: (value: number) => string;
  polarity: Polarity;
  caption: string | null;
  spark: number[] | null;
  tone: SparkTone;
  suffix?: string;
}

export interface KpiStripProps {
  summary: Summary;
  /** The previous period's summary: undefined while loading, null when it
   * could not be loaded. */
  previous: Summary | null | undefined;
  /** Every member this period (unfiltered), for failures and sparklines. */
  members: AgentTeamMemberRow[] | undefined;
  previousMembers: AgentTeamMemberRow[] | undefined;
  previousPeriod: TeamPeriod | undefined;
  axis: Bucket[];
  ctx: CellContext;
}

const SPARK_POINTS = 14;

export function KpiStrip({
  summary,
  previous,
  members,
  previousMembers,
  previousPeriod,
  axis,
  ctx,
}: KpiStripProps) {
  const text = ctx.copy.agentTeam;
  const locale = ctx.locale;
  const unavailable = ctx.runtimeStatus === "unavailable";
  const count = (value: number) => formatCount(value, locale);
  const cost = (value: number) => formatCost(String(value), locale);
  const duration = (value: number) =>
    formatDuration(value, locale, text.durationUnits);
  const num = (value: string | null | undefined) =>
    value === null || value === undefined ? null : Number(value);
  const spark = (metric: TeamMetric) =>
    members === undefined || unavailable
      ? null
      : downsample(teamSeries(members, axis, metric), SPARK_POINTS);
  const failures =
    members === undefined || unavailable ? null : sumRows(members, "failures");
  const runs = summary.runs;
  const perRun = (total: number | null) =>
    total === null || runs === null || runs === 0 ? null : total / runs;
  const days = axis.length > 0 ? dayCount(axis) : 0;
  const latency = summary.latencyTotalMs;
  const costValue = num(summary.costUsd);
  const caption = (template: string, value: string | null) =>
    value === null ? null : template.replace("{value}", value);

  const kpis: Kpi[] = [
    {
      id: "runs",
      label: text.metrics.runs,
      value: runs,
      previous: previous === undefined ? undefined : (previous?.runs ?? null),
      format: count,
      polarity: "up-good",
      caption: caption(
        text.kpi.perDay,
        runs === null || days === 0 ? null : count(runs / days),
      ),
      spark: spark("runs"),
      tone: "brand",
    },
    {
      id: "failures",
      label: text.metrics.failures,
      value: failures,
      previous:
        previous === undefined || previousMembers === undefined
          ? undefined
          : previous === null
            ? null
            : sumRows(previousMembers, "failures"),
      format: count,
      polarity: "up-bad",
      caption: caption(
        text.kpi.failureRate,
        failures === null || runs === null || runs === 0
          ? null
          : formatPercent(failures / runs, locale),
      ),
      spark: spark("failures"),
      tone: "slate",
    },
    {
      id: "cost",
      label: text.metrics.cost,
      value: costValue,
      previous:
        previous === undefined ? undefined : num(previous?.costUsd ?? null),
      format: cost,
      polarity: "up-bad",
      caption: caption(
        text.kpi.perRun,
        perRun(costValue) === null ? null : cost(perRun(costValue)!),
      ),
      spark: spark("cost"),
      tone: "slate",
    },
    {
      id: "latency",
      label: text.metrics.latency,
      value: latency,
      previous:
        previous === undefined ? undefined : (previous?.latencyTotalMs ?? null),
      format: duration,
      polarity: "neutral",
      caption: caption(
        text.kpi.perRun,
        perRun(latency) === null ? null : duration(perRun(latency)!),
      ),
      spark: spark("latency"),
      tone: "slate",
    },
    {
      id: "active",
      label: text.metrics.active,
      value: summary.active,
      previous: previous === undefined ? undefined : (previous?.active ?? null),
      format: count,
      polarity: "up-good",
      suffix: ` / ${count(summary.agentTotal)}`,
      caption: caption(
        text.kpi.idle,
        summary.idle === null ? null : count(summary.idle),
      ),
      spark:
        members === undefined || unavailable
          ? null
          : downsample(activeSeries(members, axis), SPARK_POINTS, "max"),
      tone: "brand",
    },
  ];

  const baseline = (kpi: Kpi) =>
    previousPeriod === undefined
      ? undefined
      : text.kpi.previous
          .replace("{from}", previousPeriod.from)
          .replace("{to}", previousPeriod.to)
          .replace(
            "{value}",
            kpi.previous === null || kpi.previous === undefined
              ? EMPTY
              : kpi.format(kpi.previous),
          );

  return (
    <dl className="tap-team-kpis">
      {kpis.map((kpi) => {
        const comparable =
          kpi.previous !== undefined && !unavailable && kpi.value !== null;
        return (
          <div key={kpi.id} className="tap-team-kpi" data-kpi={kpi.id}>
            <dt>{kpi.label}</dt>
            <dd>
              <span className="tap-team-kpi-value">
                <RunNumber value={kpi.value} format={kpi.format} ctx={ctx} />
                {kpi.suffix === undefined || kpi.value === null ? null : (
                  <span className="tap-team-kpi-suffix">{kpi.suffix}</span>
                )}
              </span>
              {kpi.caption === null ? null : (
                <span className="tap-team-kpi-caption">{kpi.caption}</span>
              )}
              <span className="tap-team-kpi-delta" title={baseline(kpi)}>
                {comparable ? (
                  <>
                    <DeltaBadge
                      current={kpi.value}
                      previous={kpi.previous}
                      polarity={kpi.polarity}
                      locale={locale}
                    />
                    <span>
                      {kpi.previous === null || kpi.previous === 0
                        ? text.kpi.noBaseline
                        : text.kpi.vsPrevious}
                    </span>
                  </>
                ) : kpi.previous === undefined &&
                  !unavailable &&
                  kpi.value !== null ? (
                  // The previous period is still loading.
                  <span className="tap-team-kpi-delta-pending" />
                ) : null}
              </span>
              <span className="tap-team-kpi-spark">
                {kpi.spark === null ? (
                  <span
                    className="tap-team-sparkline-empty"
                    aria-hidden="true"
                  />
                ) : (
                  <Sparkline
                    points={kpi.spark}
                    label={`${kpi.label} ${text.kpi.trendLabel}`}
                    tone={kpi.tone}
                    height={36}
                  />
                )}
              </span>
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

/** Days covered by the axis (its first bucket start to the period end). */
function dayCount(axis: Bucket[]): number {
  const first = axis[0];
  const last = axis.at(-1)!;
  const end = last.label.split(" – ").at(-1) ?? last.key;
  const start = first.label.split(" – ")[0] ?? first.key;
  const ms = Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`);
  return Number.isFinite(ms) ? Math.round(ms / 86_400_000) + 1 : axis.length;
}
