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

const consistencyOverlays = new WeakMap<QueryClient, ConsistencyOverlays>();

const KnowledgeClientContext = createContext<KnowledgeClient | null>(null);

export function KnowledgeClientProvider({
  children,
  client,
}: {
  children: ReactNode;
  client: KnowledgeClient;
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

export const knowledgeKeys = {
  all: ["knowledge"] as const,
  documents: () => ["knowledge", "documents"] as const,
  detail: (documentId: string) =>
    ["knowledge", "documents", documentId] as const,
  citations: () => ["knowledge", "citations"] as const,
  citation: (citationId: string, generation = 0) =>
    ["knowledge", "citations", citationId, generation] as const,
};

function isNonTerminal(document: DocumentSummary): boolean {
  return (
    document.status === "queued" ||
    document.status === "processing" ||
    document.status === "deleting"
  );
}

function overlaysFor(queryClient: QueryClient): ConsistencyOverlays {
  const current = consistencyOverlays.get(queryClient);
  if (current !== undefined) return current;
  const created: ConsistencyOverlays = {
    deletions: new Map(),
    receipts: new Map(),
  };
  consistencyOverlays.set(queryClient, created);
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
  page: DocumentPage,
): DocumentPage {
  const overlays = overlaysFor(queryClient);
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
  document: DocumentSummary,
): void {
  overlaysFor(queryClient).receipts.set(document.documentId, {
    document,
  });
}

function rememberPendingDelete(
  queryClient: QueryClient,
  documentId: string,
  document: DocumentSummary | undefined,
): void {
  overlaysFor(queryClient).deletions.set(documentId, {
    document,
    state: "pending",
  });
}

function commitDelete(queryClient: QueryClient, documentId: string): void {
  const overlays = overlaysFor(queryClient);
  const pending = overlays.deletions.get(documentId);
  overlays.deletions.set(documentId, {
    document: pending?.document,
    state: "committed",
  });
  overlays.receipts.delete(documentId);
}

function forgetDelete(queryClient: QueryClient, documentId: string): void {
  overlaysFor(queryClient).deletions.delete(documentId);
}

async function invalidateChangedDetails(
  queryClient: QueryClient,
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
        queryKey: knowledgeKeys.detail(document.documentId),
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

function setReceipt(queryClient: QueryClient, receipt: DocumentAccepted): void {
  rememberReceipt(queryClient, receipt.document);
  queryClient.setQueriesData<DocumentPage>(
    { queryKey: knowledgeKeys.documents(), exact: true },
    (page) => upsertDocument(page, receipt.document),
  );
}

async function settleReceipt(
  queryClient: QueryClient,
  receipt: DocumentAccepted,
): Promise<void> {
  await queryClient.cancelQueries({
    queryKey: knowledgeKeys.documents(),
    exact: true,
  });
  setReceipt(queryClient, receipt);
  void queryClient.invalidateQueries({
    queryKey: knowledgeKeys.detail(receipt.document.documentId),
    exact: true,
    refetchType: "active",
  });
  void queryClient.invalidateQueries({
    queryKey: knowledgeKeys.documents(),
    exact: true,
    refetchType: "active",
  });
}

export function useDocumentListQuery({
  pollIntervalMs = POLL_INTERVAL_MS,
}: { pollIntervalMs?: number } = {}) {
  const client = useKnowledgeClient();
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: knowledgeKeys.documents(),
    queryFn: async ({ signal }) => {
      const previousPage = queryClient.getQueryData<DocumentPage>(
        knowledgeKeys.documents(),
      );
      const nextPage = mergeConsistencyOverlays(
        queryClient,
        await client.listDocuments({ limit: DOCUMENT_LIMIT, signal }),
      );
      await invalidateChangedDetails(queryClient, previousPage, nextPage);
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

export function useDocumentDetailQuery(documentId: string | null) {
  const client = useKnowledgeClient();
  return useQuery({
    queryKey: knowledgeKeys.detail(documentId ?? "none"),
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

export function useUploadDocumentMutation() {
  const client = useKnowledgeClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, onProgress, signal }: UploadDocumentCommand) =>
      client.uploadDocument(file, onProgress, signal),
    onSuccess: (receipt) => settleReceipt(queryClient, receipt),
  });
}

export function useRetryDocumentMutation() {
  const client = useKnowledgeClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) => client.retryDocument(documentId),
    onSuccess: (receipt) => settleReceipt(queryClient, receipt),
  });
}

export interface CreateAnswerCommand {
  request: RetrievalAnswerRequest;
  signal?: AbortSignal;
}

export function useCreateAnswerMutation() {
  const client = useKnowledgeClient();
  const queryClient = useQueryClient();
  return useMutation<RetrievalAnswerResponse, Error, CreateAnswerCommand>({
    mutationFn: ({ request, signal }) => client.createAnswer(request, signal),
    onError: async (error) => {
      if (
        error instanceof KnowledgeClientError &&
        error.code === "document-state-changed"
      ) {
        await queryClient.invalidateQueries({
          queryKey: knowledgeKeys.documents(),
          exact: true,
          refetchType: "active",
        });
      }
    },
  });
}

export function useCitationQuery(citationId: string | null, generation = 0) {
  const client = useKnowledgeClient();
  return useQuery<CitationPreview>({
    queryKey: knowledgeKeys.citation(citationId ?? "none", generation),
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

export function useDeleteDocumentMutation() {
  const client = useKnowledgeClient();
  const queryClient = useQueryClient();
  return useMutation<void, Error, string, DeleteContext>({
    mutationFn: (documentId) => client.deleteDocument(documentId),
    onMutate: async (documentId) => {
      await queryClient.cancelQueries({
        queryKey: knowledgeKeys.documents(),
        exact: true,
      });
      const pages = queryClient.getQueriesData<DocumentPage>({
        queryKey: knowledgeKeys.documents(),
        exact: true,
      });
      const detail = queryClient.getQueryData<DocumentDetail>(
        knowledgeKeys.detail(documentId),
      );
      const deletingDocument = pages
        .flatMap(([, page]) => page?.items ?? [])
        .find((item) => item.documentId === documentId);
      rememberPendingDelete(queryClient, documentId, deletingDocument);
      queryClient.setQueriesData<DocumentPage>(
        { queryKey: knowledgeKeys.documents(), exact: true },
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
        queryClient.setQueryData(knowledgeKeys.detail(documentId), {
          ...detail,
          status: "deleting",
        });
      }
      return { detail, pages };
    },
    onError: (_error, documentId, context) => {
      forgetDelete(queryClient, documentId);
      for (const [key, page] of context?.pages ?? []) {
        queryClient.setQueryData(key, page);
      }
      if (context?.detail !== undefined) {
        queryClient.setQueryData(
          knowledgeKeys.detail(documentId),
          context.detail,
        );
      }
    },
    onSuccess: async (_data, documentId) => {
      commitDelete(queryClient, documentId);
      await queryClient.cancelQueries({
        queryKey: knowledgeKeys.documents(),
        exact: true,
      });
      queryClient.setQueriesData<DocumentPage>(
        { queryKey: knowledgeKeys.documents(), exact: true },
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
        queryKey: knowledgeKeys.detail(documentId),
        exact: true,
      });
    },
    onSettled: () =>
      queryClient.invalidateQueries({
        queryKey: knowledgeKeys.documents(),
        exact: true,
        refetchType: "active",
      }),
  });
}
