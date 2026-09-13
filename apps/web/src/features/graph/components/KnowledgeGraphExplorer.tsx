import { useMemo, useState } from "react";

import { useActiveGraph, useGraphSearch } from "../api/queries";
import type { GraphEvidenceLink } from "../model/graph";
import { GraphInspector } from "./GraphInspector";
import { KnowledgeGraphCanvas } from "./KnowledgeGraphCanvas";
import "./graph.css";

export function KnowledgeGraphExplorer({
  projectId,
  sourceRevisionIds,
}: {
  projectId: string;
  sourceRevisionIds: string[];
}) {
  const active = useActiveGraph(projectId, sourceRevisionIds);
  const snapshotId = active.data?.items[0]?.snapshotId ?? null;
  const graph = useGraphSearch(projectId, snapshotId, "*");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const selectedNode = graph.data?.nodes.find(
    (node) => node.nodeId === selectedNodeId,
  );
  const relations = useMemo(
    () =>
      graph.data?.edges.filter(
        (edge) =>
          edge.sourceNodeId === selectedNodeId ||
          edge.targetNodeId === selectedNodeId,
      ) ?? [],
    [graph.data?.edges, selectedNodeId],
  );
  const evidence = useMemo<GraphEvidenceLink[]>(() => {
    if (!selectedNode || !snapshotId) return [];
    const ids = new Set([
      ...(selectedNode.evidenceIds ?? []),
      ...relations.flatMap((edge) => edge.evidenceIds ?? []),
    ]);
    return (graph.data?.evidence ?? [])
      .filter((item) => ids.has(item.evidenceId))
      .map((item) => ({
        evidenceId: item.evidenceId,
        label: `${item.documentRevisionId} · ${item.chunkId}`,
        href: `/api/v1/projects/${encodeURIComponent(projectId)}/knowledge/graph/evidence/${encodeURIComponent(item.evidenceId)}?snapshotId=${encodeURIComponent(snapshotId)}`,
      }));
  }, [graph.data?.evidence, projectId, relations, selectedNode, snapshotId]);

  if (sourceRevisionIds.length === 0) {
    return (
      <p className="tap-graph-state">
        Select at least one ready source to explore its graph.
      </p>
    );
  }
  if (active.isError || (snapshotId !== null && graph.isError)) {
    return (
      <div className="tap-graph-state" role="alert">
        <p>The knowledge graph is temporarily unavailable.</p>
        <button type="button" onClick={() => void active.refetch()}>
          Try again
        </button>
      </div>
    );
  }
  if (active.isPending || (snapshotId !== null && graph.isPending)) {
    return (
      <p className="tap-graph-state" role="status">
        Loading the bounded knowledge graph…
      </p>
    );
  }
  if (!snapshotId || !graph.data || graph.data.nodes.length === 0) {
    return (
      <p className="tap-graph-state">
        No published graph is ready for these sources yet.
      </p>
    );
  }

  return (
    <div className="tap-graph-live-workspace">
      <KnowledgeGraphCanvas
        graph={graph.data}
        onSelectNode={setSelectedNodeId}
      />
      {selectedNode ? (
        <GraphInspector
          node={selectedNode}
          relations={relations}
          evidence={evidence}
        />
      ) : (
        <aside className="tap-graph-inspector" aria-label="Graph details">
          <h2>Graph details</h2>
          <p>
            Select a node to inspect its relationships and grounded evidence.
          </p>
        </aside>
      )}
    </div>
  );
}
