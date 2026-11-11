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

import {
  BrowserRecovery,
  browserRecoveryKey,
  type FrozenBrowserIntent,
} from "./browserRecovery";
import {
  loadBrowserContext,
  sameLcaContext,
  type LcaGatewayContext,
} from "../../../shared/api/browserContext";

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
  recoveryContext?: LcaGatewayContext,
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
  const recovery = useRef<BrowserRecovery | null>(null);
  const [browserRecoveryPending, setBrowserRecoveryPending] = useState(false);
  const [browserRecoveryError, setBrowserRecoveryError] = useState("");
  const recoveryContextRef = useRef(recoveryContext);
  recoveryContextRef.current = recoveryContext;
  const getRecovery = async () => {
    const context = await loadBrowserContext();
    if (
      recoveryContextRef.current &&
      !sameLcaContext(context, recoveryContextRef.current)
    )
      throw new Error(
        "Authorized browser gateway context changed. Execution is blocked. Review task history and confirm the saved request outcome before starting a replacement.",
      );
    if (context.project_id !== projectId)
      throw new Error("Browser recovery context does not match this project.");
    const store = new BrowserRecovery(window.sessionStorage, context);
    recovery.current = store;
    return store;
  };
  useEffect(() => {
    let active = true;
    recovery.current = null;
    async function restore() {
      try {
        if (window.sessionStorage.getItem(browserRecoveryKey) === null) return;
        const context =
          recoveryContextRef.current ?? (await loadBrowserContext());
        if (!active) return;
        if (context.project_id !== projectId)
          throw new Error(
            "Browser recovery context does not match this project.",
          );
        const store = new BrowserRecovery(window.sessionStorage, context);
        recovery.current = store;
        let pending: FrozenBrowserIntent | null;
        try {
          pending = store.pending();
        } catch (error) {
          const reference = store.reference();
          if (
            !reference ||
            reference.unconfirmed ||
            reference.conversationId === null ||
            client === null
          )
            throw error;
          // A confirmed receipt can be restored through a read-only authorized API.
          // Unknown POST outcomes are never rebound or cleared this way.
          const saved = await client.get(reference.conversationId);
          if (!active || saved.conversationId !== reference.conversationId)
            throw error;
          store.adoptAccepted(saved.conversationId);
          pending = null;
        }
        setBrowserRecoveryPending(pending !== null);
        const restored = store.conversation();
        if (pending === null && restored !== null) {
          selectionRef.current = { projectId, conversationId: restored };
          setSelection(selectionRef.current);
        }
      } catch (error) {
        if (active)
          setBrowserRecoveryError(
            error instanceof Error
              ? error.message
              : "Browser recovery unavailable.",
          );
      }
    }
    if (projectId !== null) void restore();
    return () => {
      active = false;
    };
  }, [projectId, recoveryContext?.context_binding]);
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
  const knownTurns = detail.data?.turns ?? [];
  const acceptedInDetail =
    currentAccepted !== null &&
    knownTurns.some((turn) => turn.turnId === currentAccepted.turnId);
  const candidates = [
    ...knownTurns.map((turn) => ({ turnId: turn.turnId, status: turn.state })),
    ...(currentAccepted !== null && !acceptedInDetail
      ? [{ turnId: currentAccepted.turnId, status: currentAccepted.state }]
      : []),
  ];
  // A continuation can acknowledge its own Turn while the origin still runs.
  // Anchor the conversation stream to the oldest active Turn, not the latest receipt.
  const targetTurnId =
    candidates.find(
      (turn) =>
        !terminal(turn.status) &&
        !terminal(recovered.turns[turn.turnId]?.status),
    )?.turnId ??
    candidates.at(-1)?.turnId ??
    null;
  const targetDetail = detail.data?.turns?.find(
    (turn) => turn.turnId === targetTurnId,
  );
  const targetIsInsights =
    (currentAccepted?.turnId === targetTurnId &&
      currentAccepted.kind === "insights") ||
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
        workflowResult: latest?.workflowResult ?? null,
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
              : latest?.response === undefined && latest?.workflowResult == null
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
  const hasActiveTurn = turns.some((turn) => !terminal(turn.status));
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
    recoveredIntent?: FrozenBrowserIntent,
  ): Promise<Accepted | null> => {
    if (client === null || inFlight.current !== null) return null;
    const token = Symbol("send");
    const startedGeneration = generation.current;
    const startedSelection =
      recoveredIntent === undefined
        ? selectionRef.current
        : { projectId, conversationId: recoveredIntent.conversationId };
    inFlight.current = token;
    setSending(true);
    const isCurrent = () => generation.current === startedGeneration;
    let browserStore: BrowserRecovery | null = null;
    let browserIntent: FrozenBrowserIntent | null = null;
    try {
      // Reserve before asynchronous source/Agent/Skill validation in the caller.
      const prepared =
        typeof request === "function" ? await request() : request;
      if (!isCurrent()) return null;
      const browser =
        prepared.kind === "chat" && prepared.input.browser != null;
      if (browser) {
        browserStore = await getRecovery();
        if (!isCurrent()) return null;
        browserIntent = browserStore.prepare(
          startedSelection.conversationId,
          prepared.input,
        );
        setBrowserRecoveryPending(true);
        setBrowserRecoveryError("");
      } else if (browserRecoveryPending) {
        throw new Error(
          "An unconfirmed browser request must be recovered before sending another message.",
        );
      }
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
      if (browserIntent !== null)
        pending = { signature, key: browserIntent.key };
      pendingRef.current = pending;
      if (browserStore !== null && browserIntent !== null)
        browserStore.markAttempt(browserIntent.key);
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
      if (browserStore !== null && browserIntent !== null) {
        browserStore.accept(browserIntent.key, result.conversationId);
        setBrowserRecoveryPending(false);
        setBrowserRecoveryError("");
      }
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
      const rejected =
        browserStore !== null &&
        browserIntent !== null &&
        browserStore.rejectUnadmitted(
          browserIntent.key,
          (error as { status?: number }).status ?? 0,
        );
      if (rejected) {
        setBrowserRecoveryPending(false);
        setBrowserRecoveryError("");
      } else if (recovery.current !== null || recoveredIntent !== undefined)
        setBrowserRecoveryError(
          error instanceof Error
            ? error.message
            : "Browser receipt unconfirmed. Recover the original request.",
        );
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
    reset: () => {
      if (browserRecoveryPending || browserRecoveryError) return;
      // Non-browser chat retains its existing reset behavior.
      // Insights keeps its separate recovery key until acceptance.
      pendingChat.current = null;
      changeSelection("draft");
    },
    detail: detail.data,
    events: events.data?.items,
    turns,
    hasActiveTurn,
    sending,
    send,
    browserRecoveryPending,
    browserRecoveryError,
    recoverBrowser: async () => {
      const store = await getRecovery();
      const pending = store.pending();
      if (pending === null) return null;
      return send({ kind: "chat", input: pending.input }, pending);
    },
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
