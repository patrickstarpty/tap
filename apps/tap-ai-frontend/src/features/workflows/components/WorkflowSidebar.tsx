import { useEffect, useRef, useState } from "react";
import type {
  WorkflowCommandRequest,
  WorkflowCommandView,
  WorkflowTaskSnapshot,
} from "../api/client";
import { useWorkflowCommand } from "../api/queries";
import { NodeDetails } from "./NodeDetails";
import { RunDetail } from "./RunDetail";
import "./workflow.css";

type Connection = "idle" | "connecting" | "live" | "reconnecting" | "error";
export function WorkflowSidebar({
  projectId,
  snapshot,
  connection,
  onClose,
  selectedNodeId,
  onSelectNode,
}: {
  projectId: string;
  snapshot: WorkflowTaskSnapshot;
  connection: Connection;
  onClose: () => void;
  selectedNodeId?: string | null;
  onSelectNode?: (nodeId: string | null) => void;
}) {
  const [localNodeId, setLocalNodeId] = useState<string | null>(null);
  const selection = selectedNodeId === undefined ? localNodeId : selectedNodeId;
  const select = onSelectNode ?? setLocalNodeId;
  const selected = snapshot.nodes.find((node) => node.nodeId === selection);
  const command = useWorkflowCommand(projectId, snapshot.taskId);
  const intent = useRef<{ signature: string; key: string } | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
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
      <div className="tap-workflow-scroll">
        {selected ? (
          <NodeDetails
            projectId={projectId}
            snapshot={snapshot}
            node={selected}
            onBack={() => select(null)}
            onCommand={issue}
          />
        ) : (
          <RunDetail
            snapshot={snapshot}
            connection={connection}
            onSelectNode={select}
            onCommand={issue}
          />
        )}
      </div>
    </aside>
  );
}
