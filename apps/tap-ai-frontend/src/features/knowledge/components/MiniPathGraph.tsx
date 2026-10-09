import { relationText, type EdgeCitation } from "../model/edgeCitation";

const WIDTH = 320;
const HEIGHT = 200;
const CENTER_X = WIDTH / 2;
const CENTER_Y = HEIGHT / 2;
const RADIUS_X = 130;
const RADIUS_Y = 70;
const ARROW_MARKER_ID = "tapper-mini-path-arrow";

interface PathNode {
  nodeId: string;
  label: string;
}

function collectNodes(edges: readonly EdgeCitation[]): PathNode[] {
  const byId = new Map<string, PathNode>();
  for (const citation of edges) {
    const { subject, object } = citation.edge;
    if (!byId.has(subject.nodeId)) {
      byId.set(subject.nodeId, {
        nodeId: subject.nodeId,
        label: subject.label,
      });
    }
    if (!byId.has(object.nodeId)) {
      byId.set(object.nodeId, { nodeId: object.nodeId, label: object.label });
    }
  }
  return [...byId.values()];
}

function layout(
  nodes: readonly PathNode[],
): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>();
  const count = Math.max(nodes.length, 1);
  nodes.forEach((node, index) => {
    const angle = (2 * Math.PI * index) / count - Math.PI / 2;
    positions.set(node.nodeId, {
      x: CENTER_X + RADIUS_X * Math.cos(angle),
      y: CENTER_Y + RADIUS_Y * Math.sin(angle),
    });
  });
  return positions;
}

/**
 * Renders every edge citation of the current turn as a tiny node/edge
 * diagram — nodes deduplicated by `nodeId` and spread evenly on a 320×200
 * ellipse, edges as straight arrowed lines, the citation the evidence panel
 * is currently showing marked `data-active="true"` (so active state does
 * not rely on color alone). `role="img"` carries the same information as
 * text for assistive tech: every edge's `relationText`, joined by "；".
 */
export function MiniPathGraph({
  edges,
  activeEdgeId,
}: {
  edges: readonly EdgeCitation[];
  activeEdgeId: string;
}) {
  const nodes = collectNodes(edges);
  const positions = layout(nodes);
  const label = edges.map((edge) => relationText(edge)).join("；");

  return (
    <svg
      className="tapper-mini-path-graph"
      viewBox={`0 0 ${String(WIDTH)} ${String(HEIGHT)}`}
      width={WIDTH}
      height={HEIGHT}
      role="img"
      aria-label={label}
    >
      <defs>
        <marker
          id={ARROW_MARKER_ID}
          viewBox="0 0 10 10"
          refX="8"
          refY="5"
          markerWidth="6"
          markerHeight="6"
          orient="auto-start-reverse"
        >
          <path d="M0,0 L10,5 L0,10 z" />
        </marker>
      </defs>
      {edges.map((citation) => {
        const from = positions.get(citation.edge.subject.nodeId);
        const to = positions.get(citation.edge.object.nodeId);
        if (from === undefined || to === undefined) return null;
        return (
          <line
            key={citation.edge.edgeId}
            x1={from.x}
            y1={from.y}
            x2={to.x}
            y2={to.y}
            stroke="currentColor"
            markerEnd={`url(#${ARROW_MARKER_ID})`}
            data-active={
              citation.edge.edgeId === activeEdgeId ? "true" : undefined
            }
          />
        );
      })}
      {nodes.map((node) => {
        const position = positions.get(node.nodeId);
        if (position === undefined) return null;
        return (
          <g
            key={node.nodeId}
            transform={`translate(${String(position.x)}, ${String(position.y)})`}
          >
            <circle r={5} />
            <text x={8} y={4}>
              {node.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
