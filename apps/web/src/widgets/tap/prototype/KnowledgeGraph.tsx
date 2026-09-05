import { AimOutlined, MinusOutlined, PlusOutlined } from "@ant-design/icons";
import { useEffect, useMemo, useRef, useState } from "react";
import type {
  CSSProperties,
  KeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from "react";

import type { PrototypeCopy } from "./copy";
import type { LibrarySource } from "./model";

type GraphCommunity = "sources" | "application" | "underwriting" | "parties";
type GraphNodeKind = "document" | "concept" | "entity";
type GraphProvenance = "extracted" | "inferred";

interface GraphNode {
  community: GraphCommunity;
  degree: number;
  id: string;
  kind: GraphNodeKind;
  label: string;
  secondary?: string;
  x: number;
  y: number;
}

interface GraphEdge {
  id: string;
  label: string;
  provenance: GraphProvenance;
  source: string;
  target: string;
}

interface GraphData {
  edges: GraphEdge[];
  nodes: GraphNode[];
}

const GRAPH_WIDTH = 680;
const GRAPH_HEIGHT = 460;
const GRAPH_HORIZONTAL_MARGIN = 24;
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 1.75;
const ZOOM_STEP = 0.25;

const COMMUNITY_ORDER: readonly GraphCommunity[] = [
  "sources",
  "application",
  "underwriting",
  "parties",
];

const COMMUNITY_COLORS: Record<GraphCommunity, string> = {
  sources: "#7c8ba5",
  application: "#8b82f6",
  underwriting: "#35c7a5",
  parties: "#f2a65a",
};

const SOURCE_POSITIONS = [
  { x: 72, y: 82 },
  { x: 116, y: 190 },
  { x: 70, y: 338 },
  { x: 218, y: 58 },
  { x: 198, y: 414 },
] as const;

function graphData(
  copy: PrototypeCopy,
  sources: readonly LibrarySource[],
): GraphData {
  const nodesWithoutDegree: Omit<GraphNode, "degree">[] = [
    {
      id: "application",
      label: copy.library.application,
      community: "application",
      kind: "concept",
      x: 334,
      y: 232,
    },
    {
      id: "policy",
      label: copy.library.policy,
      community: "application",
      kind: "entity",
      x: 350,
      y: 78,
    },
    {
      id: "coverage",
      label: copy.library.coverage,
      community: "application",
      kind: "entity",
      x: 468,
      y: 58,
    },
    {
      id: "premium",
      label: copy.library.premium,
      community: "application",
      kind: "entity",
      x: 604,
      y: 372,
    },
    {
      id: "health-disclosure",
      label: copy.library.healthDisclosure,
      community: "underwriting",
      kind: "concept",
      x: 466,
      y: 184,
    },
    {
      id: "underwriting",
      label: copy.library.underwriting,
      community: "underwriting",
      kind: "concept",
      x: 570,
      y: 252,
    },
    {
      id: "risk-assessment",
      label: copy.library.riskAssessment,
      community: "underwriting",
      kind: "entity",
      x: 596,
      y: 122,
    },
    {
      id: "beneficiary",
      label: copy.library.beneficiary,
      community: "parties",
      kind: "concept",
      x: 430,
      y: 368,
    },
    {
      id: "applicant",
      label: copy.library.applicant,
      community: "parties",
      kind: "entity",
      x: 266,
      y: 356,
    },
    ...sources.map((source, index) => {
      const position = SOURCE_POSITIONS[index % SOURCE_POSITIONS.length]!;
      const lap = Math.floor(index / SOURCE_POSITIONS.length);
      return {
        id: `source-${source.id}`,
        label: source.name,
        secondary: source.type,
        community: "sources" as const,
        kind: "document" as const,
        x: position.x + lap * 34,
        y: Math.max(52, position.y - lap * 26),
      };
    }),
  ];

  const edges: GraphEdge[] = [
    {
      id: "application-health",
      source: "application",
      target: "health-disclosure",
      label: copy.library.requires,
      provenance: "extracted",
    },
    {
      id: "health-underwriting",
      source: "health-disclosure",
      target: "underwriting",
      label: copy.library.informs,
      provenance: "extracted",
    },
    {
      id: "application-beneficiary",
      source: "application",
      target: "beneficiary",
      label: copy.library.names,
      provenance: "extracted",
    },
    {
      id: "applicant-application",
      source: "applicant",
      target: "application",
      label: copy.library.submits,
      provenance: "extracted",
    },
    {
      id: "application-policy",
      source: "application",
      target: "policy",
      label: copy.library.creates,
      provenance: "inferred",
    },
    {
      id: "coverage-underwriting",
      source: "coverage",
      target: "underwriting",
      label: copy.library.informs,
      provenance: "extracted",
    },
    {
      id: "underwriting-risk",
      source: "underwriting",
      target: "risk-assessment",
      label: copy.library.evaluates,
      provenance: "inferred",
    },
    {
      id: "risk-premium",
      source: "risk-assessment",
      target: "premium",
      label: copy.library.determines,
      provenance: "inferred",
    },
    ...sources.map((source) => {
      const normalizedName = source.name.toLocaleLowerCase();
      const target = normalizedName.includes("health")
        ? "health-disclosure"
        : "application";
      return {
        id: `document-${source.id}`,
        source: `source-${source.id}`,
        target,
        label: copy.library.supports,
        provenance: "extracted" as const,
      };
    }),
  ];

  const degreeByNode = new Map<string, number>();
  for (const edge of edges) {
    degreeByNode.set(edge.source, (degreeByNode.get(edge.source) ?? 0) + 1);
    degreeByNode.set(edge.target, (degreeByNode.get(edge.target) ?? 0) + 1);
  }

  return {
    edges,
    nodes: nodesWithoutDegree.map((node) => ({
      ...node,
      degree: degreeByNode.get(node.id) ?? 0,
    })),
  };
}

function displayLabel(label: string): string {
  return label.length > 27 ? `${label.slice(0, 25)}…` : label;
}

export function KnowledgeGraph({
  copy,
  query,
  sources,
}: {
  copy: PrototypeCopy;
  query: string;
  sources: readonly LibrarySource[];
}) {
  const data = useMemo(() => graphData(copy, sources), [copy, sources]);
  const [activeCommunities, setActiveCommunities] = useState<
    ReadonlySet<GraphCommunity>
  >(() => new Set(COMMUNITY_ORDER));
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef<{
    originX: number;
    originY: number;
    pointerId: number;
    startX: number;
    startY: number;
  } | null>(null);
  const normalizedQuery = query.trim().toLocaleLowerCase();

  const communityLabels: Record<GraphCommunity, string> = {
    sources: copy.library.sourceCommunity,
    application: copy.library.applicationCommunity,
    underwriting: copy.library.underwritingCommunity,
    parties: copy.library.partiesCommunity,
  };
  const kindLabels: Record<GraphNodeKind, string> = {
    document: copy.library.documentNode,
    concept: copy.library.conceptNode,
    entity: copy.library.entityNode,
  };
  const provenanceLabels: Record<GraphProvenance, string> = {
    extracted: copy.library.extracted,
    inferred: copy.library.inferred,
  };

  const visibleNodes = data.nodes.filter((node) =>
    activeCommunities.has(node.community),
  );
  const visibleNodeIds = new Set(visibleNodes.map((node) => node.id));
  const visibleEdges = data.edges.filter(
    (edge) =>
      visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target),
  );
  const selectedNode =
    visibleNodes.find((node) => node.id === selectedNodeId) ?? null;
  const selectedRelationships =
    selectedNode === null
      ? []
      : visibleEdges.filter(
          (edge) =>
            edge.source === selectedNode.id || edge.target === selectedNode.id,
        );

  useEffect(() => {
    if (
      selectedNodeId !== null &&
      !visibleNodes.some((node) => node.id === selectedNodeId)
    ) {
      setSelectedNodeId(null);
    }
  }, [selectedNodeId, visibleNodes]);

  const toggleCommunity = (community: GraphCommunity) => {
    setActiveCommunities((current) => {
      const next = new Set(current);
      if (next.has(community)) next.delete(community);
      else next.add(community);
      return next;
    });
  };

  const selectNodeFromKeyboard = (
    event: KeyboardEvent<SVGGElement>,
    nodeId: string,
  ) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    setSelectedNodeId(nodeId);
  };

  const startPan = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (event.button !== 0) return;
    const target = event.target as Element;
    if (target.closest('[role="button"]') !== null) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: pan.x,
      originY: pan.y,
    };
    setDragging(true);
  };

  const movePan = (event: ReactPointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (drag === null || drag.pointerId !== event.pointerId) return;
    setPan({
      x: drag.originX + (event.clientX - drag.startX) / zoom,
      y: drag.originY + (event.clientY - drag.startY) / zoom,
    });
  };

  const endPan = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    dragRef.current = null;
    setDragging(false);
  };

  const resetView = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };

  const domainConceptIds = [
    "application",
    "underwriting",
    "health-disclosure",
    "beneficiary",
  ] as const;

  return (
    <div className="tap-graph-workspace">
      <aside
        className="tap-graph-communities"
        aria-label={copy.library.communities}
      >
        <h2>{copy.library.communities}</h2>
        <div className="tap-graph-community-list">
          {COMMUNITY_ORDER.map((community) => {
            const count = data.nodes.filter(
              (node) => node.community === community,
            ).length;
            const label = communityLabels[community];
            return (
              <label key={community}>
                <input
                  type="checkbox"
                  checked={activeCommunities.has(community)}
                  aria-label={`${label} · ${count} ${copy.library.nodes}`}
                  onChange={() => toggleCommunity(community)}
                />
                <span
                  className="tap-graph-community-dot"
                  style={
                    {
                      "--tap-community-color": COMMUNITY_COLORS[community],
                    } as CSSProperties
                  }
                  aria-hidden="true"
                />
                <span>{label}</span>
                <small>{count}</small>
              </label>
            );
          })}
        </div>
        <p>{copy.library.graphNavigationHint}</p>
      </aside>

      <figure className="tap-knowledge-graph">
        <div className="tap-graph-toolbar">
          <div className="tap-graph-zoom-controls">
            <button
              type="button"
              aria-label={copy.library.zoomOut}
              disabled={zoom <= MIN_ZOOM}
              onClick={() =>
                setZoom((current) => Math.max(MIN_ZOOM, current - ZOOM_STEP))
              }
            >
              <MinusOutlined aria-hidden="true" />
            </button>
            <span role="status" aria-label={copy.library.zoomLevel}>
              {Math.round(zoom * 100)}%
            </span>
            <button
              type="button"
              aria-label={copy.library.zoomIn}
              disabled={zoom >= MAX_ZOOM}
              onClick={() =>
                setZoom((current) => Math.min(MAX_ZOOM, current + ZOOM_STEP))
              }
            >
              <PlusOutlined aria-hidden="true" />
            </button>
            <button
              type="button"
              aria-label={copy.library.resetView}
              onClick={resetView}
            >
              <AimOutlined aria-hidden="true" />
            </button>
          </div>
        </div>

        <div className="tap-graph-canvas" data-dragging={dragging}>
          <svg
            role="group"
            aria-label={copy.library.knowledgeGraphImage}
            aria-describedby="tap-library-graph-caption tap-library-graph-summary"
            viewBox={`${-GRAPH_HORIZONTAL_MARGIN} 0 ${GRAPH_WIDTH + GRAPH_HORIZONTAL_MARGIN * 2} ${GRAPH_HEIGHT}`}
            preserveAspectRatio="xMidYMid meet"
            onPointerDown={startPan}
            onPointerMove={movePan}
            onPointerUp={endPan}
            onPointerCancel={endPan}
          >
            <title>{copy.library.knowledgeGraphImage}</title>
            <desc>{copy.library.graphSummary}</desc>
            <defs>
              <marker
                id="tap-network-arrow"
                viewBox="0 0 10 10"
                refX="8"
                refY="5"
                markerWidth="5"
                markerHeight="5"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" />
              </marker>
            </defs>

            <g transform={`translate(${pan.x} ${pan.y}) scale(${zoom})`}>
              <g className="tap-graph-edges" aria-hidden="true">
                {visibleEdges.map((edge, index) => {
                  const source = data.nodes.find(
                    (node) => node.id === edge.source,
                  )!;
                  const target = data.nodes.find(
                    (node) => node.id === edge.target,
                  )!;
                  const middleX = (source.x + target.x) / 2;
                  const middleY = (source.y + target.y) / 2;
                  const bend = index % 2 === 0 ? -18 : 18;
                  const active =
                    selectedNodeId === edge.source ||
                    selectedNodeId === edge.target;
                  const showLabel = [
                    "application-health",
                    "health-underwriting",
                    "application-beneficiary",
                  ].includes(edge.id);
                  return (
                    <g
                      key={edge.id}
                      className="tap-graph-edge"
                      data-active={active}
                      data-provenance={edge.provenance}
                    >
                      <path
                        d={`M ${source.x} ${source.y} Q ${middleX} ${middleY + bend} ${target.x} ${target.y}`}
                      />
                      {showLabel ? (
                        <g
                          className="tap-graph-edge-label"
                          transform={`translate(${middleX} ${middleY + bend / 2})`}
                        >
                          <rect
                            x={-(edge.label.length * 3.1 + 8)}
                            y="-9"
                            width={edge.label.length * 6.2 + 16}
                            height="18"
                            rx="9"
                          />
                          <text textAnchor="middle" dominantBaseline="middle">
                            {edge.label}
                          </text>
                        </g>
                      ) : null}
                    </g>
                  );
                })}
              </g>

              <g className="tap-graph-nodes">
                {visibleNodes.map((node) => {
                  const communityLabel = communityLabels[node.community];
                  const kindLabel = kindLabels[node.kind];
                  const highlighted =
                    normalizedQuery.length > 0 &&
                    [node.label, node.secondary, communityLabel, kindLabel]
                      .filter((value): value is string => value !== undefined)
                      .some((value) =>
                        value.toLocaleLowerCase().includes(normalizedQuery),
                      );
                  const dimmed = normalizedQuery.length > 0 && !highlighted;
                  const selected = node.id === selectedNodeId;
                  const radius = 13 + Math.min(node.degree * 2.7, 17);
                  return (
                    <g
                      key={node.id}
                      role="button"
                      tabIndex={0}
                      aria-label={`${node.label} · ${kindLabel} · ${communityLabel}`}
                      aria-pressed={selected}
                      className="tap-graph-node"
                      data-community={node.community}
                      data-highlighted={highlighted}
                      data-dimmed={dimmed}
                      data-selected={selected}
                      onClick={() => setSelectedNodeId(node.id)}
                      onKeyDown={(event) =>
                        selectNodeFromKeyboard(event, node.id)
                      }
                      onPointerDown={(event) => event.stopPropagation()}
                    >
                      <circle
                        className="tap-graph-node-ring"
                        cx={node.x}
                        cy={node.y}
                        r={radius + 7}
                      />
                      <circle
                        className="tap-graph-node-core"
                        cx={node.x}
                        cy={node.y}
                        r={radius}
                        style={
                          {
                            "--tap-community-color":
                              COMMUNITY_COLORS[node.community],
                          } as CSSProperties
                        }
                      />
                      <text
                        className="tap-graph-node-label"
                        x={node.x}
                        y={node.y + radius + 17}
                        textAnchor="middle"
                      >
                        {displayLabel(node.label)}
                      </text>
                      {node.secondary === undefined ? null : (
                        <text
                          className="tap-graph-node-secondary"
                          x={node.x}
                          y={node.y + radius + 30}
                          textAnchor="middle"
                        >
                          {node.secondary}
                        </text>
                      )}
                    </g>
                  );
                })}
              </g>
            </g>
          </svg>
        </div>

        <section
          id="tap-library-graph-summary"
          className="tapper-visually-hidden"
          aria-label={copy.library.graphSummary}
        >
          <h2>{copy.library.graphSummary}</h2>
          <h3>{copy.library.visibleDocuments}</h3>
          <ul aria-label={copy.library.visibleDocuments}>
            {visibleNodes
              .filter((node) => node.kind === "document")
              .map((node) => (
                <li key={node.id}>{node.label}</li>
              ))}
          </ul>
          <h3>{copy.library.concepts}</h3>
          <ul aria-label={copy.library.concepts}>
            {domainConceptIds
              .map((nodeId) => visibleNodes.find((node) => node.id === nodeId))
              .filter((node): node is GraphNode => node !== undefined)
              .map((node) => (
                <li key={node.id}>{node.label}</li>
              ))}
          </ul>
          <h3>{copy.library.labeledRelationships}</h3>
          <ul aria-label={copy.library.labeledRelationships}>
            {visibleEdges.map((edge) => {
              const source = data.nodes.find(
                (node) => node.id === edge.source,
              )!;
              const target = data.nodes.find(
                (node) => node.id === edge.target,
              )!;
              return (
                <li key={edge.id}>
                  {source.label} {edge.label} {target.label}
                </li>
              );
            })}
          </ul>
        </section>

        <figcaption id="tap-library-graph-caption">
          {copy.library.illustrative}
        </figcaption>
      </figure>

      <aside
        className="tap-graph-inspector"
        role="region"
        aria-label={copy.library.nodeDetails}
      >
        <h2>{copy.library.nodeDetails}</h2>
        {selectedNode === null ? (
          <p className="tap-graph-inspector-empty">{copy.library.selectNode}</p>
        ) : (
          <>
            <div className="tap-graph-inspector-title">
              <span
                style={
                  {
                    "--tap-community-color":
                      COMMUNITY_COLORS[selectedNode.community],
                  } as CSSProperties
                }
                aria-hidden="true"
              />
              <div>
                <small>{kindLabels[selectedNode.kind]}</small>
                <h3>{selectedNode.label}</h3>
              </div>
            </div>
            <dl>
              <div>
                <dt>{copy.library.community}</dt>
                <dd>{communityLabels[selectedNode.community]}</dd>
              </div>
              <div>
                <dt>{copy.library.relationships}</dt>
                <dd>
                  {selectedRelationships.length} {copy.library.connections}
                </dd>
              </div>
            </dl>
            <ul className="tap-graph-inspector-relations">
              {selectedRelationships.map((edge) => {
                const otherId =
                  edge.source === selectedNode.id ? edge.target : edge.source;
                const otherNode = data.nodes.find(
                  (node) => node.id === otherId,
                )!;
                return (
                  <li key={edge.id}>
                    <span>{edge.label}</span>
                    <strong>{otherNode.label}</strong>
                  </li>
                );
              })}
            </ul>
            <div className="tap-graph-provenance">
              <span>{copy.library.provenance}</span>
              {(["extracted", "inferred"] as const)
                .filter((provenance) =>
                  selectedRelationships.some(
                    (edge) => edge.provenance === provenance,
                  ),
                )
                .map((provenance) => (
                  <strong key={provenance} data-provenance={provenance}>
                    {provenanceLabels[provenance]}
                  </strong>
                ))}
            </div>
          </>
        )}
      </aside>
    </div>
  );
}
