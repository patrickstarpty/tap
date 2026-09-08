import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";

import { KnowledgeClientError } from "./client";
import type {
  CitationPreview,
  DocumentAccepted,
  DocumentDetail,
  DocumentPage,
  DocumentSummary,
  KnowledgeClient,
  RetrievalAnswerRequest,
  RetrievalAnswerResponse,
  SourceAccepted,
  SourcePage,
  SourceRetryRequest,
} from "./types";

const DOCUMENT_LIMIT = 50;
const POLL_INTERVAL_MS = 2_000;
const TERMINAL_CACHE_MS = 30_000;

interface ReceiptOverlay {
  document: DocumentSummary;
}

interface PendingDeleteOverlay {
  document: DocumentSummary | undefined;
  state: "pending";
}

interface CommittedDeleteOverlay {
  document: DocumentSummary | undefined;
  state: "committed";
}

type DeleteOverlay = PendingDeleteOverlay | CommittedDeleteOverlay;

interface ConsistencyOverlays {
  deletions: Map<string, DeleteOverlay>;
  receipts: Map<string, ReceiptOverlay>;
}

const consistencyOverlays = new WeakMap<
  QueryClient,
  Map<string, ConsistencyOverlays>
>();

const KnowledgeClientContext = createContext<KnowledgeClient | null>(null);

export function KnowledgeClientProvider({
  children,
  client,
}: {
  children: ReactNode;
  client: KnowledgeClient | null;
}) {
  return (
    <KnowledgeClientContext.Provider value={client}>
      {children}
    </KnowledgeClientContext.Provider>
  );
}

export function useKnowledgeClient(): KnowledgeClient {
  const client = useContext(KnowledgeClientContext);
  if (client === null) {
    throw new Error("KnowledgeClientProvider is required.");
  }
  return client;
}

function useProjectKnowledgeClient(projectId: string): KnowledgeClient {
  const client = useKnowledgeClient();
  if (client.projectId !== projectId) {
    throw new Error("Knowledge client does not match the requested project.");
  }
  return client;
}

export const knowledgeKeys = {
  all: ["knowledge"] as const,
  sources: (projectId: string | null) =>
    ["knowledge", projectId, "sources"] as const,
  source: (projectId: string, sourceId: string) =>
    ["knowledge", projectId, "source", sourceId] as const,
  documents: (projectId: string | null) =>
    ["knowledge", projectId, "documents"] as const,
  detail: (projectId: string, documentId: string) =>
    ["knowledge", projectId, "documents", documentId] as const,
  citations: (projectId: string) =>
    ["knowledge", projectId, "citations"] as const,
  citation: (projectId: string, citationId: string, generation = 0) =>
    ["knowledge", projectId, "citations", citationId, generation] as const,
};

export function useSourceListQuery(projectId: string | null) {
  const client = useContext(KnowledgeClientContext);
  return useQuery({
    queryKey: knowledgeKeys.sources(projectId),
    enabled:
      projectId !== null && client !== null && client.projectId === projectId,
    queryFn: ({ signal }) => {
      if (
        projectId === null ||
        client === null ||
        client.projectId !== projectId
      )
        throw new Error("A matching project client is required.");
      return client.listSources({ limit: DOCUMENT_LIMIT, signal });
    },
    refetchInterval: (query) =>
      query.state.data?.items.some(
        (source) =>
          source.readyCount + source.failedCount < source.documentCount,
      )
        ? POLL_INTERVAL_MS
        : false,
    staleTime: TERMINAL_CACHE_MS,
  });
}

export function useSourceDetailQuery(
  projectId: string,
  sourceId: string | null,
) {
  const client = useProjectKnowledgeClient(projectId);
  return useQuery({
    queryKey: knowledgeKeys.source(projectId, sourceId ?? "none"),
    enabled: sourceId !== null,
    queryFn: ({ signal }) => client.getSource(sourceId ?? "", signal),
    staleTime: 0,
    refetchInterval: (query) =>
      query.state.data?.documents.items.some((item) => isNonTerminal(item))
        ? POLL_INTERVAL_MS
        : false,
  });
}

async function settleSourceReceipt(
  queryClient: QueryClient,
  projectId: string,
  receipt: SourceAccepted,
) {
  await queryClient.cancelQueries({
    queryKey: knowledgeKeys.sources(projectId),
    exact: true,
  });
  queryClient.setQueryData<SourcePage>(
    knowledgeKeys.sources(projectId),
    (page) => ({
      items: [
        receipt.source,
        ...(page?.items ?? []).filter(
          (source) => source.sourceId !== receipt.source.sourceId,
        ),
      ],
      nextCursor: page?.nextCursor ?? null,
    }),
  );
  void queryClient.invalidateQueries({
    queryKey: knowledgeKeys.source(projectId, receipt.source.sourceId),
    exact: true,
  });
  void queryClient.invalidateQueries({
    queryKey: knowledgeKeys.sources(projectId),
    exact: true,
  });
}

export function useUploadSourceMutation(projectId: string) {
  const client = useProjectKnowledgeClient(projectId);
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["knowledge", projectId, "source-upload"],
    retry: false,
    mutationFn: ({
      file,
      onProgress,
      signal,
      idempotencyKey,
    }: UploadDocumentCommand & { idempotencyKey: string }) =>
      client.uploadSource(file, onProgress, signal, idempotencyKey),
    onSuccess: (receipt) =>
      settleSourceReceipt(queryClient, projectId, receipt),
  });
}

export function useRetrySourceMutation(projectId: string) {
  const client = useProjectKnowledgeClient(projectId);
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["knowledge", projectId, "source-retry"],
    retry: false,
    mutationFn: ({
      sourceId,
      request,
      idempotencyKey,
    }: {
      sourceId: string;
      request: SourceRetryRequest;
      idempotencyKey: string;
    }) => client.retrySource(sourceId, request, idempotencyKey),
    onSuccess: (receipt) =>
      settleSourceReceipt(queryClient, projectId, receipt),
  });
}

export function useDeleteSourceMutation(projectId: string) {
  const client = useProjectKnowledgeClient(projectId);
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["knowledge", projectId, "source-delete"],
    retry: false,
    mutationFn: ({
      sourceId,
      idempotencyKey,
    }: {
      sourceId: string;
      idempotencyKey: string;
    }) => client.deleteSource(sourceId, idempotencyKey),
    onSuccess: async (_result, { sourceId }) => {
      await queryClient.cancelQueries({
        queryKey: knowledgeKeys.sources(projectId),
        exact: true,
      });
      queryClient.setQueryData<SourcePage>(
        knowledgeKeys.sources(projectId),
        (page) =>
          page === undefined
            ? page
            : {
                ...page,
                items: page.items.filter(
                  (source) => source.sourceId !== sourceId,
                ),
              },
      );
      queryClient.removeQueries({
        queryKey: knowledgeKeys.source(projectId, sourceId),
        exact: true,
      });
      void queryClient.invalidateQueries({
        queryKey: knowledgeKeys.sources(projectId),
        exact: true,
      });
    },
  });
}

function isNonTerminal(document: DocumentSummary): boolean {
  return (
    document.status === "queued" ||
    document.status === "processing" ||
    document.status === "deleting"
  );
}

function overlaysFor(
  queryClient: QueryClient,
  projectId: string,
): ConsistencyOverlays {
  let projects = consistencyOverlays.get(queryClient);
  if (projects === undefined) {
    projects = new Map();
    consistencyOverlays.set(queryClient, projects);
  }
  const current = projects.get(projectId);
  if (current !== undefined) return current;
  const created: ConsistencyOverlays = {
    deletions: new Map(),
    receipts: new Map(),
  };
  projects.set(projectId, created);
  return created;
}

function isNotOlder(candidate: string, baseline: string): boolean {
  const candidateTime = Date.parse(candidate);
  const baselineTime = Date.parse(baseline);
  if (Number.isNaN(candidateTime) || Number.isNaN(baselineTime)) {
    return candidate === baseline;
  }
  return candidateTime >= baselineTime;
}

function mergeConsistencyOverlays(
  queryClient: QueryClient,
  projectId: string,
  page: DocumentPage,
): DocumentPage {
  const overlays = overlaysFor(queryClient, projectId);
  const items = [...page.items];

  for (const [documentId, overlay] of overlays.receipts) {
    const index = items.findIndex((item) => item.documentId === documentId);
    const serverDocument = index === -1 ? undefined : items[index];
    if (
      serverDocument !== undefined &&
      isNotOlder(serverDocument.updatedAt, overlay.document.updatedAt)
    ) {
      overlays.receipts.delete(documentId);
      continue;
    }
    if (index === -1) items.unshift(overlay.document);
    else items[index] = overlay.document;
  }

  for (const [documentId, overlay] of overlays.deletions) {
    const index = items.findIndex((item) => item.documentId === documentId);
    if (overlay.state === "committed") {
      if (index === -1) {
        overlays.deletions.delete(documentId);
      } else {
        items.splice(index, 1);
      }
      continue;
    }
    const visibleDocument = overlay.document ?? items[index];
    if (visibleDocument === undefined) continue;
    const deletingDocument: DocumentSummary = {
      ...visibleDocument,
      status: "deleting",
    };
    if (index === -1) items.unshift(deletingDocument);
    else items[index] = deletingDocument;
  }

  return { ...page, items };
}

function rememberReceipt(
  queryClient: QueryClient,
  projectId: string,
  document: DocumentSummary,
): void {
  overlaysFor(queryClient, projectId).receipts.set(document.documentId, {
    document,
  });
}

function rememberPendingDelete(
  queryClient: QueryClient,
  projectId: string,
  documentId: string,
  document: DocumentSummary | undefined,
): void {
  overlaysFor(queryClient, projectId).deletions.set(documentId, {
    document,
    state: "pending",
  });
}

function commitDelete(
  queryClient: QueryClient,
  projectId: string,
  documentId: string,
): void {
  const overlays = overlaysFor(queryClient, projectId);
  const pending = overlays.deletions.get(documentId);
  overlays.deletions.set(documentId, {
    document: pending?.document,
    state: "committed",
  });
  overlays.receipts.delete(documentId);
}

function forgetDelete(
  queryClient: QueryClient,
  projectId: string,
  documentId: string,
): void {
  overlaysFor(queryClient, projectId).deletions.delete(documentId);
}

async function invalidateChangedDetails(
  queryClient: QueryClient,
  projectId: string,
  previousPage: DocumentPage | undefined,
  nextPage: DocumentPage,
): Promise<void> {
  if (previousPage === undefined) return;
  const previousById = new Map(
    previousPage.items.map((document) => [document.documentId, document]),
  );
  await Promise.all(
    nextPage.items.map(async (document) => {
      const previous = previousById.get(document.documentId);
      if (previous === undefined || previous.updatedAt === document.updatedAt) {
        return;
      }
      await queryClient.invalidateQueries({
        queryKey: knowledgeKeys.detail(projectId, document.documentId),
        exact: true,
        refetchType: "active",
      });
    }),
  );
}

function upsertDocument(
  page: DocumentPage | undefined,
  document: DocumentSummary,
): DocumentPage {
  if (page === undefined) {
    return { items: [document], nextCursor: null };
  }
  const existingIndex = page.items.findIndex(
    (item) => item.documentId === document.documentId,
  );
  const items = [...page.items];
  if (existingIndex === -1) {
    items.unshift(document);
  } else {
    items[existingIndex] = document;
  }
  return { ...page, items };
}

function setReceipt(
  queryClient: QueryClient,
  projectId: string,
  receipt: DocumentAccepted,
): void {
  rememberReceipt(queryClient, projectId, receipt.document);
  queryClient.setQueriesData<DocumentPage>(
    { queryKey: knowledgeKeys.documents(projectId), exact: true },
    (page) => upsertDocument(page, receipt.document),
  );
}

async function settleReceipt(
  queryClient: QueryClient,
  projectId: string,
  receipt: DocumentAccepted,
): Promise<void> {
  await queryClient.cancelQueries({
    queryKey: knowledgeKeys.documents(projectId),
    exact: true,
  });
  setReceipt(queryClient, projectId, receipt);
  void queryClient.invalidateQueries({
    queryKey: knowledgeKeys.detail(projectId, receipt.document.documentId),
    exact: true,
    refetchType: "active",
  });
  void queryClient.invalidateQueries({
    queryKey: knowledgeKeys.documents(projectId),
    exact: true,
    refetchType: "active",
  });
}

export function useDocumentListQuery(
  projectId: string | null,
  { pollIntervalMs = POLL_INTERVAL_MS }: { pollIntervalMs?: number } = {},
) {
  const client = useContext(KnowledgeClientContext);
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: knowledgeKeys.documents(projectId),
    enabled:
      projectId !== null && client !== null && client.projectId === projectId,
    queryFn: async ({ signal }) => {
      if (
        projectId === null ||
        client === null ||
        client.projectId !== projectId
      ) {
        throw new Error(
          "A matching project client is required for Knowledge requests.",
        );
      }
      const previousPage = queryClient.getQueryData<DocumentPage>(
        knowledgeKeys.documents(projectId),
      );
      const nextPage = mergeConsistencyOverlays(
        queryClient,
        projectId,
        await client.listDocuments({ limit: DOCUMENT_LIMIT, signal }),
      );
      await invalidateChangedDetails(
        queryClient,
        projectId,
        previousPage,
        nextPage,
      );
      return nextPage;
    },
    refetchInterval: (query) =>
      query.state.data?.items.some(isNonTerminal) === true
        ? pollIntervalMs
        : false,
    refetchIntervalInBackground: true,
    staleTime: TERMINAL_CACHE_MS,
  });
}

export function useDocumentDetailQuery(
  projectId: string,
  documentId: string | null,
) {
  const client = useProjectKnowledgeClient(projectId);
  return useQuery({
    queryKey: knowledgeKeys.detail(projectId, documentId ?? "none"),
    queryFn: ({ signal }) => client.getDocument(documentId ?? "", signal),
    enabled: documentId !== null,
    staleTime: 5_000,
  });
}

export interface UploadDocumentCommand {
  file: File;
  onProgress: (ratio: number) => void;
  signal?: AbortSignal;
}

export function useUploadDocumentMutation(projectId: string) {
  const client = useProjectKnowledgeClient(projectId);
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["knowledge", projectId, "upload"],
    mutationFn: ({ file, onProgress, signal }: UploadDocumentCommand) =>
      client.uploadDocument(file, onProgress, signal),
    onSuccess: (receipt) => settleReceipt(queryClient, projectId, receipt),
  });
}

export function useRetryDocumentMutation(projectId: string) {
  const client = useProjectKnowledgeClient(projectId);
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["knowledge", projectId, "retry"],
    mutationFn: (documentId: string) => client.retryDocument(documentId),
    onSuccess: (receipt) => settleReceipt(queryClient, projectId, receipt),
  });
}

export interface CreateAnswerCommand {
  request: RetrievalAnswerRequest;
  signal?: AbortSignal;
}

export function useCreateAnswerMutation(projectId: string) {
  const client = useProjectKnowledgeClient(projectId);
  const queryClient = useQueryClient();
  return useMutation<RetrievalAnswerResponse, Error, CreateAnswerCommand>({
    mutationKey: ["knowledge", projectId, "answer"],
    mutationFn: ({ request, signal }) => client.createAnswer(request, signal),
    onError: async (error) => {
      if (
        error instanceof KnowledgeClientError &&
        error.code === "document-state-changed"
      ) {
        await queryClient.invalidateQueries({
          queryKey: knowledgeKeys.documents(projectId),
          exact: true,
          refetchType: "active",
        });
      }
    },
  });
}

export function useCitationQuery(
  projectId: string,
  citationId: string | null,
  generation = 0,
) {
  const client = useProjectKnowledgeClient(projectId);
  return useQuery<CitationPreview>({
    queryKey: knowledgeKeys.citation(
      projectId,
      citationId ?? "none",
      generation,
    ),
    queryFn: ({ signal }) => client.getCitation(citationId ?? "", signal),
    enabled: citationId !== null,
    staleTime: 0,
    retry: false,
  });
}

interface DeleteContext {
  detail: DocumentDetail | undefined;
  pages: Array<[readonly unknown[], DocumentPage | undefined]>;
}

export function useDeleteDocumentMutation(projectId: string) {
  const client = useProjectKnowledgeClient(projectId);
  const queryClient = useQueryClient();
  return useMutation<void, Error, string, DeleteContext>({
    mutationKey: ["knowledge", projectId, "delete"],
    mutationFn: (documentId) => client.deleteDocument(documentId),
    onMutate: async (documentId) => {
      await queryClient.cancelQueries({
        queryKey: knowledgeKeys.documents(projectId),
        exact: true,
      });
      const pages = queryClient.getQueriesData<DocumentPage>({
        queryKey: knowledgeKeys.documents(projectId),
        exact: true,
      });
      const detail = queryClient.getQueryData<DocumentDetail>(
        knowledgeKeys.detail(projectId, documentId),
      );
      const deletingDocument = pages
        .flatMap(([, page]) => page?.items ?? [])
        .find((item) => item.documentId === documentId);
      rememberPendingDelete(
        queryClient,
        projectId,
        documentId,
        deletingDocument,
      );
      queryClient.setQueriesData<DocumentPage>(
        { queryKey: knowledgeKeys.documents(projectId), exact: true },
        (page) =>
          page === undefined
            ? page
            : {
                ...page,
                items: page.items.map((item) =>
                  item.documentId === documentId
                    ? { ...item, status: "deleting" }
                    : item,
                ),
              },
      );
      if (detail !== undefined) {
        queryClient.setQueryData(knowledgeKeys.detail(projectId, documentId), {
          ...detail,
          status: "deleting",
        });
      }
      return { detail, pages };
    },
    onError: (_error, documentId, context) => {
      forgetDelete(queryClient, projectId, documentId);
      for (const [key, page] of context?.pages ?? []) {
        queryClient.setQueryData(key, page);
      }
      if (context?.detail !== undefined) {
        queryClient.setQueryData(
          knowledgeKeys.detail(projectId, documentId),
          context.detail,
        );
      }
    },
    onSuccess: async (_data, documentId) => {
      commitDelete(queryClient, projectId, documentId);
      await queryClient.cancelQueries({
        queryKey: knowledgeKeys.documents(projectId),
        exact: true,
      });
      queryClient.setQueriesData<DocumentPage>(
        { queryKey: knowledgeKeys.documents(projectId), exact: true },
        (page) =>
          page === undefined
            ? page
            : {
                ...page,
                items: page.items.filter(
                  (item) => item.documentId !== documentId,
                ),
              },
      );
      queryClient.removeQueries({
        queryKey: knowledgeKeys.detail(projectId, documentId),
        exact: true,
      });
    },
    onSettled: () =>
      queryClient.invalidateQueries({
        queryKey: knowledgeKeys.documents(projectId),
        exact: true,
        refetchType: "active",
      }),
  });
}
