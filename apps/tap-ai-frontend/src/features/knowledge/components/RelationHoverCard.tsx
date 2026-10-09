import { Alert, Button, Skeleton } from "antd";

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
 * already generated against an earlier revision. Loading/error/retry mirror
 * `CitationViewer.tsx:158-260`.
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
          action={
            problem.kind === "retryable" ? (
              <Button size="small" onClick={() => void citationQuery.refetch()}>
                {text.retryCitation}
              </Button>
            ) : undefined
          }
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
