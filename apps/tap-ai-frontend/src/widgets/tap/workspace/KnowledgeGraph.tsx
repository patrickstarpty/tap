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
import {
  EDGE_LABEL_HEIGHT,
  edgeLabelWidth,
  placeGraphLabels,
  type LabelBox,
} from "../../../features/graph/model/labelPlacement";
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
// Must match the `.tap-graph-node-label` font size and weight in
// `TapperWorkspace.css`: label collision avoidance sizes each label's box
// from them. The CSS draws document-kind node labels smaller (14px); 18px
// is the conservative upper bound, so their boxes are only ever too large.
const NODE_LABEL_FONT_SIZE = 18;
const NODE_LABEL_FONT_WEIGHT = 600;
// The whole drawable canvas, in canvas units.
const WHOLE_CANVAS: LabelBox = {
  left: -GRAPH_HORIZONTAL_MARGIN,
  top: 0,
  right: GRAPH_WIDTH + GRAPH_HORIZONTAL_MARGIN,
  bottom: GRAPH_HEIGHT,
};
// How long the view must stay still after a pan, zoom or resize before
// labels are re-placed against the newly visible area.
const VIEW_SETTLE_MS = 150;
// Screen pixels kept clear around the floating toolbar.
const TOOLBAR_LABEL_CLEARANCE = 6;

interface VisibleArea {
  /** The visible part of the canvas, in canvas units. */
  bounds: LabelBox;
  /** Visible areas covered by floating controls, in canvas units. */
  blocked: LabelBox[];
}

/**
 * Measures, in canvas units (before pan and zoom), what part of the canvas
 * is visible on screen and which of it the floating toolbar covers.
 * `null` until the canvas has a size (e.g. in a test DOM without layout).
 */
function measureVisibleArea(
  canvas: HTMLElement,
  svg: SVGSVGElement,
  toolbar: HTMLElement | null,
  zoom: number,
  pan: { x: number; y: number },
): VisibleArea | null {
  const svgRect = svg.getBoundingClientRect();
  const canvasRect = canvas.getBoundingClientRect();
  if (svgRect.width <= 0 || svgRect.height <= 0) return null;
  if (canvasRect.width <= 0 || canvasRect.height <= 0) return null;
  // The SVG scales its view box to fit ("meet") and centers it.
  const viewWidth = GRAPH_WIDTH + GRAPH_HORIZONTAL_MARGIN * 2;
  const scale = Math.min(
    svgRect.width / viewWidth,
    svgRect.height / GRAPH_HEIGHT,
  );
  const originX = svgRect.left + (svgRect.width - viewWidth * scale) / 2;
  const originY = svgRect.top + (svgRect.height - GRAPH_HEIGHT * scale) / 2;
  const toCanvas = (rect: LabelBox): LabelBox => ({
    left:
      ((rect.left - originX) / scale - GRAPH_HORIZONTAL_MARGIN - pan.x) / zoom,
    right:
      ((rect.right - originX) / scale - GRAPH_HORIZONTAL_MARGIN - pan.x) / zoom,
    top: ((rect.top - originY) / scale - pan.y) / zoom,
    bottom: ((rect.bottom - originY) / scale - pan.y) / zoom,
  });
  const toolbarRect = toolbar?.getBoundingClientRect();
  return {
    // The canvas clips whatever the SVG draws outside it.
    bounds: toCanvas(canvasRect),
    blocked:
      toolbarRect && toolbarRect.width > 0 && toolbarRect.height > 0
        ? [
            toCanvas({
              left: toolbarRect.left - TOOLBAR_LABEL_CLEARANCE,
              right: toolbarRect.right + TOOLBAR_LABEL_CLEARANCE,
              top: toolbarRect.top - TOOLBAR_LABEL_CLEARANCE,
              bottom: toolbarRect.bottom + TOOLBAR_LABEL_CLEARANCE,
            }),
          ]
        : [],
  };
}

function visibleAreaKey(area: VisibleArea | null): string {
  return area
    ? [area.bounds, ...area.blocked].map((box) => boxKey(box)).join("|")
    : "";
}

function scaleBox(box: LabelBox, factor: number): LabelBox {
  return {
    left: box.left * factor,
    right: box.right * factor,
    top: box.top * factor,
    bottom: box.bottom * factor,
  };
}

function boxKey(box: LabelBox): string {
  return [box.left, box.top, box.right, box.bottom]
    .map((value) => value.toFixed(1))
    .join(",");
}

/**
 * Measures node label widths with the browser's own text metrics for the
 * canvas font, so collision avoidance neither hides labels that would fit
 * nor lets wide (e.g. CJK) labels overlap. `undefined` where no 2D text
 * measurement is available (label placement then falls back to an
 * estimate).
 */
function createLabelTextMeasure(
  fontFamily: string,
): ((text: string) => number) | undefined {
  if (typeof OffscreenCanvas === "undefined") return undefined;
  const context = new OffscreenCanvas(1, 1).getContext("2d");
  if (!context) return undefined;
  context.font = `${String(NODE_LABEL_FONT_WEIGHT)} ${String(NODE_LABEL_FONT_SIZE)}px ${fontFamily}`;
  // An unparseable font string is ignored and leaves the 10px default.
  if (!context.font.includes(`${String(NODE_LABEL_FONT_SIZE)}px`)) {
    return undefined;
  }
  const widths = new Map<string, number>();
  return (text) => {
    let width = widths.get(text);
    if (width === undefined) {
      width = context.measureText(text).width;
      widths.set(text, width);
    }
    return width;
  };
}

function displayLabel(label: string): string {
  return label.length > 27 ? `${label.slice(0, 25)}…` : label;
}

function nodeRadius(node: GraphNode): number {
  return 12 + Math.min(node.degree, 20) * 0.9;
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
  const canvasRef = useRef<HTMLDivElement>(null);
  const toolbarRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  // The labels inherit the canvas font family, only known once rendered.
  const [labelFontFamily, setLabelFontFamily] = useState<string | null>(null);
  useEffect(() => {
    const svg = svgRef.current;
    if (svg) setLabelFontFamily(window.getComputedStyle(svg).fontFamily);
  }, []);
  const measureLabelText = useMemo(
    () =>
      labelFontFamily ? createLabelTextMeasure(labelFontFamily) : undefined,
    [labelFontFamily],
  );
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

  // The visible part of the canvas, re-measured once the view settles after
  // a pan, zoom or resize (not on every frame of a drag), so labels at the
  // viewport's edges and under the toolbar can be moved into view.
  const [visibleArea, setVisibleArea] = useState<VisibleArea | null>(null);
  const [canvasResizes, setCanvasResizes] = useState(0);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() =>
      setCanvasResizes((count) => count + 1),
    );
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const canvas = canvasRef.current;
      const svg = svgRef.current;
      if (!canvas || !svg) return;
      const measured = measureVisibleArea(
        canvas,
        svg,
        toolbarRef.current,
        zoom,
        pan,
      );
      setVisibleArea((current) =>
        visibleAreaKey(current) === visibleAreaKey(measured)
          ? current
          : measured,
      );
    }, VIEW_SETTLE_MS);
    return () => window.clearTimeout(timer);
    // `pan` is tracked through its coordinates.
  }, [zoom, pan.x, pan.y, canvasResizes]);

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
  const isHighlighted = (node: GraphNode) =>
    normalizedQuery.length > 0
      ? matchesQuery(node)
      : (highlight?.nodeIds.has(node.id) ?? false);
  const isEdgeActive = (edge: GraphEdge) =>
    focusedNodeId === edge.source ||
    focusedNodeId === edge.target ||
    (highlight?.edgeIds.has(edge.id) ?? false);
  const isEdgeLabelShown = (edge: GraphEdge) =>
    isEdgeActive(edge) || zoom >= EDGE_LABEL_ZOOM;
  const isEdgeMuted = (edge: GraphEdge) =>
    highlightActive
      ? !(highlight?.edgeIds.has(edge.id) ?? false)
      : Boolean(focusedNodeId) && !isEdgeActive(edge);

  // Every node keeps a label, placed below, above, right or left of it —
  // whichever is first clear of other labels, other nodes and the visible
  // edge labels — or, when none is, where it overlaps least, over a halo.
  // Node positions are left untouched. Hovered, selected and cited-path
  // nodes are placed first, then search matches and the focused node's
  // neighbors (edge labels slide along or beside their edges out of these
  // labels' way), then the rest by degree. Edge labels never overlap each
  // other: of two that collide wherever they slide, the lower-priority one
  // (see `edgeLabelTier`) is hidden at this zoom. Placement runs in screen
  // space and is redone on zoom: labels (node and edge) grow only with the
  // square root of the zoom while distances grow with the zoom itself, so
  // zooming in still enlarges text but separates crowded labels. Labels
  // stay inside the visible part of the canvas and out from under the
  // toolbar, re-checked once a pan or zoom settles. Keyed on content (these
  // id sets are rebuilt every render), so a pan in progress does not redo it.
  const labelZoom = Math.sqrt(zoom);
  const labelPriority = new Set(
    [hoveredNodeId, selectedNodeId, ...(highlight?.nodeIds ?? [])].filter(
      (id): id is string => id != null,
    ),
  );
  const labelEmphasis = new Set(
    visibleNodes
      .filter(
        (node) =>
          (normalizedQuery.length > 0 && matchesQuery(node)) ||
          neighborhood.has(node.id),
      )
      .map((node) => node.id),
  );
  const shownEdgeLabels = visibleEdges.filter(isEdgeLabelShown);
  // Where two edge labels cannot both be shown, cited edges keep theirs
  // first, then edges at the hovered or selected node, then the rest.
  const edgeLabelTier = (edge: GraphEdge) =>
    (highlight?.edgeIds.has(edge.id) ?? false)
      ? 0
      : [hoveredNodeId, selectedNodeId].some(
            (id) => id != null && (edge.source === id || edge.target === id),
          )
        ? 1
        : 2;
  const labelInputsKey = [
    [...labelPriority].join("|"),
    [...labelEmphasis].join("|"),
    shownEdgeLabels
      .map((edge) => `${edge.id}:${String(edgeLabelTier(edge))}`)
      .join("|"),
  ].join("#");
  const labelPlacements = useMemo(
    () =>
      placeGraphLabels(
        visibleNodes.map((node) => ({
          id: node.id,
          x: node.x,
          y: node.y,
          radius: nodeRadius(node),
          text: displayLabel(node.label),
          degree: node.degree,
        })),
        shownEdgeLabels.flatMap((edge) => {
          const source = nodeById.get(edge.source);
          const target = nodeById.get(edge.target);
          if (!source || !target) return [];
          return [
            {
              id: edge.id,
              x1: source.x,
              y1: source.y,
              x2: target.x,
              y2: target.y,
              text: edge.label,
              tier: edgeLabelTier(edge),
            },
          ];
        }),
        {
          priority: labelPriority,
          emphasized: labelEmphasis,
          fontSize: NODE_LABEL_FONT_SIZE,
          zoom: labelZoom,
          measureText: measureLabelText,
          // Until the visible area is measured, the whole canvas.
          bounds: scaleBox(visibleArea?.bounds ?? WHOLE_CANVAS, labelZoom),
          canvasBounds: scaleBox(WHOLE_CANVAS, labelZoom),
          blocked: (visibleArea?.blocked ?? []).map((box) =>
            scaleBox(box, labelZoom),
          ),
        },
      ),
    // The id sets and shown edge labels are tracked through
    // `labelInputsKey`; positions through `visibleNodes`/`nodeById`.
    [
      visibleNodes,
      nodeById,
      labelZoom,
      labelInputsKey,
      measureLabelText,
      visibleArea,
    ],
  );

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
        <div ref={toolbarRef} className="tap-graph-toolbar">
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
        <div
          ref={canvasRef}
          className="tap-graph-canvas"
          data-dragging={dragging}
        >
          <svg
            ref={svgRef}
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
                  return (
                    <g
                      key={edge.id}
                      className="tap-graph-edge"
                      data-active={isEdgeActive(edge)}
                      data-muted={isEdgeMuted(edge)}
                      style={
                        { "--tap-edge-color": target.color } as CSSProperties
                      }
                      data-provenance={edge.provenance}
                    >
                      <path
                        d={`M ${source.x} ${source.y} L ${target.x} ${target.y}`}
                      />
                    </g>
                  );
                })}
              </g>

              {/* Edge labels sit in their own layer above every edge line,
                  so no other edge is drawn across a label. */}
              <g className="tap-graph-edge-labels" aria-hidden="true">
                {shownEdgeLabels.map((edge) => {
                  const labelAt = labelPlacements.edges.get(edge.id);
                  if (!labelAt) return null;
                  return (
                    <g
                      key={edge.id}
                      className="tap-graph-edge-label"
                      data-muted={isEdgeMuted(edge)}
                      transform={`translate(${labelAt.x} ${labelAt.y}) scale(${1 / labelZoom})`}
                    >
                      <rect
                        x={-edgeLabelWidth(edge.label) / 2}
                        y={-EDGE_LABEL_HEIGHT / 2}
                        width={edgeLabelWidth(edge.label)}
                        height={EDGE_LABEL_HEIGHT}
                        rx={EDGE_LABEL_HEIGHT / 2}
                      />
                      <text textAnchor="middle" dominantBaseline="middle">
                        {edge.label}
                      </text>
                    </g>
                  );
                })}
              </g>

              <g className="tap-graph-nodes">
                {visibleNodes.map((node) => {
                  const highlighted = isHighlighted(node);
                  const dimmed =
                    normalizedQuery.length > 0 || highlightActive
                      ? !highlighted
                      : Boolean(focusedNodeId) && !neighborhood.has(node.id);
                  const selected = node.id === selectedNodeId;
                  const radius = nodeRadius(node);
                  const label = labelPlacements.nodes.get(node.id);
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
                      {label ? (
                        <g
                          className="tap-graph-node-label-group"
                          data-label-placement={label.placement}
                          data-halo={label.halo}
                          transform={`translate(${node.x} ${node.y}) scale(${1 / labelZoom})`}
                        >
                          {label.halo ? (
                            <rect
                              className="tap-graph-node-label-halo"
                              x={label.box.left}
                              y={label.box.top}
                              width={label.box.right - label.box.left}
                              height={label.box.bottom - label.box.top}
                              rx="4"
                            />
                          ) : null}
                          <text
                            className="tap-graph-node-label"
                            x={label.dx}
                            y={label.dy}
                            textAnchor={label.anchor}
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
