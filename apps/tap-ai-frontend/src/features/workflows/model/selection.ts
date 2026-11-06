export function agentSelectionFields(
  workflowEnabled: boolean,
  selectedRevisions: readonly string[],
  agentsByRevision: ReadonlyMap<string, { assetId: string }>,
) {
  const selected = selectedRevisions.filter((revision) =>
    agentsByRevision.has(revision),
  );
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
