import { useLayoutEffect, useRef, useState } from "react";
import type { components } from "../../../shared/api/generated/schema";
import { layoutWorkflow } from "../model/layout";

type Node = components["schemas"]["WorkflowNodeView"];
type Edge = components["schemas"]["WorkflowEdgeView"];
type Status = components["schemas"]["WorkflowStatus"];
const kindName = { agent: "Agent", service: "Service", approval: "Approval" };
export function WorkflowGraph({
  nodes,
  edges,
  status,
  onSelect,
}: {
  nodes: readonly Node[];
  edges: readonly Edge[];
  status: Status;
  onSelect: (nodeId: string) => void;
}) {
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const graphRef = useRef<HTMLDivElement>(null);
  const [lines, setLines] = useState<{ id: string; path: string }[]>([]);
  useLayoutEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    const measure = () => {
      const frame = graph.getBoundingClientRect();
      const cards = new Map(
        Array.from(
          graph.querySelectorAll<HTMLElement>("[data-workflow-node]"),
        ).map((card) => [card.dataset.workflowNode!, card]),
      );
      setLines(
        edges.flatMap((edge) => {
          const from = cards.get(edge.fromNodeId)?.getBoundingClientRect();
          const to = cards.get(edge.toNodeId)?.getBoundingClientRect();
          if (!from || !to) return [];
          const x1 = from.left + from.width / 2 - frame.left;
          const y1 = from.bottom - frame.top;
          const x2 = to.left + to.width / 2 - frame.left;
          const y2 = to.top - frame.top;
          const middle = (y1 + y2) / 2;
          return [
            {
              id: `${edge.fromNodeId}:${edge.toNodeId}`,
              path: `M ${x1} ${y1} C ${x1} ${middle}, ${x2} ${middle}, ${x2} ${y2}`,
            },
          ];
        }),
      );
    };
    measure();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(graph);
    return () => observer.disconnect();
  }, [edges, nodes, collapsed]);
  // The system root is an internal provenance, never a callable Agent card.
  const visible = nodes.filter((node) => node.agentId !== "orchestrator");
  const ids = new Set(visible.map((node) => node.nodeId));
  const layout = layoutWorkflow(
    visible,
    edges.filter((edge) => ids.has(edge.fromNodeId) && ids.has(edge.toNodeId)),
    collapsed,
  );
  if (layout.error)
    return <p role="alert">Workflow data is inconsistent. Refresh the task.</p>;
  if (visible.length === 0)
    return (
      <p className="tap-workflow-empty">
        {["succeeded", "failed", "canceled", "skipped"].includes(status)
          ? "No specialist nodes were recorded for this task."
          : "Tapper is handling this task."}
      </p>
    );
  const byId = new Map(visible.map((node) => [node.nodeId, node]));
  return (
    <div
      className="tap-workflow-graph"
      aria-label="Workflow dependencies"
      ref={graphRef}
    >
      <svg
        className="tap-workflow-edges"
        aria-hidden="true"
        width="100%"
        height="100%"
      >
        <defs>
          <marker
            id="tap-workflow-arrow"
            markerWidth="5"
            markerHeight="5"
            refX="4"
            refY="2.5"
            orient="auto"
          >
            <path d="M0 0 L5 2.5 L0 5" fill="none" stroke="currentColor" />
          </marker>
        </defs>
        {lines.map((line) => (
          <path
            key={line.id}
            data-workflow-edge={line.id}
            d={line.path}
            markerEnd="url(#tap-workflow-arrow)"
          />
        ))}
      </svg>
      {layout.layers.map((layer, index) => (
        <div className="tap-workflow-layer" key={index}>
          {layer.map((id) => {
            const node = byId.get(id)!;
            const hasChildren = visible.some(
              (item) => item.parentNodeId === id,
            );
            const dependencies = edges
              .filter((edge) => edge.toNodeId === id)
              .map((edge) => byId.get(edge.fromNodeId)?.displayName)
              .filter((name): name is string => name !== undefined);
            const dependencyLabel = dependencies.length
              ? `Depends on ${dependencies.join(", ")}`
              : null;
            const statusLabel =
              node.status === "unknown"
                ? "Outcome unknown"
                : node.status.replaceAll("_", " ");
            return (
              <div
                className="tap-workflow-node"
                key={id}
                data-workflow-node={id}
              >
                <button
                  type="button"
                  onClick={() => onSelect(id)}
                  aria-label={`${node.displayName} ${kindName[node.kind]} ${statusLabel}${dependencyLabel ? ` · ${dependencyLabel}` : ""}`}
                >
                  <span className="tap-workflow-kind">
                    {kindName[node.kind]}
                  </span>
                  <strong>{node.displayName}</strong>
                  <span
                    className="tap-workflow-status"
                    data-status={node.status}
                  >
                    {statusLabel}
                  </span>
                  {dependencyLabel ? (
                    <small className="tap-workflow-dependency">
                      {dependencyLabel}
                    </small>
                  ) : null}
                  {node.attempt > 1 ? (
                    <small>Attempt {node.attempt}</small>
                  ) : null}
                </button>
                {hasChildren ? (
                  <button
                    type="button"
                    className="tap-workflow-fold"
                    aria-label={`${collapsed.has(id) ? "Expand" : "Collapse"} ${node.displayName}`}
                    onClick={() =>
                      setCollapsed((current) => {
                        const next = new Set(current);
                        if (next.has(id)) next.delete(id);
                        else next.add(id);
                        return next;
                      })
                    }
                  >
                    {collapsed.has(id) ? "Show children" : "Hide children"}
                  </button>
                ) : null}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
