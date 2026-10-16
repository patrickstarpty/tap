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
  attributes: {},
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

// A claim's citation badges are spliced into the single Markdown document as
// an inline code span carrying this sentinel wrapper around the claim ID
// (U+0001, never legitimate answer prose) immediately after the claim's own
// text, so `CitedAnswerMarkdown`'s `code` override can find and replace it --
// this is what lets badges land inline right after an arbitrary mid-paragraph
// or mid-list-item offset without a custom remark/rehype plugin, while lists,
// headings and multi-claim paragraphs still parse as one normal document
// (LangGraph PR 3 review ruling, 2026-10-16, round 2).
const CITATION_PLACEHOLDER_MARK = "\u0001";
const CITATION_PLACEHOLDER_PATTERN = /^\u0001([^\u0001]+)\u0001$/;

function citationPlaceholder(claimId: string): string {
  return `\`${CITATION_PLACEHOLDER_MARK}${claimId}${CITATION_PLACEHOLDER_MARK}\``;
}

/** The whole answer, verbatim, with one inline citation-badge placeholder
 * spliced in right after each claim's `answerEnd` offset. */
function citedAnswerMarkdown(graph: ValidAnswerGraph): string {
  const parts: string[] = [];
  let cursor = 0;
  for (const claim of graph.claims) {
    parts.push(graph.points.slice(cursor, claim.answerEnd).join(""));
    parts.push(citationPlaceholder(claim.claimId));
    cursor = claim.answerEnd;
  }
  parts.push(graph.points.slice(cursor).join(""));
  return parts.join("");
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
      aria-label={locale === "zh" ? "本段引用" : "Sources for this paragraph"}
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
}: {
  children?: ReactNode;
  className?: string;
}) {
  const context = useContext(CitedMarkdownContext);
  const content =
    typeof children === "string"
      ? children
      : Array.isArray(children) &&
          children.every((child) => typeof child === "string")
        ? children.join("")
        : null;
  const match =
    content === null ? null : CITATION_PLACEHOLDER_PATTERN.exec(content);
  const claim =
    context !== null && match !== null
      ? context.claimById.get(match[1]!)
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
  const markdown = useMemo(() => citedAnswerMarkdown(graph), [graph]);
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
