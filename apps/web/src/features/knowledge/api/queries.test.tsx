import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { createTestQueryClient } from "../../../shared/testing/renderApp";
import {
  answerResponse,
  citationPreview,
  document,
  documentDetail,
  fakeKnowledgeClient,
} from "../testing/fakeKnowledgeClient";
import { KnowledgeClientError } from "./client";
import {
  KnowledgeClientProvider,
  knowledgeKeys,
  useCitationQuery,
  useCreateAnswerMutation,
  useDocumentListQuery,
  useDeleteDocumentMutation,
  useRetryDocumentMutation,
  useUploadDocumentMutation,
} from "./queries";
import type {
  DocumentPage,
  KnowledgeClient,
  RetrievalAnswerRequest,
} from "./types";

const POLL_INTERVAL_MS = 2_000;
const OVERLAY_REGRESSION_TIME = new Date("2026-08-28T07:32:01Z");

async function advanceOnePoll(): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
  });
}

function cachedDocument(
  queryClient: ReturnType<typeof createTestQueryClient>,
  documentId: string,
) {
  return queryClient
    .getQueryData<DocumentPage>(knowledgeKeys.documents())
    ?.items.find((item) => item.documentId === documentId);
}

describe("knowledge query mutations", () => {
  it("polls every two seconds only until a nonterminal list settles", async () => {
    vi.useFakeTimers();
    const api = fakeKnowledgeClient()
      .listOnce([document({ status: "processing", stage: "embedding" })])
      .listOnce([document({ status: "failed", stage: "embedding" })]);
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useDocumentListQuery(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>
          <KnowledgeClientProvider client={api}>
            {children}
          </KnowledgeClientProvider>
        </QueryClientProvider>
      ),
    });
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(result.current.data?.items[0]?.status).toBe("processing");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
      await vi.runOnlyPendingTimersAsync();
    });
    expect(api.listCalls).toBe(2);
    expect(
      queryClient.getQueryData<{ items: Array<{ status: string }> }>(
        knowledgeKeys.documents(),
      )?.items[0]?.status,
    ).toBe("failed");
    expect(result.current.data?.items[0]?.status).toBe("failed");

    await act(async () => vi.advanceTimersByTimeAsync(4_000));
    expect(api.listCalls).toBe(2);
  });

  it("passes TanStack cancellation through to an in-flight list request", async () => {
    const api = fakeKnowledgeClient().deferList();
    const queryClient = createTestQueryClient();
    const { unmount } = renderHook(() => useDocumentListQuery(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>
          <KnowledgeClientProvider client={api}>
            {children}
          </KnowledgeClientProvider>
        </QueryClientProvider>
      ),
    });
    await waitFor(() => expect(api.listCalls).toBe(1));
    expect(api.listInputs).toEqual([{ cursor: undefined, limit: 50 }]);

    unmount();

    expect(api.listSignals[0]?.aborted).toBe(true);
    api.finishList();
  });

  it("invalidates cached detail when the list reports a newer document snapshot", async () => {
    const existing = document({
      documentId: "doc-detail",
      updatedAt: "2026-08-28T07:00:00Z",
    });
    const api = fakeKnowledgeClient().listOnce([
      document({
        ...existing,
        status: "ready",
        stage: "ready",
        updatedAt: "2026-08-28T08:00:00Z",
      }),
    ]);
    const queryClient = createTestQueryClient();
    queryClient.setQueryData(knowledgeKeys.documents(), {
      items: [existing],
      nextCursor: null,
    });
    queryClient.setQueryData(
      knowledgeKeys.detail("doc-detail"),
      documentDetail(existing),
    );
    const { result } = renderHook(() => useDocumentListQuery(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>
          <KnowledgeClientProvider client={api}>
            {children}
          </KnowledgeClientProvider>
        </QueryClientProvider>
      ),
    });

    await act(async () => result.current.refetch());

    expect(
      queryClient.getQueryState(knowledgeKeys.detail("doc-detail"))
        ?.isInvalidated,
    ).toBe(true);
  });

  it("upserts the canonical duplicate receipt before invalidating the list", async () => {
    const api = fakeKnowledgeClient()
      .withDocuments([
        document({
          documentId: "doc-existing",
          filename: "old-name.md",
          status: "ready",
          stage: "ready",
        }),
      ])
      .withDuplicateUpload();
    const queryClient = createTestQueryClient();
    queryClient.setQueryData(knowledgeKeys.documents(), {
      items: [
        document({
          documentId: "doc-existing",
          filename: "old-name.md",
          status: "ready",
          stage: "ready",
        }),
      ],
      nextCursor: null,
    });
    queryClient.setQueryData(
      knowledgeKeys.detail("doc-existing"),
      documentDetail({ documentId: "doc-existing", filename: "old-name.md" }),
    );
    const { result } = renderHook(() => useUploadDocumentMutation(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>
          <KnowledgeClientProvider client={api}>
            {children}
          </KnowledgeClientProvider>
        </QueryClientProvider>
      ),
    });

    await act(async () =>
      result.current.mutateAsync({
        file: new File(["# Canonical"], "canonical.md"),
        onProgress: () => undefined,
      }),
    );

    expect(
      queryClient.getQueryData<{
        items: Array<{ documentId: string; filename: string }>;
      }>(knowledgeKeys.documents()),
    ).toMatchObject({
      items: [{ documentId: "doc-existing", filename: "handbook.md" }],
    });
    expect(
      queryClient.getQueryState(knowledgeKeys.detail("doc-existing"))
        ?.isInvalidated,
    ).toBe(true);
  });

  it("does not let an older cancelled list response overwrite a 202 receipt", async () => {
    const api = fakeKnowledgeClient().deferList();
    const queryClient = createTestQueryClient();
    const { result } = renderHook(
      () => ({
        list: useDocumentListQuery(),
        upload: useUploadDocumentMutation(),
      }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={queryClient}>
            <KnowledgeClientProvider client={api}>
              {children}
            </KnowledgeClientProvider>
          </QueryClientProvider>
        ),
      },
    );
    await waitFor(() => expect(api.listCalls).toBe(1));

    await act(async () =>
      result.current.upload.mutateAsync({
        file: new File(["# New"], "new.md"),
        onProgress: () => undefined,
      }),
    );
    expect(
      queryClient.getQueryData<{ items: Array<{ filename: string }> }>(
        knowledgeKeys.documents(),
      ),
    ).toMatchObject({ items: [{ filename: "new.md" }] });

    api.finishList();
    await act(async () => Promise.resolve());
    expect(
      queryClient.getQueryData<{ items: Array<{ filename: string }> }>(
        knowledgeKeys.documents(),
      ),
    ).toMatchObject({ items: [{ filename: "new.md" }] });
  });

  it("keeps an accepted upload across repeated stale polls beyond 121 seconds and clears after catch-up", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-28T07:30:00Z"));
    const background = document({
      documentId: "doc-background",
      filename: "background.md",
      status: "processing",
      stage: "embedding",
    });
    const accepted = document({
      documentId: "doc-2",
      filename: "new-source.md",
    });
    const authoritative = document({
      ...accepted,
      chunkCount: 7,
      status: "processing",
      stage: "embedding",
      updatedAt: "2026-08-28T08:00:00Z",
    });
    const staleAfterCatchUp = document({
      documentId: accepted.documentId,
      filename: "stale-after-catch-up.md",
      updatedAt: "2026-08-28T07:00:00Z",
    });
    const api = fakeKnowledgeClient()
      .listOnce([background])
      .listOnce([background])
      .listOnce([background])
      .listOnce([background])
      .listOnce([authoritative, background])
      .listOnce([staleAfterCatchUp, background]);
    const queryClient = createTestQueryClient();
    const { result } = renderHook(
      () => ({
        list: useDocumentListQuery(),
        upload: useUploadDocumentMutation(),
      }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={queryClient}>
            <KnowledgeClientProvider client={api}>
              {children}
            </KnowledgeClientProvider>
          </QueryClientProvider>
        ),
      },
    );
    await act(async () => vi.advanceTimersByTimeAsync(0));

    let uploadPromise!: Promise<unknown>;
    act(() => {
      uploadPromise = result.current.upload.mutateAsync({
        file: new File(["# New"], accepted.filename),
        onProgress: () => undefined,
      });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20);
      await uploadPromise;
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(api.listCalls).toBe(2);
    expect(cachedDocument(queryClient, accepted.documentId)?.filename).toBe(
      accepted.filename,
    );

    vi.setSystemTime(OVERLAY_REGRESSION_TIME);
    await advanceOnePoll();
    await advanceOnePoll();
    expect(api.listCalls).toBe(4);
    expect(cachedDocument(queryClient, accepted.documentId)?.filename).toBe(
      accepted.filename,
    );

    await advanceOnePoll();
    expect(cachedDocument(queryClient, accepted.documentId)).toMatchObject({
      chunkCount: 7,
      updatedAt: "2026-08-28T08:00:00Z",
    });
    await advanceOnePoll();
    expect(cachedDocument(queryClient, accepted.documentId)?.filename).toBe(
      staleAfterCatchUp.filename,
    );
  });

  it("keeps a canonical duplicate receipt across repeated stale polls beyond 121 seconds and clears after catch-up", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-28T07:30:00Z"));
    const staleDocument = document({
      documentId: "doc-existing",
      filename: "old-name.md",
      status: "ready",
      stage: "ready",
      updatedAt: "2026-08-28T07:00:00Z",
    });
    const authoritativeDocument = document({
      documentId: "doc-existing",
      filename: "handbook.md",
      status: "ready",
      stage: "ready",
      chunkCount: 8,
      updatedAt: "2026-08-28T08:00:00Z",
    });
    const background = document({
      documentId: "doc-background",
      filename: "background.md",
      status: "processing",
      stage: "embedding",
    });
    const api = fakeKnowledgeClient()
      .listOnce([staleDocument, background])
      .listOnce([staleDocument, background])
      .listOnce([staleDocument, background])
      .listOnce([staleDocument, background])
      .listOnce([authoritativeDocument, background])
      .listOnce([staleDocument, background])
      .withDuplicateUpload();
    const queryClient = createTestQueryClient();
    const { result } = renderHook(
      () => ({
        list: useDocumentListQuery(),
        upload: useUploadDocumentMutation(),
      }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={queryClient}>
            <KnowledgeClientProvider client={api}>
              {children}
            </KnowledgeClientProvider>
          </QueryClientProvider>
        ),
      },
    );
    await act(async () => vi.advanceTimersByTimeAsync(0));

    let uploadPromise!: Promise<unknown>;
    act(() => {
      uploadPromise = result.current.upload.mutateAsync({
        file: new File(["# Duplicate"], "renamed.md"),
        onProgress: () => undefined,
      });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20);
      await uploadPromise;
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(api.listCalls).toBe(2);

    vi.setSystemTime(OVERLAY_REGRESSION_TIME);
    await advanceOnePoll();
    await advanceOnePoll();
    expect(api.listCalls).toBe(4);
    expect(cachedDocument(queryClient, "doc-existing")?.filename).toBe(
      "handbook.md",
    );

    await advanceOnePoll();
    expect(cachedDocument(queryClient, "doc-existing")).toMatchObject({
      filename: "handbook.md",
      chunkCount: 8,
    });
    await advanceOnePoll();
    expect(cachedDocument(queryClient, "doc-existing")?.filename).toBe(
      "old-name.md",
    );
  });

  it("keeps a retry receipt across repeated stale polls beyond 121 seconds and clears after catch-up", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-28T07:30:00Z"));
    const failed = document({
      documentId: "doc-retry",
      filename: "retry.md",
      status: "failed",
      stage: "embedding",
      errorCode: "embedding-unavailable",
      errorSummary: "向量服务暂时不可用。",
      updatedAt: "2026-08-28T07:00:00Z",
    });
    const background = document({
      documentId: "doc-background",
      filename: "background.md",
      status: "processing",
      stage: "embedding",
    });
    const authoritative = document({
      documentId: failed.documentId,
      filename: failed.filename,
      chunkCount: 6,
      status: "processing",
      stage: "publishing",
      updatedAt: "2026-08-28T08:00:00Z",
    });
    const api = fakeKnowledgeClient()
      .listOnce([failed, background])
      .listOnce([failed, background])
      .listOnce([failed, background])
      .listOnce([failed, background])
      .listOnce([authoritative, background])
      .listOnce([failed, background]);
    const queryClient = createTestQueryClient();
    const { result } = renderHook(
      () => ({
        list: useDocumentListQuery(),
        retry: useRetryDocumentMutation(),
      }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={queryClient}>
            <KnowledgeClientProvider client={api}>
              {children}
            </KnowledgeClientProvider>
          </QueryClientProvider>
        ),
      },
    );
    await act(async () => vi.advanceTimersByTimeAsync(0));

    await act(async () => result.current.retry.mutateAsync(failed.documentId));
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(api.listCalls).toBe(2);

    vi.setSystemTime(OVERLAY_REGRESSION_TIME);
    await advanceOnePoll();
    await advanceOnePoll();
    expect(api.listCalls).toBe(4);
    expect(cachedDocument(queryClient, failed.documentId)).toMatchObject({
      status: "queued",
      stage: "stored",
      updatedAt: "2026-08-28T07:30:00Z",
    });

    await advanceOnePoll();
    expect(cachedDocument(queryClient, failed.documentId)).toMatchObject({
      chunkCount: 6,
      status: "processing",
      stage: "publishing",
    });
    await advanceOnePoll();
    expect(cachedDocument(queryClient, failed.documentId)?.status).toBe(
      "failed",
    );
  });

  it("marks a deleting row unavailable immediately and removes it after 204", async () => {
    const api = fakeKnowledgeClient()
      .withDocuments([
        document({
          documentId: "doc-delete",
          filename: "retire.md",
          status: "ready",
        }),
      ])
      .deferDelete();
    const queryClient = createTestQueryClient();
    queryClient.setQueryData(knowledgeKeys.documents(), {
      items: [
        document({
          documentId: "doc-delete",
          filename: "retire.md",
          status: "ready",
        }),
      ],
      nextCursor: null,
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <KnowledgeClientProvider client={api}>{children}</KnowledgeClientProvider>
    );
    const { result } = renderHook(() => useDeleteDocumentMutation(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>
          {wrapper({ children })}
        </QueryClientProvider>
      ),
    });

    act(() => result.current.mutate("doc-delete"));

    await waitFor(() => {
      expect(
        queryClient.getQueryData<{ items: Array<{ status: string }> }>(
          knowledgeKeys.documents(),
        ),
      ).toMatchObject({ items: [{ status: "deleting" }] });
    });

    api.finishDelete();
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(
      queryClient.getQueryData<{ items: unknown[] }>(knowledgeKeys.documents()),
    ).toMatchObject({ items: [] });
  });

  it("keeps a pending delete marked unavailable across a stale list response", async () => {
    const existing = document({
      documentId: "doc-delete",
      filename: "retire.md",
      status: "ready",
      stage: "ready",
    });
    const api = fakeKnowledgeClient()
      .listOnce([existing])
      .listOnce([existing])
      .deferDelete();
    const queryClient = createTestQueryClient();
    const { result } = renderHook(
      () => ({
        list: useDocumentListQuery(),
        remove: useDeleteDocumentMutation(),
      }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={queryClient}>
            <KnowledgeClientProvider client={api}>
              {children}
            </KnowledgeClientProvider>
          </QueryClientProvider>
        ),
      },
    );
    await waitFor(() => expect(result.current.list.isSuccess).toBe(true));
    const now = vi.spyOn(Date, "now").mockReturnValue(0);
    try {
      act(() => result.current.remove.mutate("doc-delete"));
      await waitFor(() =>
        expect(
          queryClient.getQueryData<DocumentPage>(knowledgeKeys.documents())
            ?.items[0]?.status,
        ).toBe("deleting"),
      );
      now.mockReturnValue(6 * 60_000);
      await queryClient.refetchQueries({
        queryKey: knowledgeKeys.documents(),
        exact: true,
      });

      expect(
        queryClient.getQueryData<DocumentPage>(knowledgeKeys.documents())
          ?.items[0]?.status,
      ).toBe("deleting");
    } finally {
      now.mockRestore();
      api.finishDelete();
    }
  });

  it("does not resurrect a deleted row across repeated stale polls beyond 121 seconds and clears after absence", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-28T07:30:00Z"));
    const existing = document({
      documentId: "doc-delete",
      filename: "retire.md",
      status: "ready",
      stage: "ready",
    });
    const background = document({
      documentId: "doc-background",
      filename: "background.md",
      status: "processing",
      stage: "embedding",
    });
    const api = fakeKnowledgeClient()
      .listOnce([existing, background])
      .listOnce([existing, background])
      .listOnce([existing, background])
      .listOnce([existing, background])
      .listOnce([background])
      .listOnce([existing, background]);
    const queryClient = createTestQueryClient();
    const { result } = renderHook(
      () => ({
        list: useDocumentListQuery(),
        remove: useDeleteDocumentMutation(),
      }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={queryClient}>
            <KnowledgeClientProvider client={api}>
              {children}
            </KnowledgeClientProvider>
          </QueryClientProvider>
        ),
      },
    );
    await act(async () => vi.advanceTimersByTimeAsync(0));

    await act(async () => result.current.remove.mutateAsync("doc-delete"));
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(api.listCalls).toBe(2);
    expect(cachedDocument(queryClient, existing.documentId)).toBeUndefined();

    vi.setSystemTime(OVERLAY_REGRESSION_TIME);
    await advanceOnePoll();
    await advanceOnePoll();
    expect(api.listCalls).toBe(4);
    expect(cachedDocument(queryClient, existing.documentId)).toBeUndefined();

    await advanceOnePoll();
    expect(cachedDocument(queryClient, existing.documentId)).toBeUndefined();
    await advanceOnePoll();
    expect(cachedDocument(queryClient, existing.documentId)?.filename).toBe(
      existing.filename,
    );
  });

  it("rolls back an optimistic delete and invalidates the list when the server rejects it", async () => {
    const api = fakeKnowledgeClient()
      .withDocuments([
        document({
          documentId: "doc-delete",
          filename: "keep.md",
          status: "ready",
          stage: "ready",
        }),
      ])
      .withDeleteProblem(new Error("503 provider detail"));
    const queryClient = createTestQueryClient();
    queryClient.setQueryData(knowledgeKeys.documents(), {
      items: [
        document({
          documentId: "doc-delete",
          filename: "keep.md",
          status: "ready",
          stage: "ready",
        }),
      ],
      nextCursor: null,
    });
    const { result } = renderHook(() => useDeleteDocumentMutation(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>
          <KnowledgeClientProvider client={api}>
            {children}
          </KnowledgeClientProvider>
        </QueryClientProvider>
      ),
    });

    await act(async () => {
      await result.current.mutateAsync("doc-delete").catch(() => undefined);
    });

    expect(
      queryClient.getQueryData<{ items: Array<{ status: string }> }>(
        knowledgeKeys.documents(),
      ),
    ).toMatchObject({ items: [{ status: "ready" }] });
    expect(
      queryClient.getQueryState(knowledgeKeys.documents())?.isInvalidated,
    ).toBe(true);
  });
});

describe("grounded answer queries", () => {
  const request: RetrievalAnswerRequest = {
    query: "退款需要几人审批？",
    answerMode: "quick",
    sources: ["doc"],
    resourceRefs: [{ family: "doc", sourceId: "doc-a", mode: "scope" }],
  };

  it("passes the caller signal through the answer mutation", async () => {
    const base = fakeKnowledgeClient();
    const signals: Array<AbortSignal | undefined> = [];
    const api: KnowledgeClient = {
      ...base,
      async createAnswer(_request, signal) {
        signals.push(signal);
        return answerResponse();
      },
    };
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useCreateAnswerMutation(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>
          <KnowledgeClientProvider client={api}>
            {children}
          </KnowledgeClientProvider>
        </QueryClientProvider>
      ),
    });
    const controller = new AbortController();

    await act(async () =>
      result.current.mutateAsync({ request, signal: controller.signal }),
    );

    expect(signals).toEqual([controller.signal]);
  });

  it("invalidates the shared document snapshot after document-state-changed", async () => {
    const base = fakeKnowledgeClient();
    const api: KnowledgeClient = {
      ...base,
      async createAnswer() {
        throw new KnowledgeClientError({
          type: "https://tap.example/problems/document-state-changed",
          title: "provider secret",
          status: 409,
          detail: "provider secret",
        });
      },
    };
    const queryClient = createTestQueryClient();
    queryClient.setQueryData(knowledgeKeys.documents(), {
      items: [document({ documentId: "doc-a", status: "ready" })],
      nextCursor: null,
    });
    const { result } = renderHook(() => useCreateAnswerMutation(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>
          <KnowledgeClientProvider client={api}>
            {children}
          </KnowledgeClientProvider>
        </QueryClientProvider>
      ),
    });

    await act(async () => {
      await result.current.mutateAsync({ request }).catch(() => undefined);
    });

    expect(
      queryClient.getQueryState(knowledgeKeys.documents())?.isInvalidated,
    ).toBe(true);
  });

  it("uses an uncached, abortable citation resolution query", async () => {
    const base = fakeKnowledgeClient();
    const signals: Array<AbortSignal | undefined> = [];
    const api: KnowledgeClient = {
      ...base,
      getCitation: (_citationId, signal) => {
        signals.push(signal);
        return new Promise((_resolve, reject) => {
          signal?.addEventListener(
            "abort",
            () => reject(new DOMException("Citation aborted", "AbortError")),
            { once: true },
          );
        });
      },
    };
    const queryClient = createTestQueryClient();
    queryClient.setQueryData(
      knowledgeKeys.citation("citation-a"),
      citationPreview({ citationId: "citation-a" }),
    );
    const { result, unmount } = renderHook(
      () => useCitationQuery("citation-a"),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={queryClient}>
            <KnowledgeClientProvider client={api}>
              {children}
            </KnowledgeClientProvider>
          </QueryClientProvider>
        ),
      },
    );

    await waitFor(() => expect(signals).toHaveLength(1));
    expect(result.current.isFetching).toBe(true);
    unmount();

    expect(signals[0]?.aborted).toBe(true);
  });
});
