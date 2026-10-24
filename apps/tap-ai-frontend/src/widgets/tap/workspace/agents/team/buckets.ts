import type { TeamPeriod } from "../../../../../features/knowledge/api/agentTeam";

export type Granularity = "day" | "week" | "month";

export interface Bucket {
  /** The bucket's natural start date (the key the server reports). */
  key: string;
  /** Actual covered dates, clamped to the period (spec 2.1). */
  label: string;
}

const DAY_MS = 86_400_000;

function parse(value: string): number {
  return Date.parse(`${value}T00:00:00Z`);
}

function format(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

function bucketStart(ms: number, granularity: Granularity): number {
  if (granularity === "day") return ms;
  const date = new Date(ms);
  if (granularity === "month")
    return Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1);
  // ISO weeks start on Monday; getUTCDay() is 0 for Sunday.
  return ms - ((date.getUTCDay() + 6) % 7) * DAY_MS;
}

function nextStart(ms: number, granularity: Granularity): number {
  if (granularity === "day") return ms + DAY_MS;
  if (granularity === "week") return ms + 7 * DAY_MS;
  const date = new Date(ms);
  return Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1);
}

/**
 * Every bucket covering the period, including empty ones, so a chart's x-axis
 * is time rather than "the buckets that happen to have runs".
 */
export function bucketAxis(
  period: TeamPeriod,
  granularity: Granularity,
): Bucket[] {
  const from = parse(period.from);
  const to = parse(period.to);
  if (Number.isNaN(from) || Number.isNaN(to) || from > to) return [];
  const buckets: Bucket[] = [];
  for (
    let start = bucketStart(from, granularity);
    start <= to;
    start = nextStart(start, granularity)
  ) {
    const first = Math.max(start, from);
    const last = Math.min(nextStart(start, granularity) - DAY_MS, to);
    buckets.push({
      key: format(start),
      label:
        first === last ? format(first) : `${format(first)} – ${format(last)}`,
    });
  }
  return buckets;
}

/** One value per axis bucket; buckets without a point are zero. */
export function fillBuckets(
  axis: Bucket[],
  points: { bucketStart: string; value: number }[],
): { bucket: string; label: string; value: number }[] {
  const byKey = new Map<string, number>();
  for (const point of points)
    byKey.set(
      point.bucketStart,
      (byKey.get(point.bucketStart) ?? 0) + point.value,
    );
  return axis.map((bucket) => ({
    bucket: bucket.key,
    label: bucket.label,
    value: byKey.get(bucket.key) ?? 0,
  }));
}
