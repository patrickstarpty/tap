import {
  AimOutlined,
  CloseOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  MenuFoldOutlined,
  MinusOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent, PointerEvent } from "react";

// `import type` only — see the note in `CommunityList.tsx`: type-only
// imports from `widgets/` are not flagged by dependency-cruiser's
// `features-do-not-import-upward` rule, only value imports. This module
// never imports a `widgets/` *value*: the SVG canvas below is intentionally
// self-contained (duplicating, rather than importing,
// `widgets/tap/workspace/KnowledgeGraph.tsx`'s rendering) because that file
// cannot be imported here.
import type { WorkspaceCopy } from "../../../widgets/tap/workspace/copy";

import { useGraphLayout } from "../model/layout";
import {
  useGraphNode,
  useGraphOverview,
  useGraphProject,
  useGraphSearch,
} from "../api/queries";
import { MAX_GRAPH_NODES, OVERVIEW_PAGE } from "../model/graph";
import { CommunityList } from "./CommunityList";
import { OTHER_COMMUNITY_ID, toOverviewData } from "./toOverviewData";

const CANVAS_WIDTH = 1560;
const CANVAS_HEIGHT = 1120;
const CANVAS_HORIZONTAL_MARGIN = 24;
const MIN_ZOOM = 0.75;
const MAX_ZOOM = 1.75;
const ZOOM_STEP = 0.25;
const EDGE_LABEL_ZOOM = 1.25;

function displayLabel(label: string): string {
  return label.length > 27 ? `${label.slice(0, 25)}…` : label;
}

export interface GraphHighlightState {
  edgeIds: ReadonlySet<string>;
  nodeIds: ReadonlySet<string>;
}

export function GraphOverview({
  projectId,
  copy,
  query,
  sourceRevisionIds,
  highlight = null,
  onClearHighlight,
  onAskAboutNode,
  onOpenSource,
}: {
  projectId: string;
  locale: "en" | "zh";
  copy: WorkspaceCopy;
  query: string;
  sourceRevisionIds: readonly string[];
  highlight?: GraphHighlightState | null;
  onClearHighlight?: () => void;
  onAskAboutNode?: (label: string, sourceIds: string[]) => void;
  onOpenSource?: (sourceId: string) => void;
}) {
  const libraryCopy = copy.library;
  const workspaceRef = useRef<HTMLDivElement>(null);
  const [communitiesOpen, setCommunitiesOpen] = useState(
    () =>
      typeof window === "undefined" ||
      !window.matchMedia("(max-width: 820px)").matches,
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

  const [selectedCommunities, setSelectedCommunities] =
    useState<ReadonlySet<string> | null>(null);
  const [nodeLimit, setNodeLimit] = useState(OVERVIEW_PAGE);
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

  const trimmedQuery = query.trim();
  const normalizedQuery = trimmedQuery.toLocaleLowerCase();
  const sortedSourceRevisionIds = useMemo(
    () => [...sourceRevisionIds].sort(),
    [sourceRevisionIds],
  );

  const projectQuery = useGraphProject(projectId);
  const project = projectQuery.data ?? null;
  const graphVersion = project?.graphVersion ?? null;
  // Gate the overview/search/node queries behind the project query so the
  // overview is not fetched before the project's graphVersion is known
  // (which would otherwise double-fetch once the version resolves).
  const gatedProjectId = projectQuery.isSuccess ? projectId : null;

  const communities = project?.communities ?? [];
  const realCommunityIds = useMemo(
    () => communities.map((community) => community.communityId),
    [communities],
  );
  const effectiveSelectedCommunities =
    selectedCommunities ?? new Set([...realCommunityIds, OTHER_COMMUNITY_ID]);
  const communityIdsForRequest = useMemo(() => {
    const allRealSelected = realCommunityIds.every((id) =>
      effectiveSelectedCommunities.has(id),
    );
    return allRealSelected
      ? []
      : realCommunityIds.filter((id) => effectiveSelectedCommunities.has(id));
  }, [realCommunityIds, effectiveSelectedCommunities]);

  const overviewQuery = useGraphOverview(gatedProjectId, graphVersion, {
    sourceRevisionIds: sortedSourceRevisionIds,
    communityIds: communityIdsForRequest,
    nodeLimit,
  });
  const searchQuery = useGraphSearch(
    gatedProjectId,
    graphVersion,
    trimmedQuery,
    sortedSourceRevisionIds,
  );
  const subgraph =
    trimmedQuery.length > 0 ? searchQuery.data : overviewQuery.data;

  const nodeDetailQuery = useGraphNode(
    gatedProjectId,
    graphVersion,
    selectedNodeId,
  );

  const communityOrderIds = useMemo(
    () => communities.map((community) => community.communityId),
    [communities],
  );
  const emptySubgraphNodes = useMemo(() => [], []);
  const emptySubgraphEdges = useMemo(() => [], []);
  const layoutNodes = subgraph?.nodes ?? emptySubgraphNodes;
  const layoutEdges = subgraph?.edges ?? emptySubgraphEdges;
  const positions = useGraphLayout(layoutNodes, layoutEdges, communityOrderIds);

  const otherLabel = libraryCopy.otherCommunity;
  const overview = useMemo(
    () =>
      subgraph
        ? toOverviewData(subgraph, communities, positions, otherLabel)
        : { nodes: [], edges: [], communityOrder: [] },
    [subgraph, communities, positions, otherLabel],
  );

  const previousQuery = useRef(normalizedQuery);
  useEffect(() => {
    if (previousQuery.current && !normalizedQuery) {
      setSelectedNodeId(null);
      setZoom(1);
      setPan({ x: 0, y: 0 });
    }
    previousQuery.current = normalizedQuery;
  }, [normalizedQuery]);

  const visibleNodes = overview.nodes.filter((node) =>
    effectiveSelectedCommunities.has(node.community),
  );
  const visibleNodeIds = new Set(visibleNodes.map((node) => node.id));
  const visibleEdges = overview.edges.filter(
    (edge) =>
      visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target),
  );
  const selectedNode =
    visibleNodes.find((node) => node.id === selectedNodeId) ?? null;

  useEffect(() => {
    if (
      selectedNodeId !== null &&
      !visibleNodes.some((node) => node.id === selectedNodeId)
    ) {
      setSelectedNodeId(null);
    }
  }, [selectedNodeId, visibleNodes.map((node) => node.id).join("|")]);

  const matchesQuery = (node: (typeof overview.nodes)[number]) =>
    [node.label, node.communityLabel, node.nodeType, ...node.aliases].some(
      (value) => value?.toLocaleLowerCase().includes(normalizedQuery),
    );
  const matchingNodes =
    normalizedQuery.length > 0 ? overview.nodes.filter(matchesQuery) : [];

  const locateNode = (node: (typeof overview.nodes)[number]) => {
    setSelectedNodeId(node.id);
    const nextZoom = 1.5;
    setZoom(nextZoom);
    setPan({
      x: CANVAS_WIDTH / 2 - node.x * nextZoom,
      y: CANVAS_HEIGHT / 2 - node.y * nextZoom,
    });
  };

  const focusedNodeId = hoveredNodeId ?? selectedNode?.id;
  const neighborhood = new Set([focusedNodeId]);
  for (const edge of visibleEdges) {
    if (edge.source === focusedNodeId) neighborhood.add(edge.target);
    if (edge.target === focusedNodeId) neighborhood.add(edge.source);
  }

  const toggleCommunity = (communityId: string) => {
    setSelectedCommunities((current) => {
      const base = current ?? effectiveSelectedCommunities;
      const next = new Set(base);
      if (next.has(communityId)) next.delete(communityId);
      else next.add(communityId);
      return next;
    });
  };
  const selectAllCommunities = (all: boolean) => {
    setSelectedCommunities(
      all ? new Set([...realCommunityIds, OTHER_COMMUNITY_ID]) : new Set(),
    );
  };

  const selectNodeFromKeyboard = (
    event: KeyboardEvent<SVGGElement>,
    nodeId: string,
  ) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    setSelectedNodeId(nodeId);
  };

  const startPan = (event: PointerEvent<SVGSVGElement>) => {
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
  const movePan = (event: PointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (drag === null || drag.pointerId !== event.pointerId) return;
    setPan({
      x: drag.originX + (event.clientX - drag.startX) / zoom,
      y: drag.originY + (event.clientY - drag.startY) / zoom,
    });
  };
  const endPan = (event: PointerEvent<SVGSVGElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    dragRef.current = null;
    setDragging(false);
  };
  const resetView = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };

  const extractingCount = sourceRevisionIds.filter((id) =>
    (project?.extractingRevisionIds ?? []).includes(id),
  ).length;
  const partialCount = sourceRevisionIds.filter((id) =>
    (project?.partialRevisionIds ?? []).includes(id),
  ).length;

  const nodeTypeGroups = useMemo(() => {
    const groups = new Map<string, string[]>();
    for (const node of visibleNodes) {
      const members = groups.get(node.nodeType) ?? [];
      members.push(node.label);
      groups.set(node.nodeType, members);
    }
    return groups;
  }, [visibleNodes]);

  const nodeDetail = nodeDetailQuery.data;
  const nodeDetailSourceIds =
    nodeDetail?.sources?.map((source) => source.sourceRevisionId) ?? [];

  if (projectQuery.isPending) {
    return <p role="status">{libraryCopy.graphSummary}</p>;
  }
  if (projectQuery.isError) {
    return <p role="alert">{libraryCopy.graphEmpty}</p>;
  }
  if (project === null || project.nodeCount === 0) {
    return (
      <div className="tap-graph-empty">
        <p>{libraryCopy.graphEmpty}</p>
        <p>{libraryCopy.graphEmptyHint}</p>
      </div>
    );
  }

  return (
    <div
      ref={workspaceRef}
      className="tap-graph-workspace"
      data-communities-open={communitiesOpen || normalizedQuery.length > 0}
      data-inspector-open={selectedNode !== null}
    >
      {communitiesOpen && normalizedQuery.length === 0 ? (
        <CommunityList
          communities={overview.communityOrder}
          selected={effectiveSelectedCommunities}
          onToggle={toggleCommunity}
          onSelectAll={selectAllCommunities}
          footer={{ extracting: extractingCount, partial: partialCount }}
          copy={libraryCopy}
        />
      ) : null}

      {normalizedQuery ? (
        <section
          className="tap-graph-search-results"
          role="region"
          aria-label={libraryCopy.searchResults}
        >
          <h2>
            {libraryCopy.searchResults}{" "}
            <span aria-live="polite">{matchingNodes.length}</span>
          </h2>
          {matchingNodes.length === 0 ? (
            <p>{libraryCopy.noMatchingNodes}</p>
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
                      {node.nodeType} · {node.communityLabel}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : null}

      {project.status === "MERGING" ? (
        <p role="status">{libraryCopy.graphMerging}</p>
      ) : null}

      <figure className="tap-knowledge-graph">
        <div className="tap-graph-toolbar">
          <div className="tap-graph-zoom-controls">
            <button
              type="button"
              aria-label={libraryCopy.toggleCommunities}
              title={libraryCopy.toggleCommunities}
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
                  ? libraryCopy.exitFullscreen
                  : libraryCopy.enterFullscreen
              }
              title={
                fullscreen
                  ? libraryCopy.exitFullscreen
                  : libraryCopy.enterFullscreen
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
              aria-label={libraryCopy.zoomOut}
              disabled={zoom <= MIN_ZOOM}
              onClick={() =>
                setZoom((current) => Math.max(MIN_ZOOM, current - ZOOM_STEP))
              }
            >
              <MinusOutlined aria-hidden="true" />
            </button>
            <span role="status" aria-label={libraryCopy.zoomLevel}>
              {Math.round(zoom * 100)}%
            </span>
            <button
              type="button"
              aria-label={libraryCopy.zoomIn}
              disabled={zoom >= MAX_ZOOM}
              onClick={() =>
                setZoom((current) => Math.min(MAX_ZOOM, current + ZOOM_STEP))
              }
            >
              <PlusOutlined aria-hidden="true" />
            </button>
            <button
              type="button"
              aria-label={libraryCopy.resetView}
              onClick={resetView}
            >
              <AimOutlined aria-hidden="true" />
            </button>
          </div>
        </div>

        {fullscreenError ? (
          <p role="status">{libraryCopy.fullscreenUnavailable}</p>
        ) : null}

        <div className="tap-graph-canvas" data-dragging={dragging}>
          <svg
            role="group"
            aria-label={libraryCopy.knowledgeGraphImage}
            aria-describedby="tap-library-graph-caption tap-library-graph-summary"
            viewBox={`${-CANVAS_HORIZONTAL_MARGIN} 0 ${CANVAS_WIDTH + CANVAS_HORIZONTAL_MARGIN * 2} ${CANVAS_HEIGHT}`}
            preserveAspectRatio="xMidYMid meet"
            onPointerDown={startPan}
            onPointerMove={movePan}
            onPointerUp={endPan}
            onPointerCancel={endPan}
          >
            <title>{libraryCopy.knowledgeGraphImage}</title>
            <desc>{libraryCopy.graphSummary}</desc>
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
                {visibleEdges.map((edge) => {
                  const source = visibleNodes.find(
                    (node) => node.id === edge.source,
                  );
                  const target = visibleNodes.find(
                    (node) => node.id === edge.target,
                  );
                  if (!source || !target) return null;
                  const middleX = (source.x + target.x) / 2;
                  const middleY = (source.y + target.y) / 2;
                  const active =
                    focusedNodeId === edge.source ||
                    focusedNodeId === edge.target;
                  const showLabel = active || zoom >= EDGE_LABEL_ZOOM;
                  return (
                    <g
                      key={edge.id}
                      className="tap-graph-edge"
                      data-active={active}
                      data-muted={Boolean(focusedNodeId) && !active}
                      style={
                        {
                          "--tap-edge-color": target.color,
                        } as CSSProperties
                      }
                      data-provenance={edge.provenance}
                    >
                      <path
                        d={`M ${source.x} ${source.y} L ${target.x} ${target.y}`}
                      />
                      {showLabel ? (
                        <g
                          className="tap-graph-edge-label"
                          transform={`translate(${middleX} ${middleY})`}
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
                  const highlighted =
                    normalizedQuery.length > 0 && matchesQuery(node);
                  const dimmed =
                    normalizedQuery.length > 0
                      ? !highlighted
                      : Boolean(focusedNodeId) && !neighborhood.has(node.id);
                  const selected = node.id === selectedNodeId;
                  const radius = 12 + Math.min(node.degree, 20) * 0.9;
                  return (
                    <g
                      key={node.id}
                      role="button"
                      tabIndex={0}
                      aria-label={`${node.label} · ${node.nodeType} · ${node.communityLabel}`}
                      aria-pressed={selected}
                      className="tap-graph-node"
                      style={
                        { "--tap-community-color": node.color } as CSSProperties
                      }
                      data-community={node.community}
                      data-node-type={node.nodeType}
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
                      <circle
                        className="tap-graph-node-core"
                        cx={node.x}
                        cy={node.y}
                        r={radius}
                        style={
                          {
                            "--tap-community-color": node.color,
                          } as CSSProperties
                        }
                      />
                      <text
                        className="tap-graph-node-label"
                        x={node.x}
                        y={node.y + radius + 16}
                        textAnchor="middle"
                      >
                        {displayLabel(node.label)}
                      </text>
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
          aria-label={libraryCopy.graphSummary}
        >
          <h2>{libraryCopy.graphSummary}</h2>
          {[...nodeTypeGroups.entries()].map(([nodeType, labels]) => (
            <div key={nodeType}>
              <h3>
                {copy.library.nodeTypes[
                  nodeType as keyof typeof copy.library.nodeTypes
                ] ?? nodeType}
              </h3>
              <ul
                aria-label={
                  copy.library.nodeTypes[
                    nodeType as keyof typeof copy.library.nodeTypes
                  ] ?? nodeType
                }
              >
                {labels.map((label) => (
                  <li key={label}>{label}</li>
                ))}
              </ul>
            </div>
          ))}
          <h3>{libraryCopy.labeledRelationships}</h3>
          <ul aria-label={libraryCopy.labeledRelationships}>
            {visibleEdges.map((edge) => {
              const source = visibleNodes.find(
                (node) => node.id === edge.source,
              );
              const target = visibleNodes.find(
                (node) => node.id === edge.target,
              );
              if (!source || !target) return null;
              return (
                <li key={edge.id}>
                  {source.label} {edge.label} {target.label}
                </li>
              );
            })}
          </ul>
        </section>

        <figcaption id="tap-library-graph-caption">
          {libraryCopy.overviewCaption}
        </figcaption>

        {overview.nodes.length === nodeLimit ? (
          <button
            type="button"
            onClick={() =>
              setNodeLimit((current) =>
                Math.min(MAX_GRAPH_NODES, current + OVERVIEW_PAGE),
              )
            }
          >
            {libraryCopy.loadMore}
          </button>
        ) : null}
      </figure>

      {highlight && onClearHighlight ? (
        <section role="region" aria-label="Highlighted path / 高亮路径">
          <button type="button" onClick={onClearHighlight}>
            {libraryCopy.resetView}
          </button>
        </section>
      ) : null}

      <aside
        className="tap-graph-inspector"
        hidden={selectedNode === null}
        role="region"
        aria-label={libraryCopy.nodeDetails}
      >
        <header>
          <h2>{libraryCopy.nodeDetails}</h2>
          <button
            type="button"
            aria-label={libraryCopy.closeNodeDetails}
            onClick={() => setSelectedNodeId(null)}
          >
            <CloseOutlined aria-hidden="true" />
          </button>
        </header>
        {selectedNode === null ? (
          <p className="tap-graph-inspector-empty">{libraryCopy.selectNode}</p>
        ) : (
          <>
            <div className="tap-graph-inspector-title">
              <span
                style={
                  {
                    "--tap-community-color": selectedNode.color,
                  } as CSSProperties
                }
                aria-hidden="true"
              />
              <div>
                <small>{selectedNode.nodeType}</small>
                <h3>{selectedNode.label}</h3>
              </div>
            </div>
            <dl>
              <div>
                <dt>{libraryCopy.community}</dt>
                <dd>{selectedNode.communityLabel}</dd>
              </div>
              <div>
                <dt>{libraryCopy.relationships}</dt>
                <dd>
                  {
                    visibleEdges.filter(
                      (edge) =>
                        edge.source === selectedNode.id ||
                        edge.target === selectedNode.id,
                    ).length
                  }{" "}
                  {libraryCopy.connections}
                </dd>
              </div>
            </dl>
            {selectedNode.aliases.length > 0 ? (
              <p>{selectedNode.aliases.join(", ")}</p>
            ) : null}
            <ul className="tap-graph-inspector-relations">
              {visibleEdges
                .filter(
                  (edge) =>
                    edge.source === selectedNode.id ||
                    edge.target === selectedNode.id,
                )
                .map((edge) => {
                  const otherId =
                    edge.source === selectedNode.id ? edge.target : edge.source;
                  const otherNode = visibleNodes.find(
                    (node) => node.id === otherId,
                  );
                  return (
                    <li key={edge.id}>
                      <span>{edge.label}</span>
                      <strong>{otherNode?.label ?? otherId}</strong>
                    </li>
                  );
                })}
            </ul>
            {onAskAboutNode ? (
              <button
                type="button"
                onClick={() =>
                  onAskAboutNode(selectedNode.label, nodeDetailSourceIds)
                }
              >
                {copy.chat.messageTapper}
              </button>
            ) : null}
            {onOpenSource && nodeDetail
              ? nodeDetail.sources?.map((source) => (
                  <button
                    key={source.sourceRevisionId}
                    type="button"
                    onClick={() => onOpenSource(source.sourceRevisionId)}
                  >
                    {libraryCopy.viewSource}
                  </button>
                ))
              : null}
          </>
        )}
      </aside>
    </div>
  );
}
