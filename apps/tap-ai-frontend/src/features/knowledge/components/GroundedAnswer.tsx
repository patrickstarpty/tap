import { Alert, Button } from "antd";
import { createContext, useContext, useMemo } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";

import type { RetrievalAnswerResponse } from "../api/types";
import { CITATION_EN, COPY } from "../copy";
import { isEdgeCitation } from "../model/edgeCitation";
import type { HistoricalCitationQuery } from "./CitationViewer";
import { EdgeCitationChip } from "./EdgeCitationChip";

const ANSWER_TAGS = [
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

const answerSchema = {
  tagNames: [...ANSWER_TAGS],
  // `data*` allows exactly `data-claim-id` (and no other attribute, on no
  // other tag) -- the citation badge is spliced into the Markdown AST as an
  // empty `<code data-claim-id="...">` element (round 3 of the LangGraph PR 3
  // review ruling, 2026-10-17); see `citationBadgeNode` below.
  attributes: { code: ["data*"] },
  protocols: {},
};

const ANSWER_COPY = {
  en: {
    heading: "Answer",
    formatError: "The answer format could not be verified. Ask again.",
    insufficient_evidence:
      "The selected sources do not contain enough evidence to answer this question.",
    conflicting_sources:
      "The selected sources conflict, so a reliable answer is unavailable.",
    revision_mismatch: "A source revision changed. Submit the question again.",
    out_of_scope:
      "This request is outside what Tapper can help with. Tapper answers questions from this project's authorized knowledge sources.",
    degraded:
      "Some retrieval capabilities are unavailable. Only verified evidence is shown.",
    citation: (number: number) => `Open source citation ${number}`,
  },
  zh: {
    heading: "回答",
    formatError: "回答格式无法核验，请重新提问。",
    insufficient_evidence: "所选来源中没有足够证据回答这个问题。",
    conflicting_sources: "所选来源之间存在冲突，暂时无法给出可靠回答。",
    revision_mismatch: "来源版本已经变化，请重新提交问题。",
    out_of_scope:
      "这个问题超出了 Tapper 的服务范围。Tapper 只基于当前项目已授权的知识来源回答问题。",
    degraded: "部分检索能力暂时受限，回答仍仅显示已核验依据。",
    citation: (number: number) => `打开来源引用 ${number}`,
  },
} as const;
type AnswerLocale = keyof typeof ANSWER_COPY;

type RetrievalClaim = RetrievalAnswerResponse["claims"][number];
type RetrievalCitation = RetrievalAnswerResponse["citations"][number];

interface ValidAnswerGraph {
  citationById: ReadonlyMap<string, RetrievalCitation>;
  citationNumberById: ReadonlyMap<string, number>;
  /** Edge citations number independently from chunk citations, each from 1,
   * in the same order (`source-order` or `shown-order`) as
   * `citationNumberById` — see `isEdgeCitation`. */
  edgeNumberById: ReadonlyMap<string, number>;
  claims: readonly RetrievalClaim[];
  points: readonly string[];
}

function FormatError({ locale }: { locale: AnswerLocale }) {
  return (
    <Alert type="error" showIcon title={ANSWER_COPY[locale].formatError} />
  );
}

function hasValidCitationIdentities(
  citations: readonly RetrievalCitation[],
): boolean {
  const citationIds = new Set<string>();
  for (const citation of citations) {
    if (
      typeof citation !== "object" ||
      citation === null ||
      typeof citation.citationId !== "string" ||
      citation.citationId.length === 0 ||
      citationIds.has(citation.citationId)
    ) {
      return false;
    }
    citationIds.add(citation.citationId);
  }
  return true;
}

function validateAnswerGraph(
  response: RetrievalAnswerResponse,
  numbering: "source-order" | "shown-order",
): ValidAnswerGraph | null {
  if (
    typeof response.answer !== "string" ||
    response.answer.length === 0 ||
    !Array.isArray(response.claims) ||
    response.claims.length === 0 ||
    !Array.isArray(response.citations) ||
    response.citations.length === 0 ||
    (response.abstentionReason !== null &&
      response.abstentionReason !== undefined)
  ) {
    return null;
  }

  const points = Array.from(response.answer);
  const citationById = new Map<string, RetrievalCitation>();
  const citationNumberById = new Map<string, number>();
  const edgeNumberById = new Map<string, number>();
  for (const citation of response.citations) {
    if (
      typeof citation !== "object" ||
      citation === null ||
      typeof citation.citationId !== "string" ||
      citation.citationId.length === 0 ||
      citationById.has(citation.citationId)
    ) {
      return null;
    }
    citationById.set(citation.citationId, citation);
    if (numbering === "source-order") {
      if (isEdgeCitation(citation)) {
        edgeNumberById.set(citation.citationId, edgeNumberById.size + 1);
      } else {
        citationNumberById.set(
          citation.citationId,
          citationNumberById.size + 1,
        );
      }
    }
  }

  // LangGraph PR 3 review ruling (2026-10-16): claims cite a sentence, not
  // necessarily a whole paragraph, so a claim span no longer has to start/end
  // on a `\n\n` boundary (mirrors `contracts/http.py`'s
  // `RetrievalAnswerResponse.validate_claim_spans`). The only shape rule left
  // is that spans are non-overlapping, in increasing order and therefore
  // unique -- `start < previousEnd` below rejects any overlap or duplicate.
  let previousEnd = 0;
  const claimIds = new Set<string>();
  for (const claim of response.claims) {
    if (typeof claim !== "object" || claim === null) {
      return null;
    }
    const { answerStart: start, answerEnd: end } = claim;
    if (
      typeof claim.claimId !== "string" ||
      claim.claimId.length === 0 ||
      !CLAIM_ID_PATTERN.test(claim.claimId) ||
      claimIds.has(claim.claimId) ||
      !Number.isSafeInteger(start) ||
      !Number.isSafeInteger(end) ||
      start < 0 ||
      end <= start ||
      end > points.length ||
      start < previousEnd ||
      typeof claim.text !== "string" ||
      claim.text.includes("\n\n") ||
      points.slice(start, end).join("") !== claim.text ||
      !Array.isArray(claim.citationIds) ||
      claim.citationIds.length === 0 ||
      new Set(claim.citationIds).size !== claim.citationIds.length ||
      claim.citationIds.some(
        (citationId) =>
          typeof citationId !== "string" || citationId.length === 0,
      ) ||
      claim.citationIds.some((citationId) => !citationById.has(citationId))
    ) {
      return null;
    }
    claimIds.add(claim.claimId);
    previousEnd = end;
  }

  if (numbering === "shown-order") {
    for (const claim of response.claims) {
      for (const citationId of claim.citationIds) {
        if (
          citationNumberById.has(citationId) ||
          edgeNumberById.has(citationId)
        ) {
          continue;
        }
        const citation = citationById.get(citationId);
        if (citation !== undefined && isEdgeCitation(citation)) {
          edgeNumberById.set(citationId, edgeNumberById.size + 1);
        } else {
          citationNumberById.set(citationId, citationNumberById.size + 1);
        }
      }
    }
  }

  return {
    citationById,
    citationNumberById,
    edgeNumberById,
    claims: response.claims,
    points,
  };
}

function SafeMarkdown({ children }: { children: string }) {
  if (children.length === 0) return null;
  return (
    <div className="tapper-markdown">
      <ReactMarkdown
        rehypePlugins={[[rehypeSanitize, answerSchema]]}
        components={{ a: ({ children: label }) => <span>{label}</span> }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

// A claim ID that will end up as the `data-claim-id` attribute of a spliced
// `<code>` element (see `citationBadgeNode`) and as a React context map key;
// restricted defensively to a closed, DOM-attribute-safe alphabet (review
// ruling, 2026-10-17, round 3) even though the backend already generates
// opaque, closed-alphabet IDs.
const CLAIM_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

/** A point in an mdast `Position`; only `offset` is ever read here, so line
 * and column are filled with a harmless constant rather than tracked. */
interface MdPoint {
  line: number;
  column: number;
  offset?: number;
}

interface MdNode {
  type: string;
  value?: string;
  position?: { start: MdPoint; end: MdPoint };
  children?: MdNode[];
  data?: { hName?: string; hProperties?: Record<string, string> };
}

function offsetPoint(offset: number): MdPoint {
  return { line: 1, column: 1, offset };
}

/** The mdast node spliced in for one claim's citation badge: an empty
 * `<code data-claim-id="...">` element via mdast-to-hast's `data.hName`/
 * `data.hProperties` convention. An element (not raw text) means there is no
 * Markdown source to parse, so unlike a textual placeholder it can never
 * merge with an adjacent code span's backticks or be escaped by a
 * preceding backslash (the two concrete bugs round 3 of the review ruling,
 * 2026-10-17, found in the textual-sentinel approach this replaces). */
function citationBadgeNode(claimId: string): MdNode {
  return {
    type: "tapperCitation",
    children: [],
    data: { hName: "code", hProperties: { dataClaimId: claimId } },
  };
}

// Leaf (childless, text-bearing) node types a claim's `answerEnd` offset can
// land in or at the end of. `inlineCode` is never split -- a citation can
// end exactly at a code span's boundary (the bug case this round fixes) but
// splitting its source text would corrupt it; `text` is split when the
// offset falls strictly inside it, preserving an accurate `position` on
// both halves so a later claim's offset still resolves correctly against
// this same, already-mutated tree.
function spliceCitationAtOffset(
  node: MdNode,
  offset: number,
  claimId: string,
): boolean {
  const children = node.children;
  if (children === undefined) return false;
  for (let index = 0; index < children.length; index += 1) {
    const child = children[index]!;
    const position = child.position;
    if (
      (child.type === "text" || child.type === "inlineCode") &&
      position?.start.offset !== undefined &&
      position.end.offset !== undefined &&
      offset >= position.start.offset &&
      offset <= position.end.offset
    ) {
      const badge = citationBadgeNode(claimId);
      const start = position.start.offset;
      const end = position.end.offset;
      if (offset === end || child.type === "inlineCode") {
        children.splice(index + 1, 0, badge);
      } else if (offset === start) {
        children.splice(index, 0, badge);
      } else if (typeof child.value === "string") {
        const relative = offset - start;
        children.splice(
          index,
          1,
          {
            type: child.type,
            value: child.value.slice(0, relative),
            position: { start: position.start, end: offsetPoint(offset) },
          },
          badge,
          {
            type: child.type,
            value: child.value.slice(relative),
            position: { start: offsetPoint(offset), end: position.end },
          },
        );
      } else {
        children.splice(index + 1, 0, badge);
      }
      return true;
    }
    if (spliceCitationAtOffset(child, offset, claimId)) return true;
  }
  return false;
}

/** Converts a code-point offset (what `claim.answerEnd` is, matching the
 * backend's Python string indexing) to a UTF-16 code-unit offset (what
 * remark/mdast `position.offset` values are, matching JavaScript string
 * indexing) -- they differ whenever the answer contains an astral character
 * (e.g. an emoji) before the offset, which takes one code point but two
 * UTF-16 units. */
function codePointToUtf16Offset(
  points: readonly string[],
  codePointOffset: number,
): number {
  let utf16Offset = 0;
  for (let index = 0; index < codePointOffset; index += 1) {
    utf16Offset += points[index]!.length;
  }
  return utf16Offset;
}

/** A unified/remark plugin (factory): splices one citation-badge node into
 * the parsed Markdown AST right after each claim's own text, located by the
 * original source offset rather than by Markdown syntax -- so list items,
 * headings and a claim ending in inline code, a backslash, or right before
 * another claim all still parse and render as ordinary Markdown. */
function citationRemarkPlugin(
  insertions: readonly { utf16Offset: number; claimId: string }[],
) {
  return (tree: MdNode): void => {
    for (const insertion of insertions) {
      spliceCitationAtOffset(tree, insertion.utf16Offset, insertion.claimId);
    }
  };
}

function CitationBadges({
  claim,
  graph,
  onOpenCitation,
  locale,
  historicalCitationQueryFor,
  onPreviewCitation,
}: {
  claim: RetrievalClaim;
  graph: ValidAnswerGraph;
  onOpenCitation: (citationId: string, trigger: HTMLElement) => void;
  locale: AnswerLocale;
  historicalCitationQueryFor?: (
    citationId: string,
  ) => HistoricalCitationQuery | undefined;
  onPreviewCitation?: (citationId: string) => void;
}) {
  const text = locale === "zh" ? COPY : CITATION_EN;
  return (
    <span
      className="tapper-claim-citations"
      data-claim-id={claim.claimId}
      aria-label={locale === "zh" ? "本句引用" : "Sources for this statement"}
    >
      {claim.citationIds.map((citationId) => {
        const citation = graph.citationById.get(citationId);
        if (citation !== undefined && isEdgeCitation(citation)) {
          const number = graph.edgeNumberById.get(citationId)!;
          return (
            <EdgeCitationChip
              key={citationId}
              number={number}
              citation={citation}
              locale={locale}
              ariaLabel={text.edgeCitation(number)}
              historicalQuery={historicalCitationQueryFor?.(citationId)}
              onPreview={onPreviewCitation}
              onOpen={onOpenCitation}
            />
          );
        }
        return (
          <Button
            key={citationId}
            type="text"
            size="small"
            data-kind="chunk"
            aria-label={ANSWER_COPY[locale].citation(
              graph.citationNumberById.get(citationId)!,
            )}
            onClick={(event) => onOpenCitation(citationId, event.currentTarget)}
          >
            {`[${String(graph.citationNumberById.get(citationId))}]`}
          </Button>
        );
      })}
    </span>
  );
}

interface CitedMarkdownContextValue {
  claimById: ReadonlyMap<string, RetrievalClaim>;
  graph: ValidAnswerGraph;
  onOpenCitation: (citationId: string, trigger: HTMLElement) => void;
  locale: AnswerLocale;
  historicalCitationQueryFor?: (
    citationId: string,
  ) => HistoricalCitationQuery | undefined;
  onPreviewCitation?: (citationId: string) => void;
}

// `ReactMarkdown`'s `components` map supplies the React *component type*
// rendered at each slot; if that function were created fresh on every render
// (e.g. an inline arrow closing over per-render props), React would see a
// different type at the citation badge's slot each time `GroundedAnswer`
// re-renders for an unrelated reason (e.g. opening the citation panel) and
// remount it, destroying the very button `LegacyTapperWorkspace` is about to
// restore focus to. `CITED_MARKDOWN_COMPONENTS` is therefore a module-level
// constant -- the same two function references for the lifetime of the
// app -- and reads the per-render data (claims, callbacks, locale) through
// `CitedMarkdownContext` instead of closures (round 2 of the LangGraph PR 3
// review ruling, 2026-10-16).
const CitedMarkdownContext = createContext<CitedMarkdownContextValue | null>(
  null,
);

function CitedMarkdownLink({ children }: { children?: ReactNode }) {
  return <span>{children}</span>;
}

function CitedMarkdownCode({
  children,
  className,
  "data-claim-id": claimId,
}: {
  children?: ReactNode;
  className?: string;
  "data-claim-id"?: string;
}) {
  const context = useContext(CitedMarkdownContext);
  const claim =
    context !== null &&
    typeof claimId === "string" &&
    CLAIM_ID_PATTERN.test(claimId)
      ? context.claimById.get(claimId)
      : undefined;
  if (context === null || claim === undefined) {
    return <code className={className}>{children}</code>;
  }
  return (
    <CitationBadges
      claim={claim}
      graph={context.graph}
      onOpenCitation={context.onOpenCitation}
      locale={context.locale}
      historicalCitationQueryFor={context.historicalCitationQueryFor}
      onPreviewCitation={context.onPreviewCitation}
    />
  );
}

const CITED_MARKDOWN_COMPONENTS = {
  a: CitedMarkdownLink,
  code: CitedMarkdownCode,
};

function CitedAnswerMarkdown({
  graph,
  onOpenCitation,
  locale,
  historicalCitationQueryFor,
  onPreviewCitation,
}: {
  graph: ValidAnswerGraph;
  onOpenCitation: (citationId: string, trigger: HTMLElement) => void;
  locale: AnswerLocale;
  historicalCitationQueryFor?: (
    citationId: string,
  ) => HistoricalCitationQuery | undefined;
  onPreviewCitation?: (citationId: string) => void;
}) {
  const claimById = useMemo(
    () => new Map(graph.claims.map((claim) => [claim.claimId, claim])),
    [graph],
  );
  const markdown = useMemo(() => graph.points.join(""), [graph]);
  const insertions = useMemo(
    () =>
      graph.claims.map((claim) => ({
        utf16Offset: codePointToUtf16Offset(graph.points, claim.answerEnd),
        claimId: claim.claimId,
      })),
    [graph],
  );
  const contextValue = useMemo<CitedMarkdownContextValue>(
    () => ({
      claimById,
      graph,
      onOpenCitation,
      locale,
      historicalCitationQueryFor,
      onPreviewCitation,
    }),
    [
      claimById,
      graph,
      onOpenCitation,
      locale,
      historicalCitationQueryFor,
      onPreviewCitation,
    ],
  );
  return (
    <div className="tapper-markdown tapper-grounded-claim">
      <CitedMarkdownContext.Provider value={contextValue}>
        <ReactMarkdown
          remarkPlugins={[[citationRemarkPlugin, insertions]]}
          rehypePlugins={[[rehypeSanitize, answerSchema]]}
          components={CITED_MARKDOWN_COMPONENTS}
        >
          {markdown}
        </ReactMarkdown>
      </CitedMarkdownContext.Provider>
    </div>
  );
}

export function GroundedAnswer({
  response,
  onOpenCitation,
  locale = "zh",
  citationNumbering = "source-order",
  historicalCitationQueryFor,
  onPreviewCitation,
}: {
  response: RetrievalAnswerResponse | null | undefined;
  onOpenCitation: (citationId: string, trigger: HTMLElement) => void;
  locale?: AnswerLocale;
  citationNumbering?: "source-order" | "shown-order";
  /** Resolves an edge citation's supporting-passage snippet against the
   * turn's own historical answer instead of the chunk's current content
   * (set by `TapperWorkspace`, which can import `useConversationCitations`
   * from `features/conversations` — `features/knowledge` cannot). Omitted
   * in tests that render `GroundedAnswer` standalone; `RelationHoverCard`
   * falls back to the current-authority citation query in that case. */
  historicalCitationQueryFor?: (
    citationId: string,
  ) => HistoricalCitationQuery | undefined;
  /** Reports the first time an edge citation's chip is hovered or
   * focused, so `TapperWorkspace` can enable that citation's (otherwise
   * disabled) historical query — an answer citing several edges should
   * not fetch every one of their snippets just because the turn
   * rendered. */
  onPreviewCitation?: (citationId: string) => void;
}) {
  if (
    typeof response !== "object" ||
    response === null ||
    typeof response.abstained !== "boolean"
  ) {
    return <FormatError locale={locale} />;
  }
  if (response.abstained) {
    const reason = response.abstentionReason;
    if (
      typeof response.answer !== "string" ||
      response.answer.length !== 0 ||
      !Array.isArray(response.claims) ||
      response.claims.length !== 0 ||
      !Array.isArray(response.citations) ||
      !hasValidCitationIdentities(response.citations) ||
      typeof reason !== "string" ||
      !Object.hasOwn(ANSWER_COPY[locale], reason)
    ) {
      return <FormatError locale={locale} />;
    }
    const message =
      ANSWER_COPY[locale][
        reason as
          | "insufficient_evidence"
          | "conflicting_sources"
          | "revision_mismatch"
          | "out_of_scope"
      ];
    return <Alert type="info" showIcon title={message} />;
  }

  if (response.retrievalProfileId === "direct-chat-v1") {
    if (
      typeof response.answer !== "string" ||
      response.answer.length === 0 ||
      !Array.isArray(response.claims) ||
      response.claims.length !== 0 ||
      !Array.isArray(response.citations) ||
      response.citations.length !== 0 ||
      response.graphContextStatus !== "NOT_SELECTED" ||
      response.degradedMode !== false ||
      (response.abstentionReason !== null &&
        response.abstentionReason !== undefined)
    ) {
      return <FormatError locale={locale} />;
    }
    return (
      <div className="tapper-direct-answer">
        <SafeMarkdown>{response.answer}</SafeMarkdown>
      </div>
    );
  }

  const graph = validateAnswerGraph(response, citationNumbering);
  if (graph === null) {
    return <FormatError locale={locale} />;
  }

  return (
    <div className="tapper-grounded-answer">
      <h3 className="tapper-answer-heading">{ANSWER_COPY[locale].heading}</h3>
      {response.degradedMode ? (
        <Alert
          className="tapper-answer-note"
          type="warning"
          showIcon
          title={ANSWER_COPY[locale].degraded}
        />
      ) : null}
      <CitedAnswerMarkdown
        graph={graph}
        onOpenCitation={onOpenCitation}
        locale={locale}
        historicalCitationQueryFor={historicalCitationQueryFor}
        onPreviewCitation={onPreviewCitation}
      />
    </div>
  );
}
