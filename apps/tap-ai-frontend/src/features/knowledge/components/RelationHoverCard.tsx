import { Skeleton } from "antd";

import { useCitationQuery, useKnowledgeClient } from "../api/queries";
import { relationText, type EdgeCitation } from "../model/edgeCitation";

const MAX_SNIPPET_LENGTH = 300;

function truncateSnippet(text: string): string {
  const points = Array.from(text);
  if (points.length <= MAX_SNIPPET_LENGTH) return text;
  return `${points.slice(0, MAX_SNIPPET_LENGTH).join("")}…`;
}

/**
 * Popover content for `EdgeCitationChip`. The snippet is not carried on the
 * edge citation itself (`EdgeCitationView` has no evidence field — only
 * `edgeId`/`graphVersion`/`subject`/`object`/`relationType`/`relationLabel`),
 * so it is fetched the same way `CitationViewer` fetches a chunk citation's
 * quote: via `getCitation(citation.citationId)`. An edge citation still
 * carries its own `citationId`/`chunkId`, since it also cites the chunk the
 * relation was extracted from.
 */
export function RelationHoverCard({
  citation,
  locale,
}: {
  citation: EdgeCitation;
  locale: "en" | "zh";
}) {
  const { projectId } = useKnowledgeClient();
  const citationQuery = useCitationQuery(projectId, citation.citationId);
  return (
    <div className="tapper-relation-hover">
      <p className="tapper-relation-hover-relation">{relationText(citation)}</p>
      {citationQuery.isFetching ? (
        <Skeleton
          active
          title={false}
          paragraph={{ rows: 2 }}
          aria-label={locale === "zh" ? "正在加载" : "Loading"}
        />
      ) : (
        <p className="tapper-relation-hover-snippet">
          {citationQuery.data !== undefined
            ? truncateSnippet(citationQuery.data.quote)
            : null}
        </p>
      )}
    </div>
  );
}
