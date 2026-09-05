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

const ABSTENTION_COPY = {
  insufficient_evidence: "所选来源中没有足够证据回答这个问题。",
  conflicting_sources: "所选来源之间存在冲突，暂时无法给出可靠回答。",
  revision_mismatch: "来源版本已经变化，请重新提交问题。",
} as const;

type RetrievalClaim = RetrievalAnswerResponse["claims"][number];
type RetrievalCitation = RetrievalAnswerResponse["citations"][number];

interface ValidAnswerGraph {
  citationById: ReadonlyMap<string, RetrievalCitation>;
  citationNumberById: ReadonlyMap<string, number>;
  claims: readonly RetrievalClaim[];
  points: readonly string[];
}

function FormatError() {
  return <Alert type="error" showIcon title="回答格式无法核验，请重新提问。" />;
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

  return {
    citationById,
    citationNumberById,
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

function CitedClaim({
  claim,
  graph,
  onOpenCitation,
}: {
  claim: RetrievalClaim;
  graph: ValidAnswerGraph;
  onOpenCitation: (citationId: string, trigger: HTMLElement) => void;
}) {
  return (
    <div className="tapper-grounded-claim">
      <SafeMarkdown>{claim.text}</SafeMarkdown>
      <span className="tapper-claim-citations" aria-label="本段引用">
        {claim.citationIds.map((citationId) => (
          <Button
            key={citationId}
            type="link"
            size="small"
            onClick={(event) => onOpenCitation(citationId, event.currentTarget)}
          >
            {`引用 ${String(graph.citationNumberById.get(citationId))}`}
          </Button>
        ))}
      </span>
    </div>
  );
}

function groundedSegments(
  graph: ValidAnswerGraph,
  onOpenCitation: (citationId: string, trigger: HTMLElement) => void,
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
}: {
  response: RetrievalAnswerResponse | null | undefined;
  onOpenCitation: (citationId: string, trigger: HTMLElement) => void;
}) {
  if (
    typeof response !== "object" ||
    response === null ||
    typeof response.abstained !== "boolean"
  ) {
    return <FormatError />;
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
      !Object.hasOwn(ABSTENTION_COPY, reason)
    ) {
      return <FormatError />;
    }
    const message = ABSTENTION_COPY[reason as keyof typeof ABSTENTION_COPY];
    return <Alert type="info" showIcon title={message} />;
  }

  const graph = validateAnswerGraph(response);
  if (graph === null) {
    return <FormatError />;
  }

  return (
    <div className="tapper-grounded-answer">
      {response.degradedMode ? (
        <Alert
          className="tapper-answer-note"
          type="warning"
          showIcon
          title="部分检索能力暂时受限，回答仍仅显示已核验依据。"
        />
      ) : null}
      {groundedSegments(graph, onOpenCitation)}
    </div>
  );
}
