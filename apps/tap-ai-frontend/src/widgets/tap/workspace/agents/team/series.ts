import type { AgentTeamMemberRow } from "../../../../../features/knowledge/api/agentTeam";
import type { Bucket } from "./buckets";

/** The four run metrics a trend can show (spec 2.2 primary metrics). */
export type TeamMetric = "runs" | "failures" | "cost" | "latency";

export const TEAM_METRICS: TeamMetric[] = [
  "runs",
  "failures",
  "cost",
  "latency",
];

type TrendPoint = AgentTeamMemberRow["trend"][number];

export function pointValue(point: TrendPoint, metric: TeamMetric): number {
  switch (metric) {
    case "runs":
      return point.runs;
    case "failures":
      return point.failures;
    case "cost": {
      const value = Number(point.costUsd);
      return Number.isFinite(value) ? value : 0;
    }
    case "latency":
      return point.latencyTotalMs;
  }
}

/** A member's period total for a metric; null while run data is unknown. */
export function rowTotal(
  row: AgentTeamMemberRow,
  metric: TeamMetric,
): number | null {
  switch (metric) {
    case "runs":
      return row.runs;
    case "failures":
      return row.failures;
    case "cost": {
      if (row.costUsd === null) return null;
      const value = Number(row.costUsd);
      return Number.isFinite(value) ? value : null;
    }
    case "latency":
      return row.latencyTotalMs;
  }
}

/** One value per axis bucket for one member; missing buckets are zero. */
export function memberSeries(
  row: AgentTeamMemberRow,
  axis: Bucket[],
  metric: TeamMetric,
): number[] {
  const byKey = new Map<string, number>();
  for (const point of row.trend)
    byKey.set(
      point.bucketStart,
      (byKey.get(point.bucketStart) ?? 0) + pointValue(point, metric),
    );
  return axis.map((bucket) => byKey.get(bucket.key) ?? 0);
}

/** Team total per axis bucket. */
export function teamSeries(
  rows: AgentTeamMemberRow[],
  axis: Bucket[],
  metric: TeamMetric,
): number[] {
  const totals = axis.map(() => 0);
  for (const row of rows)
    memberSeries(row, axis, metric).forEach((value, i) => {
      totals[i] += value;
    });
  return totals;
}

/** Agents with at least one run, per axis bucket. */
export function activeSeries(
  rows: AgentTeamMemberRow[],
  axis: Bucket[],
): number[] {
  const counts = axis.map(() => 0);
  for (const row of rows)
    memberSeries(row, axis, "runs").forEach((value, i) => {
      if (value > 0) counts[i] += 1;
    });
  return counts;
}

/** Shrinks a series to at most `max` points by combining neighbours, so a
 * sparkline keeps its shape without a spike per day. */
export function downsample(
  values: number[],
  max: number,
  combine: "sum" | "max" = "sum",
): number[] {
  if (values.length <= max) return values;
  const size = Math.ceil(values.length / max);
  const out: number[] = [];
  for (let i = 0; i < values.length; i += size) {
    const chunk = values.slice(i, i + size);
    out.push(
      combine === "sum" ? chunk.reduce((a, b) => a + b, 0) : Math.max(...chunk),
    );
  }
  return out;
}

/** Sum of a metric over rows; null when every row is unknown. */
export function sumRows(
  rows: AgentTeamMemberRow[],
  metric: TeamMetric,
): number | null {
  let known = false;
  let total = 0;
  for (const row of rows) {
    const value = rowTotal(row, metric);
    if (value === null) continue;
    known = true;
    total += value;
  }
  return known ? total : null;
}

/** The busiest Agents by runs (ties by name), plus everyone else. */
export function splitTop(
  rows: AgentTeamMemberRow[],
  count: number,
): { top: AgentTeamMemberRow[]; rest: AgentTeamMemberRow[] } {
  const ranked = [...rows].sort(
    (a, b) => (b.runs ?? 0) - (a.runs ?? 0) || a.name.localeCompare(b.name),
  );
  return { top: ranked.slice(0, count), rest: ranked.slice(count) };
}

/** Leaderboard rank by runs: rows with equal runs share a rank; Agents
 * without runs are unranked. */
export function runRanks(rows: AgentTeamMemberRow[]): Map<string, number> {
  const ranks = new Map<string, number>();
  for (const row of rows) {
    // No runs, no place on the board.
    if (row.runs === null || row.runs === 0) continue;
    const runs = row.runs;
    ranks.set(
      row.agentKey,
      1 + rows.filter((other) => (other.runs ?? -1) > runs).length,
    );
  }
  return ranks;
}

export interface Scale {
  /** Top of the axis, a round number at or above the data maximum. */
  top: number;
  /** Tick values from 0 to `top`, evenly spaced. */
  ticks: number[];
}

/** Round axis ticks (1, 2, 2.5 or 5 times a power of ten). Counts never get
 * fractional steps. */
export function niceScale(max: number, integer: boolean, steps = 4): Scale {
  if (!(max > 0)) return { top: 1, ticks: [0, 1] };
  const raw = max / steps;
  const power = 10 ** Math.floor(Math.log10(raw));
  const multipliers =
    integer && power < 10 ? [1, 2, 5, 10] : [1, 2, 2.5, 5, 10];
  let step = power * multipliers[multipliers.length - 1];
  for (const m of multipliers) {
    if (power * m >= raw) {
      step = power * m;
      break;
    }
  }
  if (integer) step = Math.max(1, Math.round(step));
  const top = Number((Math.ceil(max / step - 1e-9) * step).toPrecision(12));
  const ticks: number[] = [];
  for (let value = 0; value <= top + step / 2; value += step)
    ticks.push(Number(value.toPrecision(12)));
  return { top, ticks };
}
