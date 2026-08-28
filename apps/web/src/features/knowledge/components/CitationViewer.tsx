import { Alert, Button, Descriptions, Skeleton, Typography } from "antd";

import { useCitationQuery } from "../api/queries";
import type { CitationPreview, RetrievalAnswerResponse } from "../api/types";
import { COPY, safeCitationProblem } from "../copy";

type RetrievalCitation = RetrievalAnswerResponse["citations"][number];
type DocumentAnchor = Extract<CitationPreview["anchor"], { type: "document" }>;

function isOptionalNumber(value: unknown): value is number | null | undefined {
  return (
    value === null ||
    value === undefined ||
    (typeof value === "number" && Number.isFinite(value))
  );
}

function isDocumentAnchor(value: unknown): value is DocumentAnchor {
  if (typeof value !== "object" || value === null || !("type" in value)) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    candidate.type === "document" &&
    (candidate.headingPath === null ||
      candidate.headingPath === undefined ||
      (Array.isArray(candidate.headingPath) &&
        candidate.headingPath.every((item) => typeof item === "string"))) &&
    (candidate.bbox === null ||
      candidate.bbox === undefined ||
      (Array.isArray(candidate.bbox) &&
        candidate.bbox.every(
          (coordinate) =>
            typeof coordinate === "number" && Number.isFinite(coordinate),
        ))) &&
    isOptionalNumber(candidate.page) &&
    isOptionalNumber(candidate.startOffset) &&
    isOptionalNumber(candidate.endOffset)
  );
}

function hasString(value: Record<string, unknown>, key: string): boolean {
  return typeof value[key] === "string";
}

function isPreviewShape(
  value: unknown,
): value is CitationPreview & { anchor: DocumentAnchor } {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    hasString(candidate, "citationId") &&
    hasString(candidate, "documentId") &&
    hasString(candidate, "filename") &&
    hasString(candidate, "revisionId") &&
    hasString(candidate, "sourceContentHash") &&
    hasString(candidate, "chunkContentHash") &&
    hasString(candidate, "quote") &&
    (candidate.prefix === undefined || typeof candidate.prefix === "string") &&
    (candidate.suffix === undefined || typeof candidate.suffix === "string") &&
    isDocumentAnchor(candidate.anchor)
  );
}

function anchorFacts(anchor: DocumentAnchor) {
  return {
    type: anchor.type,
    headingPath: anchor.headingPath ?? null,
    page: anchor.page ?? null,
    bbox: anchor.bbox ?? null,
    startOffset: anchor.startOffset ?? null,
    endOffset: anchor.endOffset ?? null,
  };
}

function exactPreview(
  preview: unknown,
  requestedId: string,
  expected: unknown,
): preview is CitationPreview & { anchor: DocumentAnchor } {
  if (
    !isPreviewShape(preview) ||
    typeof expected !== "object" ||
    expected === null
  ) {
    return false;
  }
  const expectedCitation = expected as Record<string, unknown>;
  const source = expectedCitation.source;
  if (typeof source !== "object" || source === null) return false;
  const expectedSource = source as Record<string, unknown>;
  if (!isDocumentAnchor(expectedSource.anchor)) return false;
  if (
    preview.citationId !== requestedId ||
    expectedCitation.citationId !== requestedId ||
    preview.documentId !== expectedSource.sourceId ||
    preview.revisionId !== expectedSource.revision ||
    preview.sourceContentHash !== expectedSource.sourceContentHash ||
    preview.chunkContentHash !== expectedCitation.chunkContentHash
  ) {
    return false;
  }
  return (
    JSON.stringify(anchorFacts(preview.anchor)) ===
    JSON.stringify(anchorFacts(expectedSource.anchor))
  );
}

export function CitationViewer({
  active,
  onClose,
}: {
  active: {
    citation: RetrievalCitation;
    generation: number;
    id: string;
  } | null;
  onClose: () => void;
}) {
  const citationQuery = useCitationQuery(
    active?.id ?? null,
    active?.generation ?? 0,
  );
  const preview =
    active !== null &&
    !citationQuery.isFetching &&
    !citationQuery.isError &&
    citationQuery.data !== undefined &&
    exactPreview(citationQuery.data, active.id, active.citation)
      ? citationQuery.data
      : null;
  const invalidPreview =
    active !== null &&
    !citationQuery.isFetching &&
    !citationQuery.isError &&
    citationQuery.data !== undefined &&
    preview === null;
  const problem = citationQuery.isError
    ? safeCitationProblem(citationQuery.error)
    : null;

  return (
    <section
      className="athena-panel athena-citation-panel"
      aria-labelledby="citation-heading"
    >
      <header className="athena-panel-header">
        <Typography.Title level={3} id="citation-heading">
          {COPY.citationTitle}
        </Typography.Title>
        {active !== null ? (
          <Button onClick={onClose} aria-label={COPY.closeCitation}>
            {COPY.close}
          </Button>
        ) : null}
      </header>

      {active === null ? (
        <p className="athena-panel-placeholder">{COPY.citationEmpty}</p>
      ) : null}
      {active !== null && citationQuery.isFetching ? (
        <div aria-live="polite">
          <span>{COPY.citationLoading}</span>
          <Skeleton active paragraph={{ rows: 7 }} />
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
                {COPY.retryCitation}
              </Button>
            ) : undefined
          }
        />
      ) : null}
      {invalidPreview ? (
        <Alert type="error" showIcon title={COPY.citationInvalid} />
      ) : null}
      {preview !== null ? (
        <div className="athena-citation-content">
          <Typography.Title level={4}>{COPY.citationEvidence}</Typography.Title>
          <Typography.Text strong>{preview.filename}</Typography.Text>
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label={COPY.revisionId}>
              <code>{preview.revisionId}</code>
            </Descriptions.Item>
            <Descriptions.Item label={COPY.sourceContentHash}>
              <code>{preview.sourceContentHash}</code>
            </Descriptions.Item>
            <Descriptions.Item label={COPY.chunkContentHash}>
              <code>{preview.chunkContentHash}</code>
            </Descriptions.Item>
            <Descriptions.Item label={COPY.headingPath}>
              {preview.anchor.headingPath?.join(" / ") ?? "—"}
            </Descriptions.Item>
            <Descriptions.Item label={COPY.page}>
              {preview.anchor.page ?? "—"}
            </Descriptions.Item>
            <Descriptions.Item label={COPY.offsets}>
              {preview.anchor.startOffset === null ||
              preview.anchor.startOffset === undefined ||
              preview.anchor.endOffset === null ||
              preview.anchor.endOffset === undefined
                ? "—"
                : `${preview.anchor.startOffset}–${preview.anchor.endOffset}`}
            </Descriptions.Item>
          </Descriptions>
          <blockquote className="athena-citation-quote">
            <span>{preview.prefix ?? ""}</span>
            <mark>{preview.quote}</mark>
            <span>{preview.suffix ?? ""}</span>
          </blockquote>
        </div>
      ) : null}
    </section>
  );
}
