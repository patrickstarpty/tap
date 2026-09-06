import {
  AimOutlined,
  MinusOutlined,
  PlusOutlined,
  FullscreenOutlined,
  FullscreenExitOutlined,
  MenuFoldOutlined,
  CloseOutlined,
} from "@ant-design/icons";
import { useEffect, useMemo, useRef, useState } from "react";
import type {
  CSSProperties,
  KeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from "react";

import type { PrototypeCopy } from "./copy";
import type { LibrarySource } from "./model";

import {
  buildKnowledgeGraph,
  GRAPH_WIDTH,
  GRAPH_HEIGHT,
  GRAPH_CLUSTERS,
  COMMUNITY_ORDER,
  COMMUNITY_COLORS,
  type GraphCommunity,
  type GraphNodeKind,
  type GraphProvenance,
  type GraphNode,
} from "./knowledgeGraphData";

const GRAPH_HORIZONTAL_MARGIN = 24;
const MIN_ZOOM = 0.75;
const MAX_ZOOM = 1.75;
const ZOOM_STEP = 0.25;

function displayLabel(label: string): string {
  return label.length > 27 ? `${label.slice(0, 25)}…` : label;
}

export function KnowledgeGraph({
  copy,
  query,
  sources,
  onViewSource,
}: {
  copy: PrototypeCopy;
  query: string;
  sources: readonly LibrarySource[];
  onViewSource: (source: LibrarySource) => void;
}) {
  const workspaceRef = useRef<HTMLDivElement>(null);
  const [communitiesOpen, setCommunitiesOpen] = useState(
    () => !window.matchMedia("(max-width: 820px)").matches,
  );
  const [fullscreen, setFullscreen] = useState(false);
  const [fullscreenError, setFullscreenError] = useState(false);
  useEffect(() => {
    const update = () =>
      setFullscreen(document.fullscreenElement === workspaceRef.current);
    document.addEventListener("fullscreenchange", update);
    return () => document.removeEventListener("fullscreenchange", update);
  }, []);
  const toggleFullscreen = async () => {
    setFullscreenError(false);
    try {
      if (document.fullscreenElement === workspaceRef.current)
        await document.exitFullscreen();
      else await workspaceRef.current?.requestFullscreen();
    } catch {
      setFullscreenError(true);
    }
  };
  const data = useMemo(
    () => buildKnowledgeGraph(copy, sources),
    [copy, sources],
  );
  const [activeCommunities, setActiveCommunities] = useState<
    ReadonlySet<GraphCommunity>
  >(() => new Set(COMMUNITY_ORDER));
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
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
    testing: copy.library.testingCommunity,
    "new-business": copy.library.newBusinessCommunity,
    servicing: copy.library.servicingCommunity,
    claims: copy.library.claimsCommunity,
    codebase: copy.library.codebaseCommunity,
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
  const selectedSource = sources.find(
    (source) => `source-${source.id}` === selectedNodeId,
  );
  const matchesQuery = (node: GraphNode) => {
    const source = sources.find((item) => `source-${item.id}` === node.id);
    return [
      node.label,
      node.secondary,
      source?.description,
      communityLabels[node.community],
      kindLabels[node.kind],
    ].some((value) => value?.toLocaleLowerCase().includes(normalizedQuery));
  };
  const matchingNodes = data.nodes.filter(matchesQuery);
  const previousQuery = useRef(normalizedQuery);
  useEffect(() => {
    if (previousQuery.current && !normalizedQuery) {
      setSelectedNodeId(null);
      setZoom(1);
      setPan({ x: 0, y: 0 });
    }
    previousQuery.current = normalizedQuery;
  }, [normalizedQuery]);
  const locateNode = (node: GraphNode) => {
    setActiveCommunities((current) => new Set([...current, node.community]));
    setSelectedNodeId(node.id);
    const nextZoom = 1.5;
    setZoom(nextZoom);
    setPan({
      x: GRAPH_WIDTH / 2 - node.x * nextZoom,
      y: GRAPH_HEIGHT / 2 - node.y * nextZoom,
    });
    requestAnimationFrame(() => {
      const canvas =
        workspaceRef.current?.querySelector<HTMLElement>(".tap-graph-canvas");
      if (canvas)
        canvas.scrollLeft = (canvas.scrollWidth - canvas.clientWidth) / 2;
    });
  };
  const focusedNodeId = hoveredNodeId ?? selectedNode?.id;
  const neighborhood = new Set([focusedNodeId]);
  for (const edge of visibleEdges) {
    if (edge.source === focusedNodeId) neighborhood.add(edge.target);
    if (edge.target === focusedNodeId) neighborhood.add(edge.source);
  }
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
    "approval",
    "test-cases",
    "exploration",
    "new-business",
    "policy-servicing",
    "claims",
    "codebase",
  ] as const;

  const edgeLabelBoxes: { x: number; y: number; width: number }[] = [];
  return (
    <div
      ref={workspaceRef}
      className="tap-graph-workspace"
      data-communities-open={communitiesOpen || normalizedQuery.length > 0}
      data-inspector-open={selectedNode !== null}
    >
      <aside
        className="tap-graph-communities"
        hidden={!communitiesOpen || normalizedQuery.length > 0}
        aria-label={copy.library.communities}
      >
        <h2>{copy.library.communities}</h2>
        <label className="tap-graph-select-all">
          <input
            type="checkbox"
            checked={activeCommunities.size === COMMUNITY_ORDER.length}
            ref={(input) => {
              if (input)
                input.indeterminate =
                  activeCommunities.size > 0 &&
                  activeCommunities.size < COMMUNITY_ORDER.length;
            }}
            onChange={(event) =>
              setActiveCommunities(
                new Set(event.target.checked ? COMMUNITY_ORDER : []),
              )
            }
          />
          <span>{copy.library.selectAllTopics}</span>
        </label>
        <div className="tap-graph-community-list">
          {COMMUNITY_ORDER.map((community) => {
            const count = data.nodes.filter(
              (node) => node.community === community,
            ).length;
            const label = communityLabels[community];
            return (
              <label
                key={community}
                style={
                  {
                    "--tap-community-color": COMMUNITY_COLORS[community],
                  } as CSSProperties
                }
              >
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
                <span className="tap-graph-community-name" title={label}>
                  {label}
                </span>
                <small>{count}</small>
              </label>
            );
          })}
        </div>
        <p>{copy.library.graphNavigationHint}</p>
      </aside>

      {normalizedQuery ? (
        <section
          className="tap-graph-search-results"
          role="region"
          aria-label={copy.library.searchResults}
        >
          <h2>
            {copy.library.searchResults}{" "}
            <span aria-live="polite">{matchingNodes.length}</span>
          </h2>
          {matchingNodes.length === 0 ? (
            <p>{copy.library.noMatchingNodes}</p>
          ) : (
            <ul>
              {matchingNodes.map((node) => (
                <li key={node.id}>
                  <button
                    type="button"
                    aria-pressed={selectedNodeId === node.id}
                    onClick={() => locateNode(node)}
                  >
                    <strong>{node.label}</strong>
                    <span>
                      {kindLabels[node.kind]} ·{" "}
                      {communityLabels[node.community]}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : null}

      <figure className="tap-knowledge-graph">
        <div className="tap-graph-toolbar">
          <div className="tap-graph-zoom-controls">
            <button
              type="button"
              aria-label={copy.library.toggleCommunities}
              title={copy.library.toggleCommunities}
              aria-expanded={communitiesOpen}
              disabled={normalizedQuery.length > 0}
              onClick={() => setCommunitiesOpen(!communitiesOpen)}
            >
              <MenuFoldOutlined aria-hidden="true" />
            </button>
            <button
              type="button"
              aria-label={
                fullscreen
                  ? copy.library.exitFullscreen
                  : copy.library.enterFullscreen
              }
              title={
                fullscreen
                  ? copy.library.exitFullscreen
                  : copy.library.enterFullscreen
              }
              onClick={() => void toggleFullscreen()}
            >
              {fullscreen ? (
                <FullscreenExitOutlined aria-hidden="true" />
              ) : (
                <FullscreenOutlined aria-hidden="true" />
              )}
            </button>
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

        {fullscreenError ? (
          <p role="status">{copy.library.fullscreenUnavailable}</p>
        ) : null}
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
              <g className="tap-graph-clusters" aria-hidden="true">
                {GRAPH_CLUSTERS.filter((cluster) =>
                  activeCommunities.has(cluster.community),
                ).map((cluster) => (
                  <g
                    key={cluster.community}
                    style={
                      {
                        "--tap-community-color":
                          COMMUNITY_COLORS[cluster.community],
                      } as CSSProperties
                    }
                  >
                    <ellipse
                      cx={cluster.x}
                      cy={cluster.y}
                      rx={cluster.community === "testing" ? 310 : 240}
                      ry="168"
                    />
                    <text x={cluster.x - 210} y={cluster.y - 149}>
                      {communityLabels[cluster.community]}
                    </text>
                  </g>
                ))}
              </g>
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
                  const bend = index % 2 === 0 ? -28 : 28;
                  const active =
                    focusedNodeId === edge.source ||
                    focusedNodeId === edge.target;
                  const showLabel =
                    (active &&
                      (source.kind !== "document" ||
                        focusedNodeId === source.id)) ||
                    (!focusedNodeId &&
                      [
                        "application-health",
                        "health-underwriting",
                        "application-beneficiary",
                        "test-allocation",
                        "execution-defect",
                      ].includes(edge.id));
                  let labelX = middleX;
                  let labelY = middleY + bend / 2;
                  const halfWidth = edge.label.length * 4 + 10;
                  if (showLabel) {
                    const dx = target.x - source.x,
                      dy = target.y - source.y;
                    const length = Math.hypot(dx, dy) || 1;
                    const candidates = [0, 32, -32, 64, -64, 96, -96].map(
                      (offset) => ({
                        x: middleX - (dy / length) * offset,
                        y: middleY + (dx / length) * offset,
                      }),
                    );
                    const score = (point: { x: number; y: number }) =>
                      visibleNodes.reduce((count, node) => {
                        const doc = node.kind === "document";
                        const overlapsMarker =
                          Math.abs(point.x - node.x) <
                            halfWidth + (doc ? 28 : 38) &&
                          Math.abs(point.y - node.y) < (doc ? 32 : 48);
                        const overlapsName =
                          !doc &&
                          Math.abs(point.x - node.x) <
                            halfWidth +
                              Math.min(node.label.length * 4.8, 140) &&
                          Math.abs(point.y - node.y - 53) < 27;
                        return count + Number(overlapsMarker || overlapsName);
                      }, 0) +
                      edgeLabelBoxes.filter(
                        (box) =>
                          Math.abs(box.x - point.x) <
                            halfWidth + box.width + 8 &&
                          Math.abs(box.y - point.y) < 32,
                      ).length;
                    candidates.sort((a, b) => score(a) - score(b));
                    labelX = candidates[0]!.x;
                    labelY = candidates[0]!.y;
                    edgeLabelBoxes.push({
                      x: labelX,
                      y: labelY,
                      width: halfWidth,
                    });
                  }
                  return (
                    <g
                      key={edge.id}
                      className="tap-graph-edge"
                      data-active={active}
                      data-muted={Boolean(focusedNodeId) && !active}
                      data-document={source.kind === "document"}
                      style={
                        {
                          "--tap-edge-color":
                            COMMUNITY_COLORS[target.community],
                        } as CSSProperties
                      }
                      data-provenance={edge.provenance}
                    >
                      <path
                        d={`M ${source.x} ${source.y} Q ${showLabel ? 2 * labelX - middleX : middleX} ${showLabel ? 2 * labelY - middleY : middleY + bend} ${target.x} ${target.y}`}
                      />
                      {showLabel ? (
                        <g
                          className="tap-graph-edge-label"
                          transform={`translate(${labelX} ${labelY})`}
                        >
                          <rect
                            x={-(edge.label.length * 4 + 10)}
                            y="-12"
                            width={edge.label.length * 8 + 20}
                            height="24"
                            rx="12"
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
                    normalizedQuery.length > 0 && matchesQuery(node);
                  const dimmed =
                    normalizedQuery.length > 0
                      ? !highlighted
                      : Boolean(focusedNodeId) && !neighborhood.has(node.id);
                  const selected = node.id === selectedNodeId;
                  const document = node.kind === "document";
                  const radius = document
                    ? 17
                    : 21 + Math.min(node.degree * 1.4, 10);
                  const showName =
                    !document ||
                    sources.length <= 5 ||
                    (node.id.startsWith("source-fwd-") &&
                      [
                        "new-business",
                        "servicing",
                        "claims",
                        "codebase",
                      ].includes(node.community)) ||
                    highlighted ||
                    node.id === focusedNodeId;
                  return (
                    <g
                      key={node.id}
                      role="button"
                      tabIndex={0}
                      aria-label={`${node.label} · ${kindLabel} · ${communityLabel}`}
                      aria-pressed={selected}
                      className="tap-graph-node"
                      style={
                        {
                          "--tap-community-color":
                            COMMUNITY_COLORS[node.community],
                        } as CSSProperties
                      }
                      data-community={node.community}
                      data-kind={node.kind}
                      onMouseEnter={() => setHoveredNodeId(node.id)}
                      onMouseLeave={() => setHoveredNodeId(null)}
                      onFocus={() => setHoveredNodeId(node.id)}
                      onBlur={() => setHoveredNodeId(null)}
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
                      {document ? (
                        <g className="tap-graph-document-marker">
                          <rect
                            x={node.x - 23}
                            y={node.y - 16}
                            width="46"
                            height="32"
                            rx="8"
                          />
                          <text
                            x={node.x}
                            y={node.y + 1}
                            textAnchor="middle"
                            dominantBaseline="middle"
                          >
                            {node.secondary}
                          </text>
                        </g>
                      ) : (
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
                      )}

                      {showName ? (
                        <g className="tap-graph-label-group">
                          {document ? (
                            <rect
                              className="tap-graph-file-label-bg"
                              x={node.x - 133}
                              y={node.y + radius + 6}
                              width="266"
                              height="30"
                              rx="6"
                            />
                          ) : null}
                          <text
                            className="tap-graph-node-label"
                            x={node.x}
                            y={node.y + radius + 26}
                            textAnchor="middle"
                          >
                            {displayLabel(node.label)}
                          </text>
                        </g>
                      ) : null}
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
        hidden={selectedNode === null}
        role="region"
        aria-label={copy.library.nodeDetails}
      >
        <header>
          <h2>{copy.library.nodeDetails}</h2>
          <button
            type="button"
            aria-label={copy.library.closeNodeDetails}
            onClick={() => {
              workspaceRef.current
                ?.querySelector<SVGElement>(
                  '.tap-graph-node[data-selected="true"]',
                )
                ?.focus();
              setSelectedNodeId(null);
            }}
          >
            <CloseOutlined aria-hidden="true" />
          </button>
        </header>
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
            {selectedSource ? (
              <div className="tap-graph-source-detail">
                <p>{selectedSource.description}</p>
                <button
                  type="button"
                  onClick={() => onViewSource(selectedSource)}
                >
                  {copy.library.viewSource}
                </button>
              </div>
            ) : null}
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
