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

export interface KnowledgeClient {
  readonly projectId: string;
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
