import type {
  CitationPreview,
  DocumentAccepted,
  DocumentDetail,
  DocumentPage,
  DocumentStageSnapshot,
  DocumentSummary,
  KnowledgeClient,
  RetrievalAnswerRequest,
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

type RetrievalCitation = RetrievalAnswerResponse["citations"][number];

const SOURCE_HASH = `sha256:${"a".repeat(64)}`;
const CHUNK_HASH = `sha256:${"b".repeat(64)}`;

export function retrievalCitation(
  citationId = "citation-a",
  overrides: Partial<RetrievalCitation> = {},
): RetrievalCitation {
  return {
    citationId,
    evidenceLabel: "S1",
    chunkId: "chunk-a",
    logicalChunkId: "logical-a",
    source: {
      sourceId: "doc-a",
      sourceType: "document",
      revisionKind: "blob_version",
      revision: "rev-a",
      sourceContentHash: SOURCE_HASH,
      anchor: {
        type: "document",
        headingPath: ["退款政策"],
        page: 1,
        bbox: null,
        startOffset: 0,
        endOffset: 10,
      },
    },
    chunkContentHash: CHUNK_HASH,
    contentRole: "source",
    derivedFromChunkIds: null,
    ...overrides,
  };
}

export function answerResponse(
  overrides: Partial<RetrievalAnswerResponse> = {},
): RetrievalAnswerResponse {
  return {
    traceId: "trace-a",
    queryPlanId: "plan-a",
    contextSnapshotId: "snapshot-a",
    corpusVersion: "tapper-demo-v1",
    retrievalProfileId: "quick-hybrid-v1",
    degradedMode: false,
    degradationReasons: null,
    answer: "😀退款需要两人审批。",
    abstained: false,
    abstentionReason: null,
    claims: [
      {
        claimId: "claim-a",
        text: "😀退款需要两人审批。",
        answerStart: 0,
        answerEnd: 10,
        citationIds: ["citation-a"],
      },
    ],
    citations: [retrievalCitation()],
    ...overrides,
  };
}

export function citationPreview(
  overrides: Partial<CitationPreview> = {},
): CitationPreview {
  return {
    citationId: "citation-a",
    documentId: "doc-a",
    revisionId: "rev-a",
    filename: "policy.md",
    sourceContentHash: SOURCE_HASH,
    chunkContentHash: CHUNK_HASH,
    anchor: {
      type: "document",
      headingPath: ["退款政策"],
      page: 1,
      bbox: null,
      startOffset: 0,
      endOffset: 10,
    },
    prefix: "前文",
    quote: "退款需要两人审批。",
    suffix: "后文",
    ...overrides,
  };
}

interface PendingOperation<T = void> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
  ignoreAbort: boolean;
}

function pendingOperation<T>(ignoreAbort = false): PendingOperation<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((finish, fail) => {
    resolve = finish;
    reject = fail;
  });
  return { promise, resolve, reject, ignoreAbort };
}

export interface FakeKnowledgeClient extends KnowledgeClient {
  listCalls: number;
  listInputs: Array<{ cursor?: string; limit: number }>;
  listSignals: Array<AbortSignal | undefined>;
  uploadCalls: number;
  answerCalls: RetrievalAnswerRequest[];
  answerSignals: Array<AbortSignal | undefined>;
  citationCalls: string[];
  citationSignals: Array<AbortSignal | undefined>;
  retryCalls: string[];
  deleteCalls: string[];
  uploadAborted: boolean;
  answerAborted: boolean;
  citationAborted: boolean;
  listOnce(items: DocumentSummary[]): FakeKnowledgeClient;
  withDocuments(items: DocumentSummary[]): FakeKnowledgeClient;
  withDetail(detail: DocumentDetail): FakeKnowledgeClient;
  withDuplicateUpload(): FakeKnowledgeClient;
  withUploadProblem(problem: unknown): FakeKnowledgeClient;
  withListProblem(problem: unknown): FakeKnowledgeClient;
  withDeleteProblem(problem: unknown): FakeKnowledgeClient;
  withAnswer(response: RetrievalAnswerResponse): FakeKnowledgeClient;
  withAnswerProblem(problem: unknown): FakeKnowledgeClient;
  withCitation(preview: CitationPreview): FakeKnowledgeClient;
  withCitationProblem(problem: unknown): FakeKnowledgeClient;
  deferAnswer(options?: { ignoreAbort?: boolean }): FakeKnowledgeClient;
  deferCitation(
    citationId: string,
    options?: { ignoreAbort?: boolean },
  ): FakeKnowledgeClient;
  deferDelete(): FakeKnowledgeClient;
  deferList(): FakeKnowledgeClient;
  deferRetry(): FakeKnowledgeClient;
  deferUpload(): FakeKnowledgeClient;
  finishDelete(): void;
  finishList(): void;
  finishRetry(): void;
  finishUpload(): void;
  finishAnswer(response?: RetrievalAnswerResponse): void;
  finishAnswerAt(callIndex: number, response?: RetrievalAnswerResponse): void;
  finishCitation(citationId: string, preview?: CitationPreview): void;
}

export function fakeKnowledgeClient(): FakeKnowledgeClient {
  let documents: DocumentSummary[] = [];
  const detailById = new Map<string, DocumentDetail>();
  const listQueue: DocumentSummary[][] = [];
  let duplicateDocument: DocumentSummary | undefined;
  let uploadProblem: unknown;
  let listProblem: unknown;
  let deleteProblem: unknown;
  let answerResult = answerResponse();
  const citationById = new Map<string, CitationPreview>();
  let answerProblem: unknown;
  let citationProblem: unknown;
  const pendingAnswerQueue: Array<PendingOperation<RetrievalAnswerResponse>> =
    [];
  const answerOperations: Array<PendingOperation<RetrievalAnswerResponse>> = [];
  const pendingCitations = new Map<string, PendingOperation<CitationPreview>>();
  let pendingDelete: PendingOperation | undefined;
  let pendingList: PendingOperation | undefined;
  let pendingRetry: PendingOperation | undefined;
  let pendingUpload: PendingOperation | undefined;

  const api: FakeKnowledgeClient = {
    listCalls: 0,
    listInputs: [],
    listSignals: [],
    uploadCalls: 0,
    answerCalls: [],
    answerSignals: [],
    citationCalls: [],
    citationSignals: [],
    retryCalls: [],
    deleteCalls: [],
    uploadAborted: false,
    answerAborted: false,
    citationAborted: false,
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
    withAnswer(response) {
      answerResult = response;
      return api;
    },
    withAnswerProblem(problem) {
      answerProblem = problem;
      return api;
    },
    withCitation(preview) {
      citationById.set(preview.citationId, preview);
      return api;
    },
    withCitationProblem(problem) {
      citationProblem = problem;
      return api;
    },
    deferAnswer(options = {}) {
      pendingAnswerQueue.push(pendingOperation(options.ignoreAbort ?? false));
      return api;
    },
    deferCitation(citationId, options = {}) {
      pendingCitations.set(
        citationId,
        pendingOperation(options.ignoreAbort ?? false),
      );
      return api;
    },
    deferDelete() {
      pendingDelete = pendingOperation();
      return api;
    },
    deferList() {
      pendingList = pendingOperation();
      return api;
    },
    deferRetry() {
      pendingRetry = pendingOperation();
      return api;
    },
    deferUpload() {
      pendingUpload = pendingOperation();
      return api;
    },
    finishDelete() {
      pendingDelete?.resolve(undefined);
    },
    finishList() {
      pendingList?.resolve(undefined);
    },
    finishRetry() {
      pendingRetry?.resolve(undefined);
    },
    finishUpload() {
      pendingUpload?.resolve(undefined);
    },
    finishAnswer(response = answerResult) {
      answerOperations.at(-1)?.resolve(response);
    },
    finishAnswerAt(callIndex, response = answerResult) {
      answerOperations[callIndex]?.resolve(response);
    },
    finishCitation(citationId, preview = citationPreview({ citationId })) {
      pendingCitations.get(citationId)?.resolve(preview);
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
    async createAnswer(request, signal): Promise<RetrievalAnswerResponse> {
      api.answerCalls.push(request);
      api.answerSignals.push(signal);
      if (answerProblem !== undefined) throw answerProblem;
      const pending = pendingAnswerQueue.shift();
      if (pending === undefined) return answerResult;
      answerOperations.push(pending);
      const abort = () => {
        api.answerAborted = true;
        if (!pending.ignoreAbort) {
          pending.reject(new DOMException("Answer aborted", "AbortError"));
        }
      };
      signal?.addEventListener("abort", abort, { once: true });
      try {
        return await pending.promise;
      } finally {
        signal?.removeEventListener("abort", abort);
      }
    },
    async getCitation(citationId, signal): Promise<CitationPreview> {
      api.citationCalls.push(citationId);
      api.citationSignals.push(signal);
      if (citationProblem !== undefined) throw citationProblem;
      const pending = pendingCitations.get(citationId);
      if (pending === undefined) {
        return citationById.get(citationId) ?? citationPreview({ citationId });
      }
      const abort = () => {
        api.citationAborted = true;
        if (!pending.ignoreAbort) {
          pending.reject(new DOMException("Citation aborted", "AbortError"));
        }
      };
      signal?.addEventListener("abort", abort, { once: true });
      try {
        return await pending.promise;
      } finally {
        signal?.removeEventListener("abort", abort);
        if (pendingCitations.get(citationId) === pending) {
          pendingCitations.delete(citationId);
        }
      }
    },
  };

  return api;
}
