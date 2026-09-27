import type { components, paths } from "../../../shared/api/generated/schema";

export type CitationPreview = components["schemas"]["CitationPreview"];
export type DocumentAccepted = components["schemas"]["DocumentAccepted"];
export type DocumentDetail = components["schemas"]["DocumentDetail"];
export type DocumentPage = components["schemas"]["DocumentPage"];
export type DocumentStageSnapshot =
  components["schemas"]["DocumentStageSnapshot"];
export type DocumentStageState = components["schemas"]["DocumentStageState"];
export type DocumentStatus = components["schemas"]["DocumentStatus"];
export type DocumentSummary = components["schemas"]["DocumentSummary"];
export type SourceSummary = components["schemas"]["SourceSummary"];
export type SourcePage = components["schemas"]["SourcePage"];
export type SourceDetail = components["schemas"]["SourceDetail"];
export type SourceAccepted = components["schemas"]["SourceAccepted"];
export type SourceRetryRequest = components["schemas"]["SourceRetryRequest"];
export type KnowledgeReviewDetail =
  components["schemas"]["KnowledgeReviewDetail"];
export type KnowledgeReviewPage = components["schemas"]["KnowledgeReviewPage"];
export type KnowledgeReviewInventory =
  components["schemas"]["KnowledgeReviewInventory"];
export type KnowledgeReviewDecisionPage =
  components["schemas"]["KnowledgeReviewDecisionPage"];
export type KnowledgeReviewHistoryPage =
  components["schemas"]["KnowledgeReviewHistoryPage"];
export type KnowledgePublicationPage =
  components["schemas"]["KnowledgePublicationPage"];
export type KnowledgeReviewItemComparison =
  components["schemas"]["KnowledgeReviewItemComparison"];
export type KnowledgeReviewDecisionRequest =
  components["schemas"]["KnowledgeReviewDecisionRequest"];
export type KnowledgePublicationDetail =
  components["schemas"]["KnowledgePublicationDetail"];
export type PublishedKnowledgeSourcePage =
  components["schemas"]["PublishedKnowledgeSourcePage"];
export type IngestionStage = components["schemas"]["IngestionStage"];
export type RetrievalAnswerRequest =
  components["schemas"]["RetrievalAnswerRequest"];
export type RetrievalAnswerResponse =
  components["schemas"]["RetrievalAnswerResponse"];

export type ProblemDetails =
  paths["/api/v1/projects/{project_id}/knowledge/documents"]["get"]["responses"][422]["content"]["application/problem+json"];

export interface ListDocumentsInput {
  cursor?: string;
  limit: number;
  signal?: AbortSignal;
}

export type KnowledgeFlowchart = components["schemas"]["KnowledgeFlowchart"];
export type KnowledgeFlowchartCorrection =
  components["schemas"]["KnowledgeFlowchartCorrection"];

export interface KnowledgeClient {
  readonly projectId: string;
  getReviewFlowchart(reviewId: string): Promise<KnowledgeFlowchart>;
  correctReviewFlowchart(
    reviewId: string,
    version: number,
    graph: KnowledgeFlowchart,
  ): Promise<KnowledgeFlowchartCorrection>;
  listReviews(input: {
    sourceRevisionId?: string;
    afterReviewId?: string;
    limit?: number;
    signal?: AbortSignal;
  }): Promise<KnowledgeReviewPage>;
  openDocumentReview(
    documentId: string,
    sourceRevisionId: string,
    idempotencyKey: string,
  ): Promise<KnowledgeReviewDetail>;
  getReview(
    reviewId: string,
    signal?: AbortSignal,
  ): Promise<KnowledgeReviewDetail>;
  listReviewInventory(
    reviewId: string,
    afterItemId?: string,
  ): Promise<KnowledgeReviewInventory>;
  listReviewDecisionHistory(
    reviewId: string,
    afterVersion?: number,
  ): Promise<KnowledgeReviewDecisionPage>;
  listReviewHistory(
    reviewId: string,
    afterVersion?: number,
  ): Promise<KnowledgeReviewHistoryPage>;
  listReviewPublications(
    reviewId: string,
    afterPublicationId?: string,
  ): Promise<KnowledgePublicationPage>;
  compareReviewItem(
    reviewId: string,
    itemId: string,
  ): Promise<KnowledgeReviewItemComparison>;
  originalImageUrl(reviewId: string, itemId: string): string;
  decideReviewItem(
    reviewId: string,
    itemId: string,
    version: number,
    body: KnowledgeReviewDecisionRequest,
  ): Promise<KnowledgeReviewDetail>;
  transitionReview(
    reviewId: string,
    action: "submit" | "return" | "approve",
    version: number,
  ): Promise<
    KnowledgeReviewDetail | components["schemas"]["KnowledgeReviewSummary"]
  >;
  publishReview(
    reviewId: string,
    version: number,
    generation: string,
    key?: string,
  ): Promise<KnowledgePublicationDetail>;
  withdrawPublication(
    publicationId: string,
    version: number,
    key?: string,
  ): Promise<KnowledgePublicationDetail>;
  listPublishedSources(
    signal?: AbortSignal,
  ): Promise<PublishedKnowledgeSourcePage>;
  listSources(input: ListDocumentsInput): Promise<SourcePage>;
  getSource(sourceId: string, signal?: AbortSignal): Promise<SourceDetail>;
  uploadSource(
    file: File,
    onProgress: (ratio: number) => void,
    signal?: AbortSignal,
    idempotencyKey?: string,
  ): Promise<SourceAccepted>;
  retrySource(
    sourceId: string,
    request: SourceRetryRequest,
    idempotencyKey?: string,
  ): Promise<SourceAccepted>;
  deleteSource(sourceId: string, idempotencyKey?: string): Promise<void>;
  listDocuments(input: ListDocumentsInput): Promise<DocumentPage>;
  getDocument(
    documentId: string,
    signal?: AbortSignal,
  ): Promise<DocumentDetail>;
  uploadDocument(
    file: File,
    onProgress: (ratio: number) => void,
    signal?: AbortSignal,
    idempotencyKey?: string,
  ): Promise<DocumentAccepted>;
  retryDocument(
    documentId: string,
    idempotencyKey?: string,
  ): Promise<DocumentAccepted>;
  deleteDocument(documentId: string, idempotencyKey?: string): Promise<void>;
  createAnswer(
    request: RetrievalAnswerRequest,
    signal?: AbortSignal,
  ): Promise<RetrievalAnswerResponse>;
  getCitation(
    citationId: string,
    signal?: AbortSignal,
  ): Promise<CitationPreview>;
}
