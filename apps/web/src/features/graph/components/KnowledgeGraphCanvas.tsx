import Graph from "graphology";
import type Sigma from "sigma";
import { useEffect, useMemo, useRef, useState } from "react";
import { boundedGraph, type GraphSubgraph } from "../model/graph";
import "./graph.css";

interface Props {
  graph: GraphSubgraph;
  onSelectNode: (nodeId: string) => void;
}

type Position = { id: string; x: number; y: number };

export function KnowledgeGraphCanvas({ graph, onSelectNode }: Props) {
  const bounded = useMemo(() => boundedGraph(graph), [graph]);
  const [query, setQuery] = useState("");
  const [community, setCommunity] = useState("all");
  const canvas = useRef<HTMLDivElement>(null);
  const communities = useMemo(
    () =>
      [
        ...new Set(bounded.nodes.map((node) => node.community).filter(Boolean)),
      ] as string[],
    [bounded.nodes],
  );
  const visible = bounded.nodes.filter((node) => {
    const matchesCommunity =
      community === "all" || node.community === community;
    const needle = query.trim().toLocaleLowerCase();
    return (
      matchesCommunity &&
      (!needle || node.label.toLocaleLowerCase().includes(needle))
    );
  });
  const visibleIds = useMemo(
    () => new Set(visible.map((node) => node.nodeId)),
    [visible],
  );
  const visibleEdges = bounded.edges.filter(
    (edge) =>
      visibleIds.has(edge.sourceNodeId) && visibleIds.has(edge.targetNodeId),
  );

  useEffect(() => {
    if (
      !canvas.current ||
      typeof Worker === "undefined" ||
      visible.length === 0
    )
      return;
    const worker = new Worker(
      new URL("../workers/forceAtlas.worker.ts", import.meta.url),
      {
        type: "module",
      },
    );
    let renderer: Sigma | undefined;
    worker.onmessage = async (event: MessageEvent<Position[]>) => {
      if (!canvas.current) return;
      const { default: SigmaRenderer } = await import("sigma");
      const positions = new Map(
        event.data.map((position) => [position.id, position]),
      );
      const data = new Graph();
      for (const node of visible) {
        const position = positions.get(node.nodeId) ?? { x: 0, y: 0 };
        data.addNode(node.nodeId, {
          label: node.label,
          x: position.x,
          y: position.y,
          size: 8,
          color: node.community === "Claims" ? "#9b6a8f" : "#e87722",
        });
      }
      for (const edge of visibleEdges) {
        if (
          data.hasNode(edge.sourceNodeId) &&
          data.hasNode(edge.targetNodeId)
        ) {
          data.addEdgeWithKey(
            edge.edgeId,
            edge.sourceNodeId,
            edge.targetNodeId,
            {
              label: `${edge.relationType} · ${edge.origin}`,
            },
          );
        }
      }
      renderer = new SigmaRenderer(data, canvas.current, {
        renderEdgeLabels: true,
      });
      renderer.on("clickNode", ({ node }) => onSelectNode(node));
    };
    worker.postMessage({
      nodes: visible.map((node) => ({ id: node.nodeId })),
      edges: visibleEdges,
      reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)")
        .matches,
    });
    return () => {
      renderer?.kill();
      worker.terminate();
    };
  }, [onSelectNode, visible, visibleEdges]);

  return (
    <section
      className="tap-graph-explorer"
      aria-label="Knowledge graph explorer"
    >
      <div className="tap-graph-toolbar">
        <label>
          <span>Search graph</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Node name"
          />
        </label>
        <label>
          <span>Community</span>
          <select
            aria-label="Community"
            value={community}
            onChange={(event) => {
              setCommunity(event.target.value);
              setQuery("");
            }}
          >
            <option value="all">All communities</option>
            {communities.map((item) => (
              <option value={item} key={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <p aria-live="polite">{visible.length} nodes in this bounded view</p>
      </div>
      <div className="tap-graph-stage" ref={canvas} aria-hidden="true" />
      <ul className="tap-graph-accessible-list">
        {visible.map((node) => (
          <li key={node.nodeId}>
            <button type="button" onClick={() => onSelectNode(node.nodeId)}>
              {node.label} · {node.nodeType}
            </button>
          </li>
        ))}
      </ul>
      <div
        className="tap-graph-provenance"
        aria-label="Relationship provenance"
      >
        {[...new Set(bounded.edges.map((edge) => edge.origin))].map(
          (origin) => (
            <span key={origin}>{origin}</span>
          ),
        )}
      </div>
    </section>
  );
}
