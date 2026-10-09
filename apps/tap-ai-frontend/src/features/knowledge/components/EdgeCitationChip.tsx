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
  onOpen: (citationId: string, trigger: HTMLElement) => void;
}) {
  return (
    <Popover
      trigger={["hover", "focus"]}
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
