import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  ConversationAccepted,
  ConversationCreateRequest,
  InsightsExplanationAccepted,
  InsightsExplanationRequest,
} from "../api/client";
import {
  conversationKeys,
  useCancelTurn,
  useConversationClient,
  useConversationDetail,
  useConversationEvents,
  useConversationStream,
} from "../api/queries";
import {
  createStreamState,
  latestTurnState,
  mergedDraftFacts,
  reduceStreamEvent,
} from "./stream";

export type ConversationSend =
  | { kind: "chat"; input: ConversationCreateRequest }
  | { kind: "insights"; input: InsightsExplanationRequest };
type Accepted = ConversationAccepted | InsightsExplanationAccepted;
type PendingIntent = { signature: string; key: string };
const terminal = (status: string | null | undefined) =>
  status != null &&
  ["completed", "abstained", "canceled", "failed"].includes(status);

/** Owns a selected conversation's transport lifecycle; callers prepare wire input
 * and map the resulting facts into their own presentation and selection models. */
export function useConversationRuntime(
  projectId: string | null,
  initialConversationId = "draft",
) {
  const client = useConversationClient(projectId);
  const cache = useQueryClient();
  const [selection, setSelection] = useState({
    projectId,
    conversationId: initialConversationId,
  });
  const [accepted, setAccepted] = useState<
    (Accepted & { kind: ConversationSend["kind"] }) | null
  >(null);
  const [sending, setSending] = useState(false);
  const generation = useRef(0);
  const inFlight = useRef<symbol | null>(null);
  const pendingChat = useRef<PendingIntent | null>(null);
  const pendingInsights = useRef<PendingIntent | null>(null);
  const selectionRef = useRef(selection);
  const changeSelection = useCallback(
    (conversationId: string) => {
      generation.current += 1;
      inFlight.current = null;
      setSending(false);
      setAccepted(null);
      selectionRef.current = { projectId, conversationId };
      setSelection(selectionRef.current);
    },
    [projectId],
  );
  const selectConversation = useCallback(
    (conversationId: string) => {
      if (
        selectionRef.current.projectId === projectId &&
        selectionRef.current.conversationId === conversationId
      )
        return;
      changeSelection(conversationId);
    },
    [changeSelection, projectId],
  );
  if (selection.projectId !== projectId) {
    generation.current += 1;
    inFlight.current = null;
    pendingChat.current = null;
    pendingInsights.current = null;
    selectionRef.current = { projectId, conversationId: initialConversationId };
    setSelection(selectionRef.current);
    setAccepted(null);
    setSending(false);
  }
  useEffect(
    () => () => {
      generation.current += 1;
      inFlight.current = null;
    },
    [],
  );
  const conversationId =
    selection.projectId === projectId
      ? selection.conversationId
      : initialConversationId;
  const durableId = conversationId === "draft" ? null : conversationId;
  const detail = useConversationDetail(projectId, durableId);
  const [pollEvents, setPollEvents] = useState(true);
  const events = useConversationEvents(projectId, durableId, pollEvents);
  const recovered = useMemo(() => {
    let state = createStreamState();
    for (const event of events.data?.items ?? []) {
      state = reduceStreamEvent(state, {
        eventId: event.eventId,
        sequence: event.sequence,
        chatId: conversationId,
        turnId: event.turnId,
        occurredAt: event.occurredAt,
        schemaVersion: 1,
        event: { type: event.eventType, payload: event.payload },
      });
    }
    return state;
  }, [conversationId, events.data?.items]);
  const currentAccepted =
    accepted?.conversationId === conversationId ? accepted : null;
  const targetTurnId =
    currentAccepted?.turnId ?? detail.data?.turns.at(-1)?.turnId ?? null;
  const targetDetail = detail.data?.turns.find(
    (turn) => turn.turnId === targetTurnId,
  );
  const targetIsInsights =
    currentAccepted?.kind === "insights" ||
    targetDetail?.input.insightsQueryId != null;
  const recoveredTarget =
    targetTurnId === null ? undefined : recovered.turns[targetTurnId];
  const stream = useConversationStream(
    projectId,
    durableId,
    targetTurnId,
    recovered.lastSequence,
    !targetIsInsights &&
      targetTurnId !== null &&
      !terminal(targetDetail?.state) &&
      !terminal(recoveredTarget?.status),
  );
  const turns = useMemo(() => {
    const details = detail.data?.turns ?? [];
    const ids = details.map((turn) => turn.turnId);
    // The accepted turn remains visible if an older detail response arrives.
    if (currentAccepted !== null && !ids.includes(currentAccepted.turnId))
      ids.push(currentAccepted.turnId);
    return ids.map((turnId) => {
      const persisted = details.find((turn) => turn.turnId === turnId);
      const latest = latestTurnState(turnId, recovered, stream.state);
      const draftFacts = mergedDraftFacts(turnId, recovered, stream.state);
      const status = terminal(persisted?.state)
        ? persisted!.state
        : (latest?.status ?? persisted?.state ?? "queued");
      return {
        turnId,
        detail: persisted,
        status,
        response: latest?.response ?? null,
        graphContext: latest?.graphContext ?? null,
        draft: terminal(status) ? null : draftFacts.draft,
        citationLabels: draftFacts.citationLabels,
        stage: terminal(status) ? null : (latest?.stage ?? null),
        partialAnswer: latest?.partialAnswer ?? null,
        steps: latest?.steps ?? [],
        error:
          latest?.error ??
          (events.isError || stream.error !== null
            ? "Conversation updates stopped. Your message is saved. Check access or connection, then retry."
            : null),
        evidenceStatus:
          events.isLoading || events.isFetching
            ? ("loading" as const)
            : events.isError
              ? ("error" as const)
              : latest?.response === undefined
                ? ("missing" as const)
                : ("ready" as const),
      };
    });
  }, [
    currentAccepted,
    detail.data,
    events.isError,
    events.isFetching,
    events.isLoading,
    recovered,
    stream.error,
    stream.state,
  ]);
  const targetStatus = turns.find(
    (turn) => turn.turnId === targetTurnId,
  )?.status;
  const hasActiveTurn = targetTurnId !== null && !terminal(targetStatus);
  useEffect(() => {
    setPollEvents(hasActiveTurn);
  }, [hasActiveTurn]);
  useEffect(() => {
    if (
      currentAccepted !== null &&
      !hasActiveTurn &&
      targetDetail !== undefined
    )
      setAccepted(null);
  }, [currentAccepted, hasActiveTurn, targetDetail]);
  const cancelMutation = useCancelTurn(projectId, durableId);
  const resetCancel = cancelMutation.reset;
  useEffect(() => {
    resetCancel();
  }, [conversationId, projectId, resetCancel]);

  const send = async (
    request: ConversationSend | (() => Promise<ConversationSend>),
  ): Promise<Accepted | null> => {
    if (client === null || inFlight.current !== null) return null;
    const token = Symbol("send");
    const startedGeneration = generation.current;
    const startedSelection = selectionRef.current;
    inFlight.current = token;
    setSending(true);
    const isCurrent = () => generation.current === startedGeneration;
    try {
      // Reserve before asynchronous source/Agent/Skill validation in the caller.
      const prepared =
        typeof request === "function" ? await request() : request;
      if (!isCurrent()) return null;
      const insights = prepared.kind === "insights";
      const pendingRef = insights ? pendingInsights : pendingChat;
      const signature = JSON.stringify(
        insights
          ? prepared.input
          : [projectId, startedSelection.conversationId, prepared.input],
      );
      const storageKey = `tap:insights-pending:${projectId}`;
      let pending = pendingRef.current;
      if (insights && pending === null) {
        try {
          pending = JSON.parse(
            window.sessionStorage.getItem(storageKey) ?? "null",
          ) as PendingIntent | null;
        } catch {
          pending = null;
        }
      }
      if (pending?.signature !== signature || typeof pending.key !== "string") {
        pending = { signature, key: crypto.randomUUID() };
        if (insights)
          window.sessionStorage.setItem(storageKey, JSON.stringify(pending));
      }
      pendingRef.current = pending;
      const result =
        prepared.kind === "insights"
          ? await client.explainInsights(prepared.input, pending.key)
          : startedSelection.conversationId === "draft"
            ? await client.create(prepared.input, pending.key)
            : await client.append(
                startedSelection.conversationId,
                prepared.input,
                pending.key,
              );
      if (pendingRef.current === pending) {
        pendingRef.current = null;
        if (insights) window.sessionStorage.removeItem(storageKey);
      }
      void cache.invalidateQueries({
        queryKey: conversationKeys.all(projectId),
      });
      if (!isCurrent()) return null;
      selectionRef.current = {
        projectId,
        conversationId: result.conversationId,
      };
      setSelection(selectionRef.current);
      setAccepted({ ...result, kind: prepared.kind });
      setPollEvents(true);
      return result;
    } catch (error) {
      if (!isCurrent()) return null;
      throw error;
    } finally {
      if (inFlight.current === token) {
        inFlight.current = null;
        setSending(false);
      }
    }
  };

  return {
    conversationId,
    selectConversation,
    reset: () => changeSelection("draft"),
    detail: detail.data,
    events: events.data?.items,
    turns,
    hasActiveTurn,
    sending,
    send,
    cancelError: cancelMutation.isError,
    cancel: (turnId: string) =>
      cancelMutation.isPending
        ? Promise.resolve()
        : cancelMutation.mutateAsync(turnId).then(() => undefined),
    retry: () => {
      stream.retry();
      void events.refetch();
      void detail.refetch();
    },
  };
}
