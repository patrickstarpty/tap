import { Alert, Button } from "antd";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";

import type { RetrievalAnswerResponse } from "../api/types";

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

function isParagraphBoundary(
  points: readonly string[],
  start: number,
  end: number,
): boolean {
  const startsAtBoundary =
    start === 0 || (points[start - 2] === "\n" && points[start - 1] === "\n");
  const endsAtBoundary =
    end === points.length || (points[end] === "\n" && points[end + 1] === "\n");
  return startsAtBoundary && endsAtBoundary;
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
  for (const [index, citation] of response.citations.entries()) {
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
    if (numbering === "source-order")
      citationNumberById.set(citation.citationId, index + 1);
  }

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
      !isParagraphBoundary(points, start, end) ||
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
        if (!citationNumberById.has(citationId)) {
          citationNumberById.set(citationId, citationNumberById.size + 1);
        }
      }
    }
  }

  return {
    citationById,
    citationNumberById,
    claims: response.claims,
    points,
  };
}

function SafeMarkdown({
  children,
  trailing,
}: {
  children: string;
  trailing?: ReactNode;
}) {
  if (children.length === 0) return null;
  return (
    <div
      className={`tapper-markdown${trailing ? " tapper-markdown--with-citation" : ""}`}
    >
      <ReactMarkdown
        rehypePlugins={[[rehypeSanitize, answerSchema]]}
        components={{ a: ({ children: label }) => <span>{label}</span> }}
      >
        {children}
      </ReactMarkdown>
      {trailing}
    </div>
  );
}

function CitedClaim({
  claim,
  graph,
  onOpenCitation,
  locale,
}: {
  claim: RetrievalClaim;
  graph: ValidAnswerGraph;
  onOpenCitation: (citationId: string, trigger: HTMLElement) => void;
  locale: AnswerLocale;
}) {
  const citations = (
    <span
      className="tapper-claim-citations"
      aria-label={locale === "zh" ? "本段引用" : "Sources for this paragraph"}
    >
      {claim.citationIds.map((citationId) => (
        <Button
          key={citationId}
          type="text"
          size="small"
          aria-label={ANSWER_COPY[locale].citation(
            graph.citationNumberById.get(citationId)!,
          )}
          onClick={(event) => onOpenCitation(citationId, event.currentTarget)}
        >
          {`[${String(graph.citationNumberById.get(citationId))}]`}
        </Button>
      ))}
    </span>
  );
  return (
    <div className="tapper-grounded-claim">
      <SafeMarkdown trailing={citations}>{claim.text}</SafeMarkdown>
    </div>
  );
}

function groundedSegments(
  graph: ValidAnswerGraph,
  onOpenCitation: (citationId: string, trigger: HTMLElement) => void,
  locale: AnswerLocale,
): ReactNode[] {
  const segments: ReactNode[] = [];
  let cursor = 0;
  for (const claim of graph.claims) {
    const before = graph.points.slice(cursor, claim.answerStart).join("");
    if (before.length > 0) {
      segments.push(
        <SafeMarkdown key={`before-${String(cursor)}`}>{before}</SafeMarkdown>,
      );
    }
    segments.push(
      <CitedClaim
        key={claim.claimId}
        claim={claim}
        graph={graph}
        onOpenCitation={onOpenCitation}
        locale={locale}
      />,
    );
    cursor = claim.answerEnd;
  }
  const after = graph.points.slice(cursor).join("");
  if (after.length > 0) {
    segments.push(<SafeMarkdown key="after-claims">{after}</SafeMarkdown>);
  }
  return segments;
}

export function GroundedAnswer({
  response,
  onOpenCitation,
  locale = "zh",
  citationNumbering = "source-order",
}: {
  response: RetrievalAnswerResponse | null | undefined;
  onOpenCitation: (citationId: string, trigger: HTMLElement) => void;
  locale?: AnswerLocale;
  citationNumbering?: "source-order" | "shown-order";
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
          "insufficient_evidence" | "conflicting_sources" | "revision_mismatch"
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
      {groundedSegments(graph, onOpenCitation, locale)}
    </div>
  );
}
