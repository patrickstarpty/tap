import { Button, Popover } from "antd";

import type { EdgeCitation } from "../model/edgeCitation";
import type { HistoricalCitationQuery } from "./CitationViewer";
import { RelationHoverCard } from "./RelationHoverCard";

export function EdgeCitationChip({
  number,
  citation,
  locale,
  ariaLabel,
  historicalQuery,
  onPreview,
  onOpen,
}: {
  number: number;
  citation: EdgeCitation;
  locale: "en" | "zh";
  /** Computed by `GroundedAnswer` (via `features/knowledge/copy.ts`'s
   * `edgeCitation(n)`), not here — this chip stays a presentational
   * component so the copy lives in exactly one place. */
  ariaLabel: string;
  historicalQuery?: HistoricalCitationQuery;
  /** Reports the citation id the first time the popover opens (hover or
   * focus), so the caller can lazily enable its snippet query instead of
   * fetching every edge citation's snippet as soon as the turn renders. */
  onPreview?: (citationId: string) => void;
  onOpen: (citationId: string, trigger: HTMLElement) => void;
}) {
  return (
    <Popover
      trigger={["hover", "focus"]}
      onOpenChange={(open) => {
        if (open) onPreview?.(citation.citationId);
      }}
      content={
        <RelationHoverCard
          citation={citation}
          locale={locale}
          historicalQuery={historicalQuery}
        />
      }
    >
      <Button
        type="text"
        size="small"
        data-kind="edge"
        aria-label={ariaLabel}
        onClick={(event) => onOpen(citation.citationId, event.currentTarget)}
      >
        {`[R${String(number)}]`}
      </Button>
    </Popover>
  );
}
