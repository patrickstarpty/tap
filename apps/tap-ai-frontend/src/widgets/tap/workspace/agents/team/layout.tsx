import { Button } from "antd";
import type { ReactNode } from "react";

import type { WorkspaceCopy } from "../../copy";

/** Places a tab's toolbar (freshness, period) in the page heading row. The
 * page supplies it; standalone the toolbar renders as its own row. */
export type RenderHeader = (toolbar: ReactNode) => ReactNode;

export const defaultHeader: RenderHeader = (toolbar) => (
  <div className="tap-team-toolbar">{toolbar}</div>
);

/** Loading placeholder shaped like the page it stands in for. */
export function TeamSkeleton({
  copy,
  kpis = true,
  cards = 3,
}: {
  copy: WorkspaceCopy;
  kpis?: boolean;
  cards?: number;
}) {
  return (
    <div role="status" className="tap-team-skeleton">
      <span className="tap-team-sr-only">{copy.agentTeam.loading}</span>
      {kpis ? (
        <div className="tap-team-skeleton-kpis" aria-hidden="true">
          {Array.from({ length: 4 }, (_, i) => (
            <span key={i} className="tap-team-skeleton-block" />
          ))}
        </div>
      ) : null}
      <div className="tap-team-skeleton-cards" aria-hidden="true">
        {Array.from({ length: cards }, (_, i) => (
          <span key={i} className="tap-team-skeleton-card">
            <span className="tap-team-skeleton-line" data-w="40" />
            <span className="tap-team-skeleton-line" data-w="70" />
            <span className="tap-team-skeleton-line" data-w="100" />
            <span className="tap-team-skeleton-line" data-w="55" />
          </span>
        ))}
      </div>
    </div>
  );
}

/** Inline failure with a retry; the surrounding controls stay usable. */
export function LoadError({
  copy,
  onRetry,
}: {
  copy: WorkspaceCopy;
  onRetry: () => void;
}) {
  return (
    <div role="alert" className="tap-team-error">
      <p>{copy.agentTeam.loadFailed}</p>
      <Button size="small" autoInsertSpace={false} onClick={onRetry}>
        {copy.agentTeam.retry}
      </Button>
    </div>
  );
}

export function EmptyRoster({
  copy,
  onCreateAgent,
}: {
  copy: WorkspaceCopy;
  onCreateAgent: () => void;
}) {
  return (
    <div className="tap-team-empty-state">
      <svg
        aria-hidden="true"
        width="132"
        height="96"
        viewBox="0 0 132 96"
        fill="none"
      >
        <rect
          x="10"
          y="18"
          width="70"
          height="62"
          rx="10"
          fill="#fff"
          stroke="var(--tap-line-strong)"
        />
        <circle
          cx="30"
          cy="38"
          r="9"
          fill="var(--tap-accent-soft)"
          stroke="var(--tap-accent-line)"
        />
        <rect
          x="45"
          y="32"
          width="26"
          height="5"
          rx="2.5"
          fill="var(--tap-line)"
        />
        <rect
          x="45"
          y="41"
          width="18"
          height="4"
          rx="2"
          fill="var(--tap-soft)"
        />
        <path
          d="M20 66 L32 60 L42 63 L54 54 L70 58"
          stroke="var(--tap-accent-line)"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <rect
          x="52"
          y="8"
          width="70"
          height="62"
          rx="10"
          fill="#fff"
          stroke="var(--tap-accent-line)"
          strokeDasharray="4 4"
        />
        <circle cx="87" cy="39" r="12" fill="var(--tap-accent-soft)" />
        <path
          d="M87 33 V45 M81 39 H93"
          stroke="var(--tap-brand)"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </svg>
      <p>{copy.agentTeam.emptyState}</p>
      <Button type="primary" autoInsertSpace={false} onClick={onCreateAgent}>
        {copy.agentTeam.createAgent}
      </Button>
    </div>
  );
}
