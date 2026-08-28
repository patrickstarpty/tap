import { Alert, Typography } from "antd";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import {
  knowledgeKeys,
  useCreateAnswerMutation,
  useDocumentListQuery,
} from "../../features/knowledge/api/queries";
import type { RetrievalAnswerResponse } from "../../features/knowledge/api/types";
import { CitationViewer } from "../../features/knowledge/components/CitationViewer";
import { GroundedAnswer } from "../../features/knowledge/components/GroundedAnswer";
import { QuestionComposer } from "../../features/knowledge/components/QuestionComposer";
import { SourcesPanel } from "../../features/knowledge/components/SourcesPanel";
import { COPY, safeAnswerProblemCopy } from "../../features/knowledge/copy";
import {
  INITIAL_SOURCE_SELECTION,
  buildAnswerRequest,
  sourceSelectionReducer,
} from "../../features/knowledge/model/sourceSelection";

type RetrievalCitation = RetrievalAnswerResponse["citations"][number];

interface AnswerState {
  response: RetrievalAnswerResponse;
  selectionSignature: string;
}

interface ActiveCitation {
  citation: RetrievalCitation;
  generation: number;
  id: string;
}

function signature(ids: readonly string[]): string {
  return ids.join("\u0000");
}

function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

export function AthenaWorkspace({
  pollIntervalMs,
}: { pollIntervalMs?: number } = {}) {
  const queryClient = useQueryClient();
  const documentsQuery = useDocumentListQuery({ pollIntervalMs });
  const answerMutation = useCreateAnswerMutation();
  const [selection, dispatchSelection] = useReducer(
    sourceSelectionReducer,
    INITIAL_SOURCE_SELECTION,
  );
  const [answer, setAnswer] = useState<AnswerState | null>(null);
  const [answerError, setAnswerError] = useState<string | null>(null);
  const [answerPending, setAnswerPending] = useState(false);
  const [activeCitation, setActiveCitation] = useState<ActiveCitation | null>(
    null,
  );
  const answerAbortRef = useRef<AbortController | null>(null);
  const answerGenerationRef = useRef(0);
  const answerInFlightRef = useRef(false);
  const citationGenerationRef = useRef(0);
  const citationTriggerRef = useRef<HTMLElement | null>(null);
  const selectionSignatureRef = useRef("");
  const documents = documentsQuery.data?.items ?? [];
  const readyIds = documents
    .filter((document) => document.status === "ready")
    .map((document) => document.documentId);
  const readySet = new Set(readyIds);
  const effectiveSelectedIds = selection.selectedIds.filter((sourceId) =>
    readySet.has(sourceId),
  );
  const effectiveSignature = signature(effectiveSelectedIds);
  const selectionIsCurrent =
    effectiveSelectedIds.length === selection.selectedIds.length;
  selectionSignatureRef.current = signature(selection.selectedIds);
  const visibleAnswer =
    selectionIsCurrent && answer?.selectionSignature === effectiveSignature
      ? answer.response
      : null;

  const clearCitation = useCallback(
    (restoreFocus: boolean) => {
      void queryClient.cancelQueries({
        queryKey: knowledgeKeys.citations(),
      });
      queryClient.removeQueries({ queryKey: knowledgeKeys.citations() });
      citationGenerationRef.current += 1;
      setActiveCitation(null);
      const trigger = citationTriggerRef.current;
      citationTriggerRef.current = null;
      if (restoreFocus && trigger !== null) {
        queueMicrotask(() => trigger.focus());
      }
    },
    [queryClient],
  );

  const cancelCurrentAnswer = useCallback(() => {
    answerGenerationRef.current += 1;
    answerAbortRef.current?.abort();
    answerAbortRef.current = null;
    answerInFlightRef.current = false;
    setAnswerPending(false);
    answerMutation.reset();
  }, [answerMutation]);

  const clearForSelectionChange = useCallback(() => {
    cancelCurrentAnswer();
    clearCitation(false);
    setAnswer(null);
    setAnswerError(null);
  }, [cancelCurrentAnswer, clearCitation]);

  useEffect(() => {
    const next = sourceSelectionReducer(selection, {
      type: "snapshotChanged",
      documents,
    });
    if (next === selection) return;
    selectionSignatureRef.current = signature(next.selectedIds);
    clearForSelectionChange();
    dispatchSelection({ type: "snapshotChanged", documents });
  }, [clearForSelectionChange, documents, selection]);

  useEffect(
    () => () => {
      answerGenerationRef.current += 1;
      answerAbortRef.current?.abort();
      void queryClient.cancelQueries({ queryKey: knowledgeKeys.citations() });
      queryClient.removeQueries({ queryKey: knowledgeKeys.citations() });
    },
    [queryClient],
  );

  const changeSelection = (
    event:
      | { type: "toggle"; sourceId: string; readyIds: readonly string[] }
      | { type: "selectAllReady"; readyIds: readonly string[] }
      | { type: "clear" },
  ) => {
    const next = sourceSelectionReducer(selection, event);
    if (next === selection) return;
    selectionSignatureRef.current = signature(next.selectedIds);
    clearForSelectionChange();
    dispatchSelection(event);
  };

  const submitQuestion = (question: string) => {
    if (answerInFlightRef.current) return;
    let request;
    try {
      request = buildAnswerRequest(question, effectiveSelectedIds);
    } catch {
      return;
    }
    const requestSignature = effectiveSignature;
    answerInFlightRef.current = true;
    setAnswerPending(true);
    setAnswer(null);
    setAnswerError(null);
    clearCitation(false);
    dispatchSelection({ type: "questionSubmitted" });
    const controller = new AbortController();
    answerAbortRef.current?.abort();
    answerAbortRef.current = controller;
    const generation = answerGenerationRef.current + 1;
    answerGenerationRef.current = generation;

    void answerMutation
      .mutateAsync({ request, signal: controller.signal })
      .then((response) => {
        if (
          answerGenerationRef.current !== generation ||
          controller.signal.aborted ||
          selectionSignatureRef.current !== requestSignature
        ) {
          return;
        }
        setAnswer({ response, selectionSignature: requestSignature });
      })
      .catch((error: unknown) => {
        if (
          answerGenerationRef.current === generation &&
          !controller.signal.aborted &&
          !isAbortError(error)
        ) {
          setAnswerError(safeAnswerProblemCopy(error));
        }
      })
      .finally(() => {
        if (answerGenerationRef.current !== generation) return;
        answerInFlightRef.current = false;
        setAnswerPending(false);
        if (answerAbortRef.current === controller) {
          answerAbortRef.current = null;
        }
      });
  };

  const openCitation = (citationId: string, trigger: HTMLElement) => {
    if (visibleAnswer === null) return;
    const citation = visibleAnswer.citations.find(
      (candidate) => candidate.citationId === citationId,
    );
    if (citation === undefined) return;
    clearCitation(false);
    citationTriggerRef.current = trigger;
    const generation = citationGenerationRef.current + 1;
    citationGenerationRef.current = generation;
    setActiveCitation({ citation, generation, id: citationId });
  };

  return (
    <section className="athena-workspace" aria-label="Athena 问答工作区">
      <SourcesPanel
        documents={documents}
        error={documentsQuery.error}
        isError={documentsQuery.isError}
        isPending={documentsQuery.isPending}
        selectedIds={effectiveSelectedIds}
        onRetry={() => void documentsQuery.refetch()}
        onToggle={(sourceId) =>
          changeSelection({ type: "toggle", sourceId, readyIds })
        }
        onSelectAllReady={() =>
          changeSelection({ type: "selectAllReady", readyIds })
        }
        onClear={() => changeSelection({ type: "clear" })}
      />

      <section
        className="athena-panel athena-question-panel"
        aria-labelledby="question-heading"
      >
        <header className="athena-panel-header">
          <div>
            <Typography.Title level={3} id="question-heading">
              {COPY.questionTitle}
            </Typography.Title>
            <Typography.Paragraph type="secondary">
              {COPY.questionDescription}
            </Typography.Paragraph>
          </div>
        </header>
        <div className="athena-answer-surface">
          {answerPending ? (
            <div className="athena-answer-pending" aria-live="polite">
              <span>{COPY.pendingSearch}</span>
              <span>{COPY.pendingAnswer}</span>
            </div>
          ) : null}
          {answerError !== null ? (
            <Alert type="error" showIcon title={answerError} />
          ) : null}
          {visibleAnswer !== null ? (
            <GroundedAnswer
              response={visibleAnswer}
              onOpenCitation={openCitation}
            />
          ) : null}
          {!answerPending && answerError === null && visibleAnswer === null ? (
            <p className="athena-panel-placeholder">{COPY.answerEmpty}</p>
          ) : null}
        </div>
        <QuestionComposer
          pending={answerPending}
          selectedCount={effectiveSelectedIds.length}
          onSubmit={submitQuestion}
        />
      </section>

      <CitationViewer
        active={visibleAnswer === null ? null : activeCitation}
        onClose={() => clearCitation(true)}
      />
    </section>
  );
}
