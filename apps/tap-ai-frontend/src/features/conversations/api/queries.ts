import {
  useInfiniteQuery,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  createConversationClient,
  type ConversationCitationPreview,
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

export function useConversationSearch(projectId: string | null, query: string) {
  const client = useConversationClient(projectId);
  const search = query.trim();
  return useQuery({
    queryKey: [...conversationKeys.all(projectId), "search", search] as const,
    enabled: client !== null && search.length > 0,
    queryFn: ({ signal }) => client!.list({ limit: 50, query: search, signal }),
    retry: retryConversationRequest,
  });
}

export function useRenameConversation(projectId: string | null) {
  const client = useConversationClient(projectId);
  const cache = useQueryClient();
  return useMutation({
    mutationFn: ({
      conversationId,
      title,
    }: {
      conversationId: string;
      title: string;
    }) => {
      if (client === null) throw new Error("Conversation is unavailable.");
      return client.rename(conversationId, title);
    },
    onSuccess: () =>
      cache.invalidateQueries({ queryKey: conversationKeys.all(projectId) }),
  });
}

export function useDeleteConversation(projectId: string | null) {
  const client = useConversationClient(projectId);
  const cache = useQueryClient();
  return useMutation({
    mutationFn: (conversationId: string) => {
      if (client === null) throw new Error("Conversation is unavailable.");
      return client.remove(conversationId);
    },
    onSuccess: (_result, conversationId) => {
      cache.removeQueries({
        queryKey: conversationKeys.detail(projectId, conversationId),
      });
      return cache.invalidateQueries({
        queryKey: conversationKeys.all(projectId),
      });
    },
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
    // A historical conversation citation is an immutable snapshot of the
    // turn's own answer, so once fetched it never needs a silent
    // background re-check — an explicit `refetch()` (the retry button)
    // still always goes to the network regardless of this.
    staleTime: Infinity,
  });
}

/**
 * Resolves many edge citations' supporting passages against the turn's own
 * historical answer in one go — every `EdgeCitationChip`/`RelationHoverCard`
 * the turn's `GroundedAnswer` renders needs this (not just the one citation
 * `useConversationCitation` handles for the active `CitationViewer`/
 * `EvidencePanel`), so `TapperWorkspace` calls this once per turn with every
 * edge citation id the turn's response cites and passes the resulting
 * lookup down as a plain function, keeping the historical-vs-current
 * authority choice out of `features/knowledge` entirely.
 *
 * `enabledIds` gates the actual network request: a query object exists for
 * every id in `citationIds` (so the returned map always has an entry, and
 * hook call order/count never depends on hover state), but only ids in
 * `enabledIds` are allowed to fetch — `TapperWorkspace` adds an id the
 * first time its chip is hovered or focused, so an answer with several
 * edge citations does not fire a GET per citation merely by rendering.
 * `staleTime: Infinity` because a historical conversation citation is an
 * immutable snapshot of the turn's own answer — once fetched, it never
 * needs re-checking.
 */
export function useConversationCitations(
  projectId: string | null,
  conversationId: string | null,
  turnId: string | null,
  citationIds: readonly string[],
  enabledIds: ReadonlySet<string>,
): ReadonlyMap<
  string,
  {
    data?: ConversationCitationPreview;
    error: unknown;
    isError: boolean;
    isFetching: boolean;
    refetch: () => Promise<unknown>;
  }
> {
  const client = useConversationClient(projectId);
  const results = useQueries({
    queries: citationIds.map((citationId) => ({
      queryKey: conversationKeys.citation(
        projectId,
        conversationId,
        turnId,
        citationId,
        0,
      ),
      enabled:
        client !== null &&
        conversationId !== null &&
        turnId !== null &&
        enabledIds.has(citationId),
      queryFn: ({ signal }: { signal?: AbortSignal }) =>
        client!.citation(conversationId!, turnId!, citationId, signal),
      retry: retryConversationRequest,
      staleTime: Infinity,
    })),
  });
  // Built with `useMemo` (rather than `useQueries`'s own `combine` option)
  // over the `results` array so the returned `Map`'s identity only changes
  // when a query result actually changes — `TapperWorkspace`'s
  // `historicalCitationQueryFor` callback closes over this map, and an
  // unstable identity here would make that callback (and everything
  // downstream it's passed to) re-create every render regardless.
  return useMemo(
    () =>
      new Map(
        citationIds.map((citationId, index) => [citationId, results[index]]),
      ),
    [citationIds, results],
  );
}

export function useTurnTrace(
  projectId: string | null,
  conversationId: string | null,
  turnId: string,
  options: { enabled: boolean; latestAttempt: number },
) {
  const client = useConversationClient(projectId);
  return useQuery({
    queryKey: [
      ...conversationKeys.detail(projectId, conversationId),
      turnId,
      "trace",
    ] as const,
    enabled: client !== null && conversationId !== null && options.enabled,
    queryFn: ({ signal }) => client!.turnTrace(conversationId!, turnId, signal),
    staleTime: Infinity,
    refetchInterval: (query) => {
      const data = query.state.data;
      const hasLatestAttemptExecuteSpan =
        data?.spans.some(
          (span) =>
            span.name === "turn.execute" &&
            span.attempt === options.latestAttempt,
        ) ?? false;
      return !hasLatestAttemptExecuteSpan && query.state.dataUpdateCount < 4
        ? 2000
        : false;
    },
    retry: retryConversationRequest,
  });
}

export function useModelCallDetail(
  projectId: string | null,
  callId: string | null,
) {
  const client = useConversationClient(projectId);
  return useQuery({
    queryKey: ["model-calls", projectId, callId] as const,
    enabled: client !== null && callId !== null,
    queryFn: ({ signal }) => client!.modelCall(callId!, signal),
    staleTime: Infinity,
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
  targetTurnId: string | null,
  initialCursor: number,
  active = true,
) {
  const client = useConversationClient(projectId);
  const [state, setState] = useState(createStreamState);
  const resume = useRef(0);
  const initialCursorRef = useRef(initialCursor);
  const streamIdentity = useRef<string | null>(null);
  const streamConversation = useRef<string | null>(null);
  const [error, setError] = useState<ConversationClientError | null>(null);
  const [generation, setGeneration] = useState(0);
  initialCursorRef.current = initialCursor;
  useEffect(() => {
    if (
      client === null ||
      conversationId === null ||
      targetTurnId === null ||
      !active
    )
      return;
    const identity = `${conversationId}:${targetTurnId}`;
    if (streamIdentity.current !== identity) {
      streamIdentity.current = identity;
      resume.current =
        streamConversation.current === conversationId
          ? Math.max(resume.current, initialCursorRef.current)
          : initialCursorRef.current;
      streamConversation.current = conversationId;
      setState({ lastSequence: resume.current, turns: {} });
    }
    setError(null);
    const controller = new AbortController();
    let stopped = false;
    const consume = async () => {
      let attempts = 0;
      while (!stopped) {
        try {
          let targetTerminal = false;
          for await (const event of client.stream(
            conversationId,
            resume.current,
            controller.signal,
          )) {
            attempts = 0;
            resume.current = Math.max(resume.current, event.sequence);
            setState((current) => reduceStreamEvent(current, event));
            if (
              event.turnId === targetTurnId &&
              isTerminalEvent(event.event.type)
            )
              targetTerminal = true;
          }
          if (targetTerminal) return;
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
  }, [active, client, conversationId, generation, targetTurnId]);
  return {
    error,
    retry: () => setGeneration((current) => current + 1),
    state,
  };
}

export function retryConversationRequest(
  count: number,
  error: unknown,
): boolean {
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
