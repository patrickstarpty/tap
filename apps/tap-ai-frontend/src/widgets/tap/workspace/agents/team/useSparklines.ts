import { useMemo } from "react";

import {
  resolvePeriod,
  useTeamMembers,
} from "../../../../../features/knowledge/api/agentTeam";

import { bucketAxis, fillBuckets } from "./buckets";

/** Last-30-day run counts per agent, whatever period is selected. */
export function useSparklines(projectId: string) {
  const todayKey = new Date().toISOString().slice(0, 10);
  const period = useMemo(
    () => resolvePeriod("last30", new Date(`${todayKey}T00:00:00Z`)),
    [todayKey],
  );
  const spark = useTeamMembers(projectId, period, NO_FILTERS);
  const byAgentKey = useMemo(() => {
    const map = new Map<string, number[]>();
    // Daily axis over the whole window: days without runs are zeros, not gaps.
    const axis = bucketAxis(period, "day");
    for (const row of spark.data?.data ?? [])
      map.set(
        row.agentKey,
        // Unknown (runtime outage) is not zero: leave the sparkline empty.
        row.runs === null
          ? []
          : fillBuckets(
              axis,
              row.trend.map((point) => ({
                bucketStart: point.bucketStart,
                value: point.runs,
              })),
            ).map((point) => point.value),
      );
    return map;
  }, [spark.data, period]);
  return { query: spark, byAgentKey };
}

const NO_FILTERS = {};
