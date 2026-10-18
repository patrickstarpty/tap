/**
 * The rehype-sanitize allowlist shared by every renderer of an answer's
 * Markdown text — `GroundedAnswer` (the terminal, cited answer) and
 * `DraftAnswer` (the streaming, pre-terminal draft of the same text). Lives
 * under `shared/` rather than either feature so both
 * `features/knowledge/components/GroundedAnswer.tsx` and
 * `features/conversations/components/DraftAnswer.tsx` can import it without
 * a forbidden feature-to-feature dependency (`no-feature-to-feature`,
 * `dependency-cruiser.cjs`).
 *
 * The `code`/`data-claim-id` slot is reserved for exactly one empty
 * `<code data-claim-id="...">` element per citation/label badge, spliced
 * into the Markdown AST via mdast-to-hast's `data.hName`/`data.hProperties`
 * convention — `GroundedAnswer` keys it by claim id, `DraftAnswer` reuses
 * the same attribute slot keyed by an evidence label (`"S3"`, `"R1"`, …)
 * instead.
 */
export const ANSWER_TAGS = [
  "p",
  "h1",
  "h2",
  "h3",
  "h4",
  "ul",
  "ol",
  "li",
  "blockquote",
  "pre",
  "code",
  "strong",
  "em",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
  "hr",
] as const;

export const answerSchema = {
  tagNames: [...ANSWER_TAGS],
  attributes: { code: ["dataClaimId"] },
  protocols: {},
};
