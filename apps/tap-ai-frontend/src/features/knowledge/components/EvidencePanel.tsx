import { Alert, Button, Typography } from "antd";
import { useEffect, useRef } from "react";

import { useCitationQuery, useKnowledgeClient } from "../api/queries";
import { CITATION_EN, COPY, safeCitationProblem } from "../copy";
import {
  dedupeEdgeCitations,
  relationText,
  truncateSnippet,
  type EdgeCitation,
} from "../model/edgeCitation";
import type { HistoricalCitationQuery } from "./CitationViewer";
import { MiniPathGraph } from "./MiniPathGraph";

/**
 * Right-side panel opened from an `EdgeCitationChip`: a mini path graph of
 * every edge citation the current turn's answer used (not just the one
 * clicked), the same paths as an accessible text list, and the cited
 * chunk's supporting passage (mirroring `CitationViewer.tsx:158-260`'s
 * structure/focus/close/loading/error/retry conventions). `historicalQuery`
 * is the same hook instance `TapperWorkspace` already computes for
 * `CitationViewer` (keyed by the active citation's turn + id), reused here
 * because only one of the two panels is ever open at a time — it prefers
 * the turn's own historical answer over a since-republished source's
 * current content. `turnEdgeCitations` can repeat an edge across claims, so
 * every derived list (graph, path text, "View in Library" ids) is deduped
 * by `edge.edgeId` first.
 */
export function EvidencePanel({
  active,
  turnEdgeCitations,
  locale = "zh",
  historicalQuery,
  onClose,
  returnFocusTo,
  onViewInLibrary,
}: {
  active: { citation: EdgeCitation; id: string };
  turnEdgeCitations: readonly EdgeCitation[];
  locale?: "en" | "zh";
  historicalQuery?: HistoricalCitationQuery;
  onClose: () => void;
  returnFocusTo?: HTMLElement | null;
  onViewInLibrary: (edgeIds: string[], graphVersion: string) => void;
}) {
  const text = locale === "zh" ? COPY : CITATION_EN;
  const { projectId } = useKnowledgeClient();
  const currentCitationQuery = useCitationQuery(
    projectId,
    historicalQuery === undefined ? active.id : null,
  );
  const citationQuery = historicalQuery ?? currentCitationQuery;
  const problem = citationQuery.isError
    ? safeCitationProblem(citationQuery.error, locale)
    : null;
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    // Deferred a tick (same idiom as the close button's `returnFocusTo`
    // focus below): focusing synchronously inside this effect's commit —
    // which runs inside the same click/keydown handling that opened the
    // panel — triggers React's "flushSync was called from inside a
    // lifecycle method" warning.
    queueMicrotask(() => headingRef.current?.focus());
  }, [active.id]);

  const edges = dedupeEdgeCitations(
    turnEdgeCitations.length > 0 ? turnEdgeCitations : [active.citation],
  );

  function close() {
    onClose();
    queueMicrotask(() => returnFocusTo?.focus());
  }

  return (
    <section
      className="tapper-panel tapper-evidence-panel"
      aria-labelledby="evidence-heading"
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          close();
        }
      }}
    >
      <header className="tapper-panel-header">
        <Typography.Title
          level={3}
          id="evidence-heading"
          tabIndex={-1}
          ref={headingRef}
        >
          {text.evidenceTitle}
        </Typography.Title>
        <Button onClick={close} aria-label={text.closeEvidence}>
          {text.close}
        </Button>
      </header>
      <div role="region" aria-label={text.pathGraph}>
        <MiniPathGraph
          edges={edges}
          activeEdgeId={active.citation.edge.edgeId}
        />
      </div>
      <ol aria-label={text.pathAsText}>
        {edges.map((citation) => (
          <li key={citation.edge.edgeId}>{relationText(citation)}</li>
        ))}
      </ol>
      <Typography.Title level={4}>{text.supportingPassage}</Typography.Title>
      {citationQuery.isFetching ? (
        <div aria-live="polite">
          <span>{text.citationLoading}</span>
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
        <blockquote className="tapper-citation-quote">
          {citationQuery.data !== undefined
            ? truncateSnippet(citationQuery.data.quote)
            : null}
        </blockquote>
      ) : null}
      <Button
        onClick={() =>
          onViewInLibrary(
            edges.map((citation) => citation.edge.edgeId),
            active.citation.edge.graphVersion,
          )
        }
      >
        {text.viewInLibrary}
      </Button>
    </section>
  );
}
