import type {
  CitationPreview,
  DocumentAccepted,
  DocumentDetail,
  DocumentPage,
  DocumentStageSnapshot,
  DocumentSummary,
  KnowledgeClient,
  RetrievalAnswerResponse,
} from "../api/types";

const NOW = "2026-08-28T07:30:00Z";

const DEFAULT_STAGES: DocumentStageSnapshot[] = [
  { stage: "stored", state: "completed", completedAt: NOW, errorCode: null },
  { stage: "parsing", state: "completed", completedAt: NOW, errorCode: null },
  { stage: "chunking", state: "completed", completedAt: NOW, errorCode: null },
  { stage: "embedding", state: "completed", completedAt: NOW, errorCode: null },
  {
    stage: "publishing",
    state: "completed",
    completedAt: NOW,
    errorCode: null,
  },
  { stage: "ready", state: "completed", completedAt: NOW, errorCode: null },
];

export function document(
  overrides: Partial<DocumentSummary> = {},
): DocumentSummary {
  return {
    chunkCount: 0,
    documentId: "doc-1",
    errorCode: null,
    errorSummary: null,
    filename: "handbook.md",
    mediaType: "text/markdown",
    stage: "stored",
    status: "queued",
    updatedAt: NOW,
    ...overrides,
  };
}

export function documentDetail(
  overrides: Partial<DocumentDetail> = {},
): DocumentDetail {
  const summary = document(overrides);
  return {
    ...summary,
    normalizedPreview: null,
    revisionId: "rev_01JABCDEF",
    sourceContentHash:
      "749d926c8783c6f208220e2db03f4b070f63672fcb42ef068a1d11903e21750d",
    stages: DEFAULT_STAGES,
    ...overrides,
  };
}

export function markdownFile(name = "policy.md", sizeInBytes?: number): File {
  if (sizeInBytes === undefined) {
    return new File(["# Policy\nUse approved sources."], name, {
      type: "text/markdown",
    });
  }

  return new File([new Uint8Array(sizeInBytes)], name, {
    type: "text/markdown",
  });
}

interface PendingOperation {
  promise: Promise<void>;
  resolve: () => void;
}

export interface FakeKnowledgeClient extends KnowledgeClient {
  listCalls: number;
  listInputs: Array<{ cursor?: string; limit: number }>;
  listSignals: Array<AbortSignal | undefined>;
  uploadCalls: number;
  retryCalls: string[];
  deleteCalls: string[];
  uploadAborted: boolean;
  listOnce(items: DocumentSummary[]): FakeKnowledgeClient;
  withDocuments(items: DocumentSummary[]): FakeKnowledgeClient;
  withDetail(detail: DocumentDetail): FakeKnowledgeClient;
  withDuplicateUpload(): FakeKnowledgeClient;
  withUploadProblem(problem: unknown): FakeKnowledgeClient;
  withListProblem(problem: unknown): FakeKnowledgeClient;
  withDeleteProblem(problem: unknown): FakeKnowledgeClient;
  deferDelete(): FakeKnowledgeClient;
  deferList(): FakeKnowledgeClient;
  deferRetry(): FakeKnowledgeClient;
  deferUpload(): FakeKnowledgeClient;
  finishDelete(): void;
  finishList(): void;
  finishRetry(): void;
  finishUpload(): void;
}

export function fakeKnowledgeClient(): FakeKnowledgeClient {
  let documents: DocumentSummary[] = [];
  const detailById = new Map<string, DocumentDetail>();
  const listQueue: DocumentSummary[][] = [];
  let duplicateDocument: DocumentSummary | undefined;
  let uploadProblem: unknown;
  let listProblem: unknown;
  let deleteProblem: unknown;
  let pendingDelete: PendingOperation | undefined;
  let pendingList: PendingOperation | undefined;
  let pendingRetry: PendingOperation | undefined;
  let pendingUpload: PendingOperation | undefined;

  const api: FakeKnowledgeClient = {
    listCalls: 0,
    listInputs: [],
    listSignals: [],
    uploadCalls: 0,
    retryCalls: [],
    deleteCalls: [],
    uploadAborted: false,
    listOnce(items) {
      listQueue.push(items);
      return api;
    },
    withDocuments(items) {
      documents = items;
      return api;
    },
    withDetail(detail) {
      detailById.set(detail.documentId, detail);
      return api;
    },
    withDuplicateUpload() {
      duplicateDocument = document({
        documentId: "doc-existing",
        filename: "handbook.md",
        stage: "ready",
        status: "ready",
      });
      return api;
    },
    withUploadProblem(problem) {
      uploadProblem = problem;
      return api;
    },
    withListProblem(problem) {
      listProblem = problem;
      return api;
    },
    withDeleteProblem(problem) {
      deleteProblem = problem;
      return api;
    },
    deferDelete() {
      let resolve = () => {};
      const promise = new Promise<void>((finish) => {
        resolve = () => finish();
      });
      pendingDelete = { promise, resolve };
      return api;
    },
    deferList() {
      let resolve = () => {};
      const promise = new Promise<void>((finish) => {
        resolve = () => finish();
      });
      pendingList = { promise, resolve };
      return api;
    },
    deferRetry() {
      let resolve = () => {};
      const promise = new Promise<void>((finish) => {
        resolve = () => finish();
      });
      pendingRetry = { promise, resolve };
      return api;
    },
    deferUpload() {
      let resolve = () => {};
      const promise = new Promise<void>((finish) => {
        resolve = () => finish();
      });
      pendingUpload = { promise, resolve };
      return api;
    },
    finishDelete() {
      pendingDelete?.resolve();
    },
    finishList() {
      pendingList?.resolve();
    },
    finishRetry() {
      pendingRetry?.resolve();
    },
    finishUpload() {
      pendingUpload?.resolve();
    },
    async listDocuments(input): Promise<DocumentPage> {
      api.listCalls += 1;
      api.listInputs.push({ cursor: input.cursor, limit: input.limit });
      api.listSignals.push(input.signal);
      if (listQueue.length > 0) {
        documents = listQueue.shift() ?? documents;
      }
      const snapshot = [...documents];
      const pending = pendingList;
      pendingList = undefined;
      await pending?.promise;
      if (listProblem !== undefined) throw listProblem;
      return { items: snapshot, nextCursor: null };
    },
    async getDocument(documentId): Promise<DocumentDetail> {
      const existing = detailById.get(documentId);
      if (existing !== undefined) return existing;
      const summary = documents.find((item) => item.documentId === documentId);
      return documentDetail(summary ?? { documentId });
    },
    async uploadDocument(file, onProgress, signal): Promise<DocumentAccepted> {
      api.uploadCalls += 1;
      if (uploadProblem !== undefined) throw uploadProblem;

      return new Promise((resolve, reject) => {
        let timer = 0;
        let settled = false;
        const abort = () => {
          window.clearTimeout(timer);
          settled = true;
          api.uploadAborted = true;
          reject(new DOMException("Upload aborted", "AbortError"));
        };
        signal?.addEventListener("abort", abort, { once: true });
        onProgress(0.52);
        const complete = () => {
          if (settled || signal?.aborted === true) return;
          const uploaded =
            duplicateDocument ??
            document({
              documentId: `doc-${documents.length + 1}`,
              filename: file.name,
            });
          documents = [
            uploaded,
            ...documents.filter(
              (item) => item.documentId !== uploaded.documentId,
            ),
          ];
          signal?.removeEventListener("abort", abort);
          settled = true;
          resolve({
            document: uploaded,
            duplicate: duplicateDocument !== undefined,
            jobId: "job-1",
          });
        };
        const pending = pendingUpload;
        if (pending === undefined) {
          timer = window.setTimeout(complete, 20);
        } else {
          void pending.promise.then(() => {
            if (pendingUpload === pending) pendingUpload = undefined;
            complete();
          });
        }
      });
    },
    async retryDocument(documentId): Promise<DocumentAccepted> {
      api.retryCalls.push(documentId);
      await pendingRetry?.promise;
      const current =
        documents.find((item) => item.documentId === documentId) ??
        document({ documentId });
      const retried = {
        ...current,
        errorCode: null,
        errorSummary: null,
        stage: "stored" as const,
        status: "queued" as const,
        updatedAt: NOW,
      };
      documents = documents.map((item) =>
        item.documentId === documentId ? retried : item,
      );
      return { document: retried, duplicate: false, jobId: "job-retry" };
    },
    async deleteDocument(documentId): Promise<void> {
      api.deleteCalls.push(documentId);
      await pendingDelete?.promise;
      if (deleteProblem !== undefined) throw deleteProblem;
      documents = documents.filter((item) => item.documentId !== documentId);
      detailById.delete(documentId);
    },
    async createAnswer(): Promise<RetrievalAnswerResponse> {
      throw new Error("Task 8 owns answer behavior.");
    },
    async getCitation(): Promise<CitationPreview> {
      throw new Error("Task 8 owns citation behavior.");
    },
  };

  return api;
}
