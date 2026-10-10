import {
  AimOutlined,
  MinusOutlined,
  PlusOutlined,
  FullscreenOutlined,
  FullscreenExitOutlined,
  MenuFoldOutlined,
} from "@ant-design/icons";
import { useEffect, useMemo, useRef, useState } from "react";
import type {
  CSSProperties,
  KeyboardEvent,
  PointerEvent as ReactPointerEvent,
  ReactNode,
} from "react";

import { CommunityList } from "../../../features/graph/components/CommunityList";
import type { WorkspaceCopy } from "./copy";
import {
  GRAPH_WIDTH,
  GRAPH_HEIGHT,
  type GraphEdge,
  type GraphNode,
} from "./graphLayout";

const GRAPH_HORIZONTAL_MARGIN = 24;
const MIN_ZOOM = 0.75;
const MAX_ZOOM = 1.75;
export const EDGE_LABEL_ZOOM = 1.25;
// Padding (canvas units) added around a highlighted path's bounding box
// before computing the fit-to-viewport zoom, so the fitted nodes are never
// flush against the canvas edge.
const FIT_TO_NODES_PADDING = 240;

function displayLabel(label: string): string {
  return label.length > 27 ? `${label.slice(0, 25)}…` : label;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/**
 * Computes the zoom/pan that fits `nodeIds`' bounding box (looked up by
 * position in `nodes`) into the canvas, centered. A node id with no match
 * in `nodes` (e.g. outside the currently drawn overview) is simply
 * skipped — `null` is returned only when none of `nodeIds` are found, so
 * the view is left unchanged rather than fit to an empty box.
 */
function fitToNodes(
  nodes: GraphNode[],
  nodeIds: ReadonlySet<string>,
): { zoom: number; pan: { x: number; y: number } } | null {
  const points = nodes.filter((node) => nodeIds.has(node.id));
  if (points.length === 0) return null;
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const width = maxX - minX;
  const height = maxY - minY;
  const zoom = clamp(
    Math.min(
      GRAPH_WIDTH / (width + FIT_TO_NODES_PADDING),
      GRAPH_HEIGHT / (height + FIT_TO_NODES_PADDING),
    ),
    MIN_ZOOM,
    MAX_ZOOM,
  );
  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2;
  return {
    zoom,
    pan: {
      x: GRAPH_WIDTH / 2 - centerX * zoom,
      y: GRAPH_HEIGHT / 2 - centerY * zoom,
    },
  };
}

function nodeTypeLabel(copy: WorkspaceCopy, nodeType: string): string {
  return (
    copy.library.nodeTypes[nodeType as keyof typeof copy.library.nodeTypes] ??
    nodeType
  );
}

export interface KnowledgeGraphProps {
  copy: WorkspaceCopy;
  nodes: GraphNode[];
  edges: GraphEdge[];
  communities: {
    communityId: string;
    label: string;
    color: string;
    size: number;
  }[];
  activeCommunities: ReadonlySet<string>;
  onToggleCommunity: (communityId: string) => void;
  onSelectAllCommunities: (all: boolean) => void;
  // Rendered inside the community column (via `CommunityList`'s own
  // `footer`/`notice` slots) rather than as a bare, unplaced child of the
  // 3-column `.tap-graph-workspace` grid.
  communitiesFooter: { extracting: number; partial: number };
  communitiesNotice?: ReactNode;
  searchQuery: string;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string | null) => void;
  highlight?: {
    edgeIds: ReadonlySet<string>;
    nodeIds: ReadonlySet<string>;
  } | null;
  detailPanel: ReactNode;
  caption: string;
  onLoadMore?: () => void;
  loadMoreLabel?: string;
}

/**
 * The single, data-agnostic SVG canvas: it renders whatever
 * `nodes`/`edges`/`communities` it is given, with no built-in notion of a
 * fixed topic or node-type taxonomy. Data fetching, community-filter state
 * and node-detail content all live with the caller (e.g.
 * `features/graph/components/GraphOverview.tsx`, injected as its `Canvas`
 * prop via a type-only import of `KnowledgeGraphProps` — `features/` may
 * not import a `widgets/` value, but this widget may be, and is, injected
 * from `LibraryWorkspace.tsx`).
 */
export function KnowledgeGraph({
  copy,
  nodes,
  edges,
  communities,
  activeCommunities,
  onToggleCommunity,
  onSelectAllCommunities,
  communitiesFooter,
  communitiesNotice = null,
  searchQuery,
  selectedNodeId,
  onSelectNode,
  highlight = null,
  detailPanel,
  caption,
  onLoadMore,
  loadMoreLabel,
}: KnowledgeGraphProps) {
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
  const normalizedQuery = searchQuery.trim().toLocaleLowerCase();
  const highlightActive = highlight != null;

  // Fit the viewport to the highlighted path — keyed off content
  // signatures (not object identity, since the caller recomputes a new
  // `nodeIds`/`edgeIds` Set and `nodes` array on most renders even when
  // nothing relevant actually changed) for both *which* nodes are
  // highlighted and *where* they currently sit: the seeded fallback layout
  // (`useGraphLayout`'s `seededFallback`) is immediately available but
  // provisional, and gets replaced by the force-layout worker's refined
  // positions a little later for whichever nodes it has resolved — a fit
  // computed from the provisional positions must be redone once the real
  // ones land, or the "highlighted path" can end up off-center or cropped.
  // That re-fit is skipped once the user has manually panned or zoomed
  // since the current highlight was applied (`interactionRef`) — a later
  // position update must not yank the view back out from under them.
  const highlightSignature = highlight
    ? [...highlight.nodeIds].sort().join(",")
    : null;
  const highlightPositionsSignature = highlight
    ? nodes
        .filter((node) => highlight.nodeIds.has(node.id))
        .map((node) => `${node.id}:${node.x.toFixed(1)}:${node.y.toFixed(1)}`)
        .sort()
        .join(",")
    : null;
  const interactionRef = useRef<{
    signature: string | null;
    interacted: boolean;
  }>({ signature: null, interacted: false });
  const markViewInteraction = () => {
    if (interactionRef.current.signature === highlightSignature) {
      interactionRef.current.interacted = true;
    }
  };
  useEffect(() => {
    if (highlightSignature === null || highlight == null) {
      interactionRef.current = { signature: null, interacted: false };
      return;
    }
    if (interactionRef.current.signature !== highlightSignature) {
      interactionRef.current = {
        signature: highlightSignature,
        interacted: false,
      };
    }
    if (interactionRef.current.interacted) return;
    const fit = fitToNodes(nodes, highlight.nodeIds);
    if (fit === null) return;
    setZoom(fit.zoom);
    setPan(fit.pan);
    // `nodes`/`highlight` are intentionally excluded: this must run only
    // when the highlighted set or its resolved positions change
    // (`highlightSignature`/`highlightPositionsSignature`), not on every
    // re-render that passes a new-but-equal object/array.
  }, [highlightSignature, highlightPositionsSignature]);

  const visibleNodes = useMemo(
    () => nodes.filter((node) => activeCommunities.has(node.community)),
    [nodes, activeCommunities],
  );
  const nodeById = useMemo(
    () => new Map(visibleNodes.map((node) => [node.id, node])),
    [visibleNodes],
  );
  const visibleEdges = useMemo(
    () =>
      edges.filter(
        (edge) => nodeById.has(edge.source) && nodeById.has(edge.target),
      ),
    [edges, nodeById],
  );
  const selectedNode = selectedNodeId
    ? (nodeById.get(selectedNodeId) ?? null)
    : null;
  const matchesQuery = (node: GraphNode) =>
    [
      node.label,
      node.communityLabel,
      node.nodeType,
      nodeTypeLabel(copy, node.nodeType),
      ...node.aliases,
    ].some((value) => value?.toLocaleLowerCase().includes(normalizedQuery));
  const matchingNodes = nodes.filter(matchesQuery);

  const focusedNodeId = hoveredNodeId ?? selectedNode?.id;
  const neighborhood = new Set([focusedNodeId]);
  for (const edge of visibleEdges) {
    if (edge.source === focusedNodeId) neighborhood.add(edge.target);
    if (edge.target === focusedNodeId) neighborhood.add(edge.source);
  }

  const nodeTypeGroups = useMemo(() => {
    const groups = new Map<string, string[]>();
    for (const node of visibleNodes) {
      const members = groups.get(node.nodeType) ?? [];
      members.push(node.label);
      groups.set(node.nodeType, members);
    }
    return groups;
  }, [visibleNodes]);

  const selectNodeFromKeyboard = (
    event: KeyboardEvent<SVGGElement>,
    nodeId: string,
  ) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onSelectNode(nodeId);
  };

  const startPan = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (event.button !== 0) return;
    const target = event.target as Element;
    if (target.closest('[role="button"]') !== null) return;
    markViewInteraction();
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
    markViewInteraction();
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };

  return (
    <div
      ref={workspaceRef}
      className="tap-graph-workspace"
      data-communities-open={communitiesOpen || normalizedQuery.length > 0}
      data-inspector-open={selectedNode !== null}
    >
      {communitiesOpen && normalizedQuery.length === 0 ? (
        <CommunityList
          communities={communities}
          selected={activeCommunities}
          onToggle={onToggleCommunity}
          onSelectAll={onSelectAllCommunities}
          footer={communitiesFooter}
          notice={communitiesNotice}
          copy={copy.library}
        />
      ) : null}

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
                    onClick={() => onSelectNode(node.id)}
                  >
                    <strong>{node.label}</strong>
                    <span>
                      {nodeTypeLabel(copy, node.nodeType)} ·{" "}
                      {node.communityLabel}
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
              onClick={() => {
                markViewInteraction();
                setZoom((current) => Math.max(MIN_ZOOM, current - 0.25));
              }}
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
              onClick={() => {
                markViewInteraction();
                setZoom((current) => Math.min(MAX_ZOOM, current + 0.25));
              }}
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
            <g transform={`translate(${pan.x} ${pan.y}) scale(${zoom})`}>
              <g className="tap-graph-edges" aria-hidden="true">
                {visibleEdges.map((edge) => {
                  const source = nodeById.get(edge.source);
                  const target = nodeById.get(edge.target);
                  if (!source || !target) return null;
                  const middleX = (source.x + target.x) / 2;
                  const middleY = (source.y + target.y) / 2;
                  const highlightedEdge =
                    highlight?.edgeIds.has(edge.id) ?? false;
                  const active =
                    focusedNodeId === edge.source ||
                    focusedNodeId === edge.target ||
                    highlightedEdge;
                  const showLabel = active || zoom >= EDGE_LABEL_ZOOM;
                  const muted = highlightActive
                    ? !highlightedEdge
                    : Boolean(focusedNodeId) && !active;
                  return (
                    <g
                      key={edge.id}
                      className="tap-graph-edge"
                      data-active={active}
                      data-muted={muted}
                      style={
                        { "--tap-edge-color": target.color } as CSSProperties
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
                    normalizedQuery.length > 0
                      ? matchesQuery(node)
                      : (highlight?.nodeIds.has(node.id) ?? false);
                  const dimmed =
                    normalizedQuery.length > 0 || highlightActive
                      ? !highlighted
                      : Boolean(focusedNodeId) && !neighborhood.has(node.id);
                  const selected = node.id === selectedNodeId;
                  const radius = 12 + Math.min(node.degree, 20) * 0.9;
                  return (
                    <g
                      key={node.id}
                      role="button"
                      tabIndex={0}
                      aria-label={`${node.label} · ${nodeTypeLabel(copy, node.nodeType)} · ${node.communityLabel}`}
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
                      onClick={() => onSelectNode(node.id)}
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
          aria-label={copy.library.graphSummary}
        >
          <h2>{copy.library.graphSummary}</h2>
          {[...nodeTypeGroups.entries()].map(([nodeType, labels]) => (
            <div key={nodeType}>
              <h3>{nodeTypeLabel(copy, nodeType)}</h3>
              <ul aria-label={nodeTypeLabel(copy, nodeType)}>
                {labels.map((label) => (
                  <li key={label}>{label}</li>
                ))}
              </ul>
            </div>
          ))}
          <h3>{copy.library.labeledRelationships}</h3>
          <ul aria-label={copy.library.labeledRelationships}>
            {visibleEdges.map((edge) => {
              const source = nodeById.get(edge.source);
              const target = nodeById.get(edge.target);
              if (!source || !target) return null;
              return (
                <li key={edge.id}>
                  {source.label} {edge.label} {target.label}
                </li>
              );
            })}
          </ul>
        </section>

        <figcaption id="tap-library-graph-caption">{caption}</figcaption>

        {onLoadMore ? (
          <button type="button" onClick={onLoadMore}>
            {loadMoreLabel ?? copy.library.loadMore}
          </button>
        ) : null}
      </figure>

      {detailPanel}
    </div>
  );
}
