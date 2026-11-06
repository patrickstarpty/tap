import { useEffect, useState } from "react";
import { useWorkflowRuns } from "../api/queries";
import type { WorkflowTaskPage } from "../api/client";

type Task = WorkflowTaskPage["items"][number];
type Run = NonNullable<Task["matchingRuns"]>[number];

function pendingItem(status: string): string | null {
  if (status === "awaiting_input") return "Input needed";
  if (status === "waiting_dependency") return "Waiting for dependency";
  if (status === "paused") return "Paused · resume available";
  if (status === "failed") return "Failed · review run";
  return null;
}

export function RunList({
  projectId,
  agentId,
  onOpen,
}: {
  projectId: string;
  agentId?: string;
  onOpen: (
    taskId: string,
    conversationId: string | null,
    nodeId?: string,
  ) => void;
}) {
  const runs = useWorkflowRuns(projectId, agentId);
  const [filter, setFilter] = useState("all");
  const rows: { task: Task; node: Run | null }[] =
    runs.data?.pages.flatMap((page) =>
      page.items.flatMap<{ task: Task; node: Run | null }>((task) =>
        agentId
          ? (task.matchingRuns ?? []).map((node) => ({ task, node }))
          : [{ task, node: null }],
      ),
    ) ?? [];
  const visible = rows.filter(
    ({ task, node }) =>
      filter === "all" ||
      (node?.status ?? task.status) === filter ||
      (filter === "waiting" &&
        ["awaiting_input", "waiting_dependency"].includes(
          node?.status ?? task.status,
        )),
  );
  useEffect(() => {
    if (visible.length === 0 && runs.hasNextPage && !runs.isFetchingNextPage)
      void runs.fetchNextPage();
  }, [
    visible.length,
    runs.hasNextPage,
    runs.isFetchingNextPage,
    runs.fetchNextPage,
  ]);
  return (
    <section className="tap-workflow-run-list" aria-label="Workflow runs">
      <label>
        Show{" "}
        <select
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        >
          <option value="all">All runs</option>
          <option value="running">Running</option>
          <option value="waiting">Waiting</option>
          <option value="paused">Paused</option>
          <option value="failed">Failed</option>
        </select>
      </label>
      {runs.isPending ? (
        <p>Loading runs…</p>
      ) : runs.isError ? (
        <p role="alert">Runs are unavailable.</p>
      ) : visible.length === 0 ? (
        <p>
          {runs.hasNextPage
            ? "Looking for matching runs…"
            : "No matching runs."}
        </p>
      ) : (
        <ul>
          {visible.map(({ task, node }) => (
            <li key={`${task.taskId}:${node?.nodeId ?? "task"}`}>
              <button
                type="button"
                onClick={() =>
                  onOpen(task.taskId, task.conversationId, node?.nodeId)
                }
              >
                <strong>
                  {node
                    ? `${node.agentId} · ${task.goalSummary || task.displayName}`
                    : task.goalSummary || task.displayName}
                </strong>
                <small>Run {task.taskId}</small>
                {!node && (task.matchingRuns?.length ?? 0) > 0 ? (
                  <small>
                    Agents:{" "}
                    {[
                      ...new Set(
                        (task.matchingRuns ?? []).map((run) => run.agentId),
                      ),
                    ].join(", ")}
                  </small>
                ) : null}
                <span>
                  {(node?.status ?? task.status).replaceAll("_", " ")}
                </span>
                {task.recentEvent ? (
                  <small>
                    Latest:{" "}
                    {task.recentEvent.kind
                      .replaceAll(".", " ")
                      .replaceAll("_", " ")}
                  </small>
                ) : null}
                {pendingItem(node?.status ?? task.status) ? (
                  <small>{pendingItem(node?.status ?? task.status)}</small>
                ) : null}
                {node ? <small>Attempt {node.attempt}</small> : null}
                <time
                  dateTime={
                    task.recentEvent?.occurredAt ??
                    node?.createdAt ??
                    task.createdAt
                  }
                >
                  {new Date(
                    task.recentEvent?.occurredAt ??
                      node?.createdAt ??
                      task.createdAt,
                  ).toLocaleString()}
                </time>
              </button>
            </li>
          ))}
        </ul>
      )}
      {runs.hasNextPage ? (
        <button
          type="button"
          onClick={() => void runs.fetchNextPage()}
          disabled={runs.isFetchingNextPage}
        >
          {runs.isFetchingNextPage ? "Loading…" : "Load more runs"}
        </button>
      ) : null}
    </section>
  );
}
