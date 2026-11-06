type AgentRevision = {
  assetId: string;
  agentId: string;
  isLatest: boolean;
  status: string;
};

const selectable = (agent: AgentRevision) =>
  agent.isLatest &&
  agent.status === "enabled" &&
  agent.agentId !== "orchestrator";

export function agentSelectionProblem(
  workflowEnabled: boolean,
  selectedRevisions: readonly string[],
  agentsByRevision: ReadonlyMap<string, AgentRevision>,
  restoredAssetIds: readonly string[] = [],
): string | null {
  if (restoredAssetIds.length > 0 && !workflowEnabled)
    return `This chat used workflow Agents: ${restoredAssetIds.join(", ")}. Choose one Agent again or clear the restored selection before sending in legacy mode.`;
  const selectableAgents = new Map(
    [...agentsByRevision].filter(([, agent]) => selectable(agent)),
  );
  const unavailableRestored = restoredAssetIds.filter(
    (assetId) =>
      ![...selectableAgents.values()].some(
        (agent) => agent.assetId === assetId,
      ),
  );
  if (unavailableRestored.length > 0)
    return `Unavailable Agent IDs: ${unavailableRestored.join(", ")}. Choose available Agents or clear the restored selection.`;
  const unavailable = selectedRevisions.filter(
    (revision) => !selectableAgents.has(revision),
  );
  if (unavailable.length > 0)
    return `Unavailable Agent revisions: ${unavailable.join(", ")}. Choose available Agents before sending.`;
  if (!workflowEnabled && selectedRevisions.length > 1)
    return "Choose one Agent before sending in legacy mode.";
  return null;
}

export function agentSelectionFields(
  workflowEnabled: boolean,
  selectedRevisions: readonly string[],
  agentsByRevision: ReadonlyMap<string, AgentRevision>,
) {
  const problem = agentSelectionProblem(
    workflowEnabled,
    selectedRevisions,
    agentsByRevision,
  );
  if (problem !== null) throw new Error(problem);
  const selected = selectedRevisions;
  return workflowEnabled
    ? {
        agentRevisionId: null,
        requestedAgentIds: [
          ...new Set(
            selected.map((revision) => agentsByRevision.get(revision)!.assetId),
          ),
        ],
      }
    : {
        agentRevisionId: selected[0] ?? null,
        requestedAgentIds: [],
      };
}

export function followUpTaskId(
  task:
    | {
        taskId: string;
        conversationId: string | null;
        origin: string;
        status: string;
        availableActions: readonly string[];
      }
    | undefined,
  conversationId: string,
): string | null {
  if (
    task?.origin !== "native" ||
    task.conversationId !== conversationId ||
    !task.availableActions.includes("steer") ||
    ["succeeded", "failed", "canceled", "skipped"].includes(task.status)
  )
    return null;
  return task.taskId;
}
