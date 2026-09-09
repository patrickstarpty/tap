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
  ConversationClientError,
} from "./client";
import { createStreamState, reduceStreamEvent } from "../model/stream";

export const conversationKeys = {
  all: (projectId: string | null) => ["conversations", projectId] as const,
  detail: (projectId: string | null, conversationId: string | null) =>
    ["conversations", projectId, conversationId] as const,
  events: (projectId: string | null, conversationId: string | null) =>
    ["conversations", projectId, conversationId, "events"] as const,
  citation: (
    projectId: string | null,
    conversationId: string | null,
    turnId: string | null,
    citationId: string | null,
    generation: number,
  ) =>
    [
      "conversations",
      projectId,
      conversationId,
      turnId,
      "citations",
      citationId,
      generation,
    ] as const,
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
    retry: retryConversationRequest,
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
    retry: retryConversationRequest,
  });
}

export function useConversationEvents(
  projectId: string | null,
  conversationId: string | null,
  active = true,
) {
  const client = useConversationClient(projectId);
  return useQuery({
    queryKey: conversationKeys.events(projectId, conversationId),
    enabled: client !== null && conversationId !== null,
    queryFn: ({ signal }) => client!.events(conversationId!, signal),
    refetchInterval: active ? 1_000 : false,
    retry: retryConversationRequest,
  });
}

export function useConversationCitation(
  projectId: string | null,
  conversationId: string | null,
  turnId: string | null,
  citationId: string | null,
  generation = 0,
) {
  const client = useConversationClient(projectId);
  return useQuery({
    queryKey: conversationKeys.citation(
      projectId,
      conversationId,
      turnId,
      citationId,
      generation,
    ),
    enabled:
      client !== null &&
      conversationId !== null &&
      turnId !== null &&
      citationId !== null,
    queryFn: ({ signal }) =>
      client!.citation(conversationId!, turnId!, citationId!, signal),
    retry: retryConversationRequest,
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
  active = true,
) {
  const client = useConversationClient(projectId);
  const [state, setState] = useState(createStreamState);
  const resume = useRef(0);
  const [error, setError] = useState<ConversationClientError | null>(null);
  const [generation, setGeneration] = useState(0);
  useEffect(() => {
    if (client === null || conversationId === null || !active) return;
    resume.current = 0;
    setState(createStreamState());
    setError(null);
    const controller = new AbortController();
    let stopped = false;
    const consume = async () => {
      let attempts = 0;
      while (!stopped) {
        try {
          for await (const event of client.stream(
            conversationId,
            resume.current,
            controller.signal,
          )) {
            attempts = 0;
            resume.current = Math.max(resume.current, event.sequence);
            setState((current) => reduceStreamEvent(current, event));
            if (isTerminalEvent(event.event.type)) return;
          }
          attempts += 1;
          if (attempts > 4) {
            setError(new ConversationClientError(503, true));
            return;
          }
          if (!stopped)
            await delay(250 * 2 ** (attempts - 1), controller.signal);
        } catch (caught) {
          if (controller.signal.aborted) return;
          const failure =
            caught instanceof ConversationClientError
              ? caught
              : new ConversationClientError(503, true);
          if (!failure.retryable || attempts >= 4) {
            setError(failure);
            return;
          }
          attempts += 1;
          await delay(250 * 2 ** (attempts - 1), controller.signal);
        }
      }
    };
    void consume();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [active, client, conversationId, generation]);
  return {
    error,
    retry: () => setGeneration((current) => current + 1),
    state,
  };
}

function retryConversationRequest(count: number, error: unknown): boolean {
  return (
    count < 3 &&
    (!(error instanceof ConversationClientError) || error.retryable)
  );
}

function delay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = window.setTimeout(resolve, milliseconds);
    signal.addEventListener(
      "abort",
      () => {
        window.clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

function isTerminalEvent(type: string): boolean {
  return [
    "turn.completed",
    "turn.abstained",
    "turn.canceled",
    "turn.failed",
    "conversation.turn.completed",
  ].includes(type);
}
