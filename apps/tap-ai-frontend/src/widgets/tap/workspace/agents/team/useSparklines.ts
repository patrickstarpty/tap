import { useMemo } from "react";

import {
  resolvePeriod,
  useTeamMembers,
} from "../../../../../features/knowledge/api/agentTeam";

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
    for (const row of spark.data?.data ?? [])
      map.set(
        row.agentKey,
        row.trend.map((point) => point.runs),
      );
    return map;
  }, [spark.data]);
  return { query: spark, byAgentKey };
}

const NO_FILTERS = {};
