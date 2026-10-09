import { Button, Popover } from "antd";

import type { EdgeCitation } from "../model/edgeCitation";
import { RelationHoverCard } from "./RelationHoverCard";

/**
 * Deliberately not imported from `GroundedAnswer.tsx` (which renders this
 * chip): `GroundedAnswer` already imports `EdgeCitationChip`, so importing
 * back would create a circular module dependency that dependency-cruiser's
 * `no-circular` rule rejects. The two EN/ZH strings are small enough to
 * duplicate here rather than hoist into a third shared module.
 */
const EDGE_CITATION_COPY = {
  en: (number: number) => `Open relation citation R${String(number)}`,
  zh: (number: number) => `打开关系引用 R${String(number)}`,
} as const;

export function EdgeCitationChip({
  number,
  citation,
  locale,
  onOpen,
}: {
  number: number;
  citation: EdgeCitation;
  locale: "en" | "zh";
  onOpen: (citationId: string, trigger: HTMLElement) => void;
}) {
  return (
    <Popover
      trigger={["hover", "focus"]}
      content={<RelationHoverCard citation={citation} locale={locale} />}
    >
      <Button
        type="text"
        size="small"
        data-kind="edge"
        aria-label={EDGE_CITATION_COPY[locale](number)}
        onClick={(event) => onOpen(citation.citationId, event.currentTarget)}
      >
        {`[R${String(number)}]`}
      </Button>
    </Popover>
  );
}
