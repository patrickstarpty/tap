import type { WorkspaceCopy } from "../../copy";
import { formatTime, teamLocale } from "./format";

export type RuntimeStatus = "ok" | "syncing" | "unavailable";

export interface RuntimeBadgeProps {
  status: RuntimeStatus;
  computedAt: string;
  timezone: string;
  copy: WorkspaceCopy;
}

/** Run-data freshness: availability notice plus the "data updated at" line. */
export function RuntimeBadge({
  status,
  computedAt,
  timezone,
  copy,
}: RuntimeBadgeProps) {
  const text = copy.agentTeam;
  const locale = teamLocale(copy);
  return (
    <div className="tap-team-freshness">
      {status === "unavailable" ? (
        <p role="status">{text.runtimeUnavailable}</p>
      ) : status === "syncing" ? (
        <p role="status">{text.runtimeSyncing}</p>
      ) : (
        <p>{text.runtimeLag}</p>
      )}
      <p>
        {text.updatedAt
          .replace("{time}", formatTime(computedAt, timezone, locale))
          .replace("{timezone}", timezone)}
      </p>
    </div>
  );
}

/** Inline marker shown next to a run-derived number that has no value. */
export function RuntimeMark({
  status,
  copy,
}: {
  status: RuntimeStatus;
  copy: WorkspaceCopy;
}) {
  if (status === "ok") return null;
  return (
    <small className="tap-team-runtime-mark">
      {status === "syncing"
        ? copy.agentTeam.runtimeSyncing
        : copy.agentTeam.runtimeUnavailable}
    </small>
  );
}
