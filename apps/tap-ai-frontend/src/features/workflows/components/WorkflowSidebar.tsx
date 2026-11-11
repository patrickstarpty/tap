import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type {
  WorkflowCommandRequest,
  WorkflowCommandView,
  WorkflowTaskSnapshot,
} from "../api/client";
import { createWorkflowClient } from "../api/client";
import { useWorkflowCommand } from "../api/queries";
import { NodeDetails } from "./NodeDetails";
import { RunDetail, type LcaSourceReference } from "./RunDetail";
import "./workflow.css";

type Connection = "idle" | "connecting" | "live" | "reconnecting" | "error";
export function WorkflowSidebar({
  projectId,
  snapshot,
  connection,
  onClose,
  selectedNodeId,
  onSelectNode,
  selectedAttempt,
  onSelectAttempt,
  scrollTop = 0,
  onScrollTopChange,
  onRetryConnection,
  onReplayed,
  onProvideInput,
  onReviewLca,
}: {
  projectId: string;
  snapshot: WorkflowTaskSnapshot;
  connection: Connection;
  onClose: () => void;
  selectedNodeId?: string | null;
  onSelectNode?: (nodeId: string | null) => void;
  selectedAttempt?: number | null;
  onSelectAttempt?: (attempt: number) => void;
  scrollTop?: number;
  onScrollTopChange?: (scrollTop: number) => void;
  onRetryConnection?: () => void;
  onReplayed?: (taskId: string) => void;
  onProvideInput?: () => void;
  onReviewLca?: (source: LcaSourceReference) => void;
}) {
  const [localNodeId, setLocalNodeId] = useState<string | null>(null);
  const selection = selectedNodeId === undefined ? localNodeId : selectedNodeId;
  const select = onSelectNode ?? setLocalNodeId;
  const selected = snapshot.nodes.find((node) => node.nodeId === selection);
  const command = useWorkflowCommand(projectId, snapshot.taskId);
  const intent = useRef<{ signature: string; key: string } | null>(null);
  const replayKey = useRef<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (scroll.current) scroll.current.scrollTop = scrollTop;
  }, [scrollTop, selected?.nodeId]);
  useEffect(() => {
    if (selected) heading.current?.focus({ preventScroll: true });
  }, [selected?.nodeId]);
  const issue = async (
    kind: WorkflowCommandRequest["kind"],
    nodeId?: string,
    text?: string,
  ): Promise<WorkflowCommandView> => {
    const body: WorkflowCommandRequest = {
      kind,
      expectedPlanRevision: snapshot.planRevision,
      targetNodeId: nodeId ?? null,
      text: text ?? null,
    };
    const signature = JSON.stringify([snapshot.taskId, body]);
    if (intent.current?.signature !== signature)
      intent.current = { signature, key: crypto.randomUUID() };
    const result = await command.mutateAsync({ body, key: intent.current.key });
    intent.current = null;
    return result;
  };
  return (
    <aside className="tap-workflow-sidebar" aria-label="Workflow details">
      <header className="tap-workflow-header">
        <h2 ref={heading} tabIndex={-1}>
          {selected ? "Node details" : "Task progress"}
        </h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close workflow details"
        >
          Close
        </button>
      </header>
      {selected && connection === "error" ? (
        <p className="tap-workflow-connection" role="status">
          Disconnected · showing last saved update.{" "}
          {onRetryConnection ? (
            <button type="button" onClick={onRetryConnection}>
              Retry connection
            </button>
          ) : null}
        </p>
      ) : null}
      <div
        className="tap-workflow-scroll"
        ref={scroll}
        onScroll={(event) => onScrollTopChange?.(event.currentTarget.scrollTop)}
      >
        {selected ? (
          <NodeDetails
            projectId={projectId}
            snapshot={snapshot}
            node={selected}
            onBack={() => select(null)}
            onCommand={issue}
            selectedAttempt={selectedAttempt}
            onSelectAttempt={onSelectAttempt}
          />
        ) : (
          <RunDetail
            snapshot={snapshot}
            connection={connection}
            onSelectNode={select}
            onCommand={issue}
            onRetryConnection={onRetryConnection}
            onReplay={
              snapshot.taskKind === "browser" && onReplayed
                ? async () => {
                    replayKey.current ??= crypto.randomUUID();
                    const next = await createWorkflowClient(projectId).replay(
                      snapshot.taskId,
                      replayKey.current,
                    );
                    replayKey.current = null;
                    onReplayed(next.taskId);
                  }
                : undefined
            }
            onProvideInput={onProvideInput}
            onReviewLca={onReviewLca}
          />
        )}
      </div>
    </aside>
  );
}
