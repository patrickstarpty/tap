import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { createTestQueryClient } from "../../../shared/testing/renderApp";
import {
  document,
  documentDetail,
  fakeKnowledgeClient,
} from "../testing/fakeKnowledgeClient";
import {
  KnowledgeClientProvider,
  knowledgeKeys,
  useDocumentListQuery,
  useDeleteDocumentMutation,
  useUploadDocumentMutation,
} from "./queries";
import type { DocumentPage } from "./types";

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

  it("keeps a canonical duplicate receipt across one stale list until the server catches up", async () => {
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
    const api = fakeKnowledgeClient()
      .listOnce([staleDocument])
      .listOnce([staleDocument])
      .listOnce([authoritativeDocument])
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
    await waitFor(() => expect(result.current.list.isSuccess).toBe(true));

    await act(async () =>
      result.current.upload.mutateAsync({
        file: new File(["# Duplicate"], "renamed.md"),
        onProgress: () => undefined,
      }),
    );
    await waitFor(() => expect(api.listCalls).toBe(2));
    expect(
      queryClient.getQueryData<DocumentPage>(knowledgeKeys.documents()),
    ).toMatchObject({
      items: [
        {
          documentId: "doc-existing",
          filename: "handbook.md",
          updatedAt: "2026-08-28T07:30:00Z",
        },
      ],
    });

    await queryClient.refetchQueries({
      queryKey: knowledgeKeys.documents(),
      exact: true,
    });
    expect(
      queryClient.getQueryData<DocumentPage>(knowledgeKeys.documents()),
    ).toMatchObject({ items: [{ filename: "handbook.md", chunkCount: 8 }] });
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

  it("does not resurrect a deleted row when the first list after 204 is stale", async () => {
    const existing = document({
      documentId: "doc-delete",
      filename: "retire.md",
      status: "ready",
      stage: "ready",
    });
    const api = fakeKnowledgeClient().listOnce([existing]).listOnce([existing]);
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

    await act(async () => result.current.remove.mutateAsync("doc-delete"));
    await waitFor(() => expect(api.listCalls).toBe(2));

    expect(
      queryClient.getQueryData<DocumentPage>(knowledgeKeys.documents()),
    ).toMatchObject({ items: [] });
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
