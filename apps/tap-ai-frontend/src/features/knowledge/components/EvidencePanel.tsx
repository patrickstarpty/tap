import { Button, Typography } from "antd";
import { useEffect, useRef } from "react";

import { useCitationQuery, useKnowledgeClient } from "../api/queries";
import { CITATION_EN, COPY } from "../copy";
import { relationText, type EdgeCitation } from "../model/edgeCitation";
import { MiniPathGraph } from "./MiniPathGraph";

const MAX_SNIPPET_LENGTH = 300;

function truncateSnippet(text: string): string {
  const points = Array.from(text);
  if (points.length <= MAX_SNIPPET_LENGTH) return text;
  return `${points.slice(0, MAX_SNIPPET_LENGTH).join("")}…`;
}

/**
 * Right-side panel opened from an `EdgeCitationChip`: a mini path graph of
 * every edge citation the current turn's answer used (not just the one
 * clicked), the same paths as an accessible text list, and the cited
 * chunk's supporting passage (fetched by `citationId`, mirroring
 * `CitationViewer.tsx:158-260`'s structure/focus/close conventions).
 */
export function EvidencePanel({
  active,
  turnEdgeCitations,
  locale = "zh",
  onClose,
  returnFocusTo,
  onViewInLibrary,
}: {
  active: { citation: EdgeCitation; id: string };
  turnEdgeCitations: readonly EdgeCitation[];
  locale?: "en" | "zh";
  onClose: () => void;
  returnFocusTo?: HTMLElement | null;
  onViewInLibrary: (edgeIds: string[], graphVersion: string) => void;
}) {
  const text = locale === "zh" ? COPY : CITATION_EN;
  const { projectId } = useKnowledgeClient();
  const citationQuery = useCitationQuery(projectId, active.id);
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    // Deferred a tick (same idiom as the close button's `returnFocusTo`
    // focus below): focusing synchronously inside this effect's commit —
    // which runs inside the same click/keydown handling that opened the
    // panel — triggers React's "flushSync was called from inside a
    // lifecycle method" warning.
    queueMicrotask(() => headingRef.current?.focus());
  }, [active.id]);

  const edges =
    turnEdgeCitations.length > 0 ? turnEdgeCitations : [active.citation];

  return (
    <section
      className="tapper-panel tapper-evidence-panel"
      aria-labelledby="evidence-heading"
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
        <Button
          onClick={() => {
            onClose();
            queueMicrotask(() => returnFocusTo?.focus());
          }}
          aria-label={text.closeEvidence}
        >
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
      <blockquote className="tapper-citation-quote">
        {!citationQuery.isFetching && citationQuery.data !== undefined
          ? truncateSnippet(citationQuery.data.quote)
          : null}
      </blockquote>
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
