import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  createConversationClient,
  type ConversationClient,
  type ConversationCreateRequest,
} from "./client";
import { createStreamState, reduceStreamEvent } from "../model/stream";

export const conversationKeys = {
  all: (projectId: string | null) => ["conversations", projectId] as const,
  detail: (projectId: string | null, conversationId: string | null) =>
    ["conversations", projectId, conversationId] as const,
  events: (projectId: string | null, conversationId: string | null) =>
    ["conversations", projectId, conversationId, "events"] as const,
};

export function useConversationClient(
  projectId: string | null,
): ConversationClient | null {
  return useMemo(
    () => (projectId === null ? null : createConversationClient({ projectId })),
    [projectId],
  );
}

export function useConversationList(projectId: string | null) {
  const client = useConversationClient(projectId);
  return useInfiniteQuery({
    queryKey: conversationKeys.all(projectId),
    enabled: client !== null,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      client!.list({ cursor: pageParam, limit: 20, signal }),
    getNextPageParam: (page) => page.nextCursor ?? undefined,
  });
}

export function useConversationDetail(
  projectId: string | null,
  conversationId: string | null,
) {
  const client = useConversationClient(projectId);
  return useQuery({
    queryKey: conversationKeys.detail(projectId, conversationId),
    enabled: client !== null && conversationId !== null,
    queryFn: ({ signal }) => client!.get(conversationId!, signal),
  });
}

export function useConversationEvents(
  projectId: string | null,
  conversationId: string | null,
) {
  const client = useConversationClient(projectId);
  return useQuery({
    queryKey: conversationKeys.events(projectId, conversationId),
    enabled: client !== null && conversationId !== null,
    queryFn: ({ signal }) => client!.events(conversationId!, signal),
    refetchInterval: 1_000,
  });
}

export function useCreateConversation(projectId: string | null) {
  const client = useConversationClient(projectId);
  const cache = useQueryClient();
  return useMutation({
    mutationFn: ({
      input,
      idempotencyKey,
    }: {
      input: ConversationCreateRequest;
      idempotencyKey: string;
    }) => {
      if (client === null)
        throw new Error("Conversation service is unavailable.");
      return client.create(input, idempotencyKey);
    },
    onSuccess: () =>
      cache.invalidateQueries({ queryKey: conversationKeys.all(projectId) }),
  });
}

export function useAppendConversation(
  projectId: string | null,
  conversationId: string | null,
) {
  const client = useConversationClient(projectId);
  const cache = useQueryClient();
  return useMutation({
    mutationFn: ({
      input,
      idempotencyKey,
    }: {
      input: ConversationCreateRequest;
      idempotencyKey: string;
    }) => {
      if (client === null || conversationId === null)
        throw new Error("Conversation is unavailable.");
      return client.append(conversationId, input, idempotencyKey);
    },
    onSuccess: () => {
      void cache.invalidateQueries({
        queryKey: conversationKeys.all(projectId),
      });
      void cache.invalidateQueries({
        queryKey: conversationKeys.detail(projectId, conversationId),
      });
    },
  });
}

export function useCancelTurn(
  projectId: string | null,
  conversationId: string | null,
) {
  const client = useConversationClient(projectId);
  const cache = useQueryClient();
  return useMutation({
    mutationFn: (turnId: string) => {
      if (client === null || conversationId === null)
        throw new Error("Conversation is unavailable.");
      return client.cancel(conversationId, turnId);
    },
    onSuccess: () =>
      cache.invalidateQueries({
        queryKey: conversationKeys.detail(projectId, conversationId),
      }),
  });
}

export function useConversationStream(
  projectId: string | null,
  conversationId: string | null,
) {
  const client = useConversationClient(projectId);
  const [state, setState] = useState(createStreamState);
  const resume = useRef(0);
  useEffect(() => {
    if (client === null || conversationId === null) return;
    resume.current = 0;
    setState(createStreamState());
    const controller = new AbortController();
    let stopped = false;
    const consume = async () => {
      while (!stopped) {
        try {
          for await (const event of client.stream(
            conversationId,
            resume.current,
            controller.signal,
          )) {
            resume.current = Math.max(resume.current, event.sequence);
            setState((current) => reduceStreamEvent(current, event));
          }
          if (!stopped)
            await new Promise((resolve) => setTimeout(resolve, 250));
        } catch {
          if (controller.signal.aborted) return;
          await new Promise((resolve) => setTimeout(resolve, 500));
        }
      }
    };
    void consume();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [client, conversationId]);
  return state;
}
