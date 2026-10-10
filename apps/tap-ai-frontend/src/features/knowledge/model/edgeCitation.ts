import type { RetrievalAnswerResponse } from "../api/types";

export type RetrievalCitation = RetrievalAnswerResponse["citations"][number];

/**
 * The generated `RetrievalCitation` type carries `kind?: "chunk" | "edge"`
 * as a plain field on a single object type, not as a discriminated union of
 * two interfaces — so `Extract<RetrievalCitation, { kind: "edge" }>` would
 * evaluate to `never` (nothing in the single `RetrievalCitation` shape is
 * assignable to the literal `"edge"` constraint). This intersection instead
 * narrows `kind` to the literal and `edge` to non-null, matching exactly
 * what `isEdgeCitation` checks at runtime below.
 */
export type EdgeCitation = RetrievalCitation & {
  kind: "edge";
  edge: NonNullable<RetrievalCitation["edge"]>;
};

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

/**
 * `kind === "edge"` plus non-empty `edgeId`, `subject.label`,
 * `object.label` and `relationType` — a citation failing any of these is
 * treated as absent rather than rendered with blank/undefined text.
 */
export function isEdgeCitation(
  citation: RetrievalCitation,
): citation is EdgeCitation {
  if (citation.kind !== "edge") return false;
  const edge = citation.edge;
  if (typeof edge !== "object" || edge === null) return false;
  return (
    isNonEmptyString(edge.edgeId) &&
    isNonEmptyString(edge.subject?.label) &&
    isNonEmptyString(edge.object?.label) &&
    isNonEmptyString(edge.relationType)
  );
}

/** `"A —requires→ B"`, falling back to the raw `relationType` code only when
 * the backend did not supply a `relationLabel` (it is required by contract,
 * but defensively still checked here since this reads an already-persisted
 * historical citation). */
export function relationText(citation: EdgeCitation): string {
  const { subject, object, relationType, relationLabel } = citation.edge;
  return `${subject.label} —${relationLabel || relationType}→ ${object.label}`;
}

/** Shared by `RelationHoverCard` and `EvidencePanel` so the two never drift
 * apart on how much of a supporting passage is shown. Code-point aware
 * (`Array.from`), so a 300-character cut never splits a surrogate pair. */
export const MAX_SNIPPET_LENGTH = 300;

export function truncateSnippet(text: string): string {
  const points = Array.from(text);
  if (points.length <= MAX_SNIPPET_LENGTH) return text;
  return `${points.slice(0, MAX_SNIPPET_LENGTH).join("")}…`;
}

/**
 * `EvidencePanel` draws every edge citation of the turn, but a turn can cite
 * the same edge twice (once per claim paragraph) — deduped by `edge.edgeId`,
 * keeping the first occurrence, so the mini path graph, the text path list
 * and "View in Library" all agree on one entry per edge.
 */
export function dedupeEdgeCitations(
  edges: readonly EdgeCitation[],
): EdgeCitation[] {
  const byId = new Map<string, EdgeCitation>();
  for (const citation of edges) {
    if (!byId.has(citation.edge.edgeId)) {
      byId.set(citation.edge.edgeId, citation);
    }
  }
  return [...byId.values()];
}
