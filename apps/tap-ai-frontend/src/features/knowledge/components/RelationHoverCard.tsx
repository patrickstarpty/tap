import { Alert, Skeleton } from "antd";

import { useCitationQuery, useKnowledgeClient } from "../api/queries";
import { CITATION_EN, COPY, safeCitationProblem } from "../copy";
import {
  relationText,
  truncateSnippet,
  type EdgeCitation,
} from "../model/edgeCitation";
import type { HistoricalCitationQuery } from "./CitationViewer";

/**
 * Popover content for `EdgeCitationChip`. The snippet is not carried on the
 * edge citation itself (`EdgeCitationView` has no evidence field — only
 * `edgeId`/`graphVersion`/`subject`/`object`/`relationType`/`relationLabel`),
 * so it is fetched by `citationId`, the same way `CitationViewer` fetches a
 * chunk citation's quote. When `historicalQuery` is supplied (the turn's own
 * historical answer, resolved by `TapperWorkspace` via
 * `useConversationCitations` and threaded down through `GroundedAnswer` and
 * `EdgeCitationChip` — `features/knowledge` cannot import
 * `features/conversations` directly), it is preferred over the
 * current-authority `useCitationQuery` fallback so a republished or deleted
 * source does not silently blank or error the snippet of an answer that was
 * already generated against an earlier revision. Loading/error mirror
 * `CitationViewer.tsx:158-260`, minus its retry button: an antd `Popover`
 * closes as soon as it loses focus, so a retry button inside this hover
 * card can never be tabbed to or otherwise reached by keyboard. Retrying a
 * failed edge citation snippet is only offered in `EvidencePanel`, which
 * stays open and focusable.
 */
export function RelationHoverCard({
  citation,
  locale,
  historicalQuery,
}: {
  citation: EdgeCitation;
  locale: "en" | "zh";
  historicalQuery?: HistoricalCitationQuery;
}) {
  const text = locale === "zh" ? COPY : CITATION_EN;
  const { projectId } = useKnowledgeClient();
  const currentCitationQuery = useCitationQuery(
    projectId,
    historicalQuery === undefined ? citation.citationId : null,
  );
  const citationQuery = historicalQuery ?? currentCitationQuery;
  const problem = citationQuery.isError
    ? safeCitationProblem(citationQuery.error, locale)
    : null;

  return (
    <div className="tapper-relation-hover">
      <p className="tapper-relation-hover-relation">{relationText(citation)}</p>
      {citationQuery.isFetching ? (
        <div aria-live="polite">
          <span>{text.citationLoading}</span>
          <Skeleton active title={false} paragraph={{ rows: 2 }} />
        </div>
      ) : null}
      {problem !== null ? (
        <Alert
          type={problem.kind === "stale" ? "warning" : "error"}
          showIcon
          title={problem.message}
        />
      ) : null}
      {!citationQuery.isFetching && problem === null ? (
        <p className="tapper-relation-hover-snippet">
          {citationQuery.data !== undefined
            ? truncateSnippet(citationQuery.data.quote)
            : null}
        </p>
      ) : null}
    </div>
  );
}
