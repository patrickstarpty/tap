// Pure, React-free label placement for the graph canvas. Node positions come
// from the layout (`layout.ts`) and are never moved here; only each node's
// label is placed below, above, right or left of its node.
//
// Everything is computed in screen space ("label space"): canvas positions
// times the current zoom, while labels keep a fixed size (the canvas draws
// them counter-scaled by `1 / zoom`). Zooming in therefore spreads nodes
// apart without growing their labels, and crowded labels separate.

export type LabelPlacement = "below" | "above" | "right" | "left";

export interface LabelCandidate {
  id: string;
  /** Node center, in canvas units. */
  x: number;
  y: number;
  /** Radius of the node's drawn circle, in canvas units. */
  radius: number;
  /** The text actually rendered (after any truncation). */
  text: string;
  degree: number;
}

/** An axis-aligned box in label space (canvas units times zoom). */
export interface LabelBox {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export interface PlacedLabel {
  placement: LabelPlacement;
  /** Text position relative to the node center, in label space. */
  dx: number;
  dy: number;
  anchor: "middle" | "start" | "end";
  /** Whether the label overlaps something and needs a backing halo. */
  halo: boolean;
  /** The label's box relative to the node center, in label space. */
  box: LabelBox;
}

export interface LabelMetrics {
  /** The label font size (must match the canvas CSS). */
  fontSize: number;
  zoom: number;
  /** Rendered text width; estimated from the text when omitted. */
  textWidth?: number;
}

export interface LabelPlacementOptions {
  /** Placed first: hovered, selected and cited-path nodes. */
  priority: ReadonlySet<string>;
  /** Placed next: search matches and the focused node's neighbors. */
  emphasized: ReadonlySet<string>;
  fontSize: number;
  zoom: number;
  /** Measures a label's rendered width (e.g. with the browser's text
   * metrics); falls back to `estimateTextWidth`. */
  measureText?: (text: string) => number;
  /** Boxes labels should stay clear of, e.g. visible edge labels. */
  obstacles?: readonly LabelBox[];
  /** The drawable area, in label space; labels should stay inside it. */
  bounds?: LabelBox;
}

// Gap between the node's edge and the label baseline below it (the line
// box then clears circles of the same size on the node's own row), the label
// baseline above it (room for descenders plus the selection ring drawn at
// `radius + 7`), and the label's near edge beside it.
const BELOW_BASELINE_OFFSET = 22;
const ABOVE_BASELINE_OFFSET = 14;
const SIDE_OFFSET = 10;
// Vertical extent of a sans-serif line box around the baseline, per em, on
// the generous side so a box always covers the rendered glyphs.
const ASCENT_EM = 0.95;
const DESCENT_EM = 0.2;
// Fallback advance widths per em when no measurement is available: CJK and
// other full-width characters take a full em; for Latin text, 0.6em is
// above the average advance of a semi-bold sans-serif.
const WIDE_CHAR_EM = 1;
const NARROW_CHAR_EM = 0.6;
const WIDE_CHAR = /[ᄀ-ᅟ⺀-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦]/u;
// The label's outline stroke (`stroke-width` in the canvas CSS) plus a
// small gap kept between neighboring labels.
const LABEL_STROKE = 5;
const LABEL_GAP = 2;
// Edge label pill size, shared with the canvas that draws it.
const EDGE_LABEL_CHAR_WIDTH = 8;
const EDGE_LABEL_PADDING = 20;
export const EDGE_LABEL_HEIGHT = 24;

const PLACEMENTS: readonly LabelPlacement[] = [
  "below",
  "above",
  "right",
  "left",
];

export function estimateTextWidth(text: string, fontSize: number): number {
  let ems = 0;
  for (const char of text) {
    ems += WIDE_CHAR.test(char) ? WIDE_CHAR_EM : NARROW_CHAR_EM;
  }
  return ems * fontSize;
}

export function edgeLabelWidth(label: string): number {
  return label.length * EDGE_LABEL_CHAR_WIDTH + EDGE_LABEL_PADDING;
}

/** The edge label pill drawn at an edge's midpoint (canvas units). */
export function edgeLabelBox(
  midX: number,
  midY: number,
  label: string,
  zoom: number,
): LabelBox {
  const halfWidth = edgeLabelWidth(label) / 2;
  return {
    left: midX * zoom - halfWidth,
    right: midX * zoom + halfWidth,
    top: midY * zoom - EDGE_LABEL_HEIGHT / 2,
    bottom: midY * zoom + EDGE_LABEL_HEIGHT / 2,
  };
}

function relativeLabel(
  radius: number,
  placement: LabelPlacement,
  fontSize: number,
  textWidth: number,
): Omit<PlacedLabel, "halo"> {
  const margin = LABEL_STROKE / 2 + LABEL_GAP / 2;
  // Baseline that vertically centers the line box on the node.
  const centeredBaseline = ((ASCENT_EM - DESCENT_EM) / 2) * fontSize;
  const position =
    placement === "below"
      ? { dx: 0, dy: radius + BELOW_BASELINE_OFFSET, anchor: "middle" as const }
      : placement === "above"
        ? {
            dx: 0,
            dy: -radius - ABOVE_BASELINE_OFFSET,
            anchor: "middle" as const,
          }
        : placement === "right"
          ? {
              dx: radius + SIDE_OFFSET,
              dy: centeredBaseline,
              anchor: "start" as const,
            }
          : {
              dx: -radius - SIDE_OFFSET,
              dy: centeredBaseline,
              anchor: "end" as const,
            };
  const left =
    position.anchor === "middle"
      ? position.dx - textWidth / 2
      : position.anchor === "start"
        ? position.dx
        : position.dx - textWidth;
  return {
    placement,
    ...position,
    box: {
      left: left - margin,
      right: left + textWidth + margin,
      top: position.dy - fontSize * ASCENT_EM - margin,
      bottom: position.dy + fontSize * DESCENT_EM + margin,
    },
  };
}

function translate(box: LabelBox, x: number, y: number): LabelBox {
  return {
    left: box.left + x,
    right: box.right + x,
    top: box.top + y,
    bottom: box.bottom + y,
  };
}

/** A node label's box in label space. */
export function labelBox(
  node: LabelCandidate,
  placement: LabelPlacement,
  metrics: LabelMetrics,
): LabelBox {
  const { fontSize, zoom } = metrics;
  const textWidth = metrics.textWidth ?? estimateTextWidth(node.text, fontSize);
  const relative = relativeLabel(
    node.radius * zoom,
    placement,
    fontSize,
    textWidth,
  );
  return translate(relative.box, node.x * zoom, node.y * zoom);
}

function overlapArea(a: LabelBox, b: LabelBox): number {
  const width = Math.min(a.right, b.right) - Math.max(a.left, b.left);
  const height = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
  return width > 0 && height > 0 ? width * height : 0;
}

/** Overlap with a node circle, approximated by the circle's bounding box
 * once the box actually reaches into the circle. */
function circleOverlapArea(
  circle: { x: number; y: number; r: number },
  box: LabelBox,
): number {
  const nearestX = Math.min(Math.max(circle.x, box.left), box.right);
  const nearestY = Math.min(Math.max(circle.y, box.top), box.bottom);
  const reaches =
    (nearestX - circle.x) ** 2 + (nearestY - circle.y) ** 2 < circle.r ** 2;
  if (!reaches) return 0;
  return overlapArea(box, {
    left: circle.x - circle.r,
    right: circle.x + circle.r,
    top: circle.y - circle.r,
    bottom: circle.y + circle.r,
  });
}

/** Area of `box` that falls outside `bounds`. */
function outsideArea(box: LabelBox, bounds: LabelBox | undefined): number {
  if (!bounds) return 0;
  return area(box) - overlapArea(box, bounds);
}

function area(box: LabelBox): number {
  return (box.right - box.left) * (box.bottom - box.top);
}

function tier(id: string, options: LabelPlacementOptions): number {
  if (options.priority.has(id)) return 0;
  if (options.emphasized.has(id)) return 1;
  return 2;
}

export interface EdgeLabelCandidate {
  id: string;
  /** Edge endpoints, in canvas units. */
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  text: string;
}

export interface GraphLabelPlacement {
  nodes: Map<string, PlacedLabel>;
  /** Where each edge label is drawn along its edge, in canvas units. */
  edges: Map<string, { x: number; y: number }>;
}

// Spots an edge label may sit at, in order of preference: the midpoint,
// then further along the edge (`t` is the fraction from source to target),
// then beside the midpoint (`side` is the distance off the edge, in label
// space) for edges too short to hold the label between their nodes.
const EDGE_LABEL_SPOTS: readonly { t: number; side: number }[] = [
  { t: 0.5, side: 0 },
  { t: 0.4, side: 0 },
  { t: 0.6, side: 0 },
  { t: 0.3, side: 0 },
  { t: 0.7, side: 0 },
  ...[20, 32, 44].flatMap((side) => [
    { t: 0.5, side },
    { t: 0.5, side: -side },
  ]),
];

interface Circle {
  id: string;
  x: number;
  y: number;
  r: number;
}

function placementCosts(
  candidate: LabelCandidate,
  placement: LabelPlacement,
  textWidth: number,
  options: LabelPlacementOptions,
  context: {
    placedBoxes: readonly LabelBox[];
    circles: readonly Circle[];
    obstacles: readonly LabelBox[];
  },
): {
  label: Omit<PlacedLabel, "halo">;
  box: LabelBox;
  labels: number;
  obstacles: number;
} {
  const { fontSize, zoom } = options;
  const label = relativeLabel(
    candidate.radius * zoom,
    placement,
    fontSize,
    textWidth,
  );
  const box = translate(label.box, candidate.x * zoom, candidate.y * zoom);
  let labels = 0;
  for (const other of context.placedBoxes) labels += overlapArea(box, other);
  for (const circle of context.circles) {
    if (circle.id !== candidate.id) labels += circleOverlapArea(circle, box);
  }
  let obstacles = outsideArea(box, options.bounds);
  for (const obstacle of context.obstacles) {
    obstacles += overlapArea(box, obstacle);
  }
  return { label, box, labels, obstacles };
}

/**
 * Places every node's label, and every edge label along its edge.
 *
 * Node labels go in priority order — priority, then emphasized, then by
 * descending degree, ties kept in input order. Each takes the first of
 * below/above/right/left that overlaps no label placed before it, no other
 * node's circle and no edge label (or other obstacle); when all four
 * overlap something, it takes the one with the least overlap area and is
 * flagged for a halo. No label is ever left out.
 *
 * Priority labels (hovered, selected, cited) come first and may claim a
 * spot that is only blocked by an edge label at its midpoint: that edge
 * label then slides along (or beside) its edge out of the way, and every
 * later node label avoids the edge labels where they finally sit.
 */
export function placeGraphLabels(
  candidates: readonly LabelCandidate[],
  edgeLabels: readonly EdgeLabelCandidate[],
  options: LabelPlacementOptions,
): GraphLabelPlacement {
  const { fontSize, zoom, measureText, obstacles = [] } = options;
  const circles: Circle[] = candidates.map((candidate) => ({
    id: candidate.id,
    x: candidate.x * zoom,
    y: candidate.y * zoom,
    r: candidate.radius * zoom,
  }));
  const ordered = candidates
    .map((candidate, index) => ({ candidate, index }))
    .sort(
      (a, b) =>
        tier(a.candidate.id, options) - tier(b.candidate.id, options) ||
        b.candidate.degree - a.candidate.degree ||
        a.index - b.index,
    )
    .map(({ candidate }) => candidate);
  const isLeading = (id: string) => options.priority.has(id);
  const widthOf = (candidate: LabelCandidate) =>
    measureText?.(candidate.text) ??
    estimateTextWidth(candidate.text, fontSize);
  const edgeAt = (
    edge: EdgeLabelCandidate,
    spot: { t: number; side: number },
  ) => {
    const dx = edge.x2 - edge.x1;
    const dy = edge.y2 - edge.y1;
    const length = Math.hypot(dx, dy) || 1;
    // Unit normal to the edge, scaled from label space back to canvas.
    const off = spot.side / zoom / length;
    return {
      x: edge.x1 + dx * spot.t - dy * off,
      y: edge.y1 + dy * spot.t + dx * off,
    };
  };

  const placedBoxes: LabelBox[] = [];
  const nodes = new Map<string, PlacedLabel>();
  const leadingBoxes = new Map<string, LabelBox>();

  // 1. Leading (priority) labels. An edge label in the way only counts for
  // as much as it would still overlap at its best spot.
  const edgeLabelSpots = edgeLabels.map((edge) =>
    EDGE_LABEL_SPOTS.map((spot) => {
      const at = edgeAt(edge, spot);
      return edgeLabelBox(at.x, at.y, edge.text, zoom);
    }),
  );
  const leading = ordered.filter((candidate) => isLeading(candidate.id));
  for (const candidate of leading) {
    const textWidth = widthOf(candidate);
    let best: {
      cost: ReturnType<typeof placementCosts>;
      total: number;
    } | null = null;
    for (const placement of PLACEMENTS) {
      const cost = placementCosts(candidate, placement, textWidth, options, {
        placedBoxes,
        circles,
        obstacles,
      });
      let unavoidable = 0;
      for (const spots of edgeLabelSpots) {
        unavoidable += Math.min(
          ...spots.map((spot) => overlapArea(cost.box, spot)),
        );
      }
      const total = cost.labels + cost.obstacles + unavoidable;
      if (best === null || total < best.total) best = { cost, total };
      if (total === 0) break;
    }
    if (best === null) continue;
    const chosen = best.cost;
    nodes.set(candidate.id, {
      ...chosen.label,
      halo: chosen.labels + chosen.obstacles > 0,
    });
    placedBoxes.push(chosen.box);
    leadingBoxes.set(candidate.id, chosen.box);
  }

  // 2. Edge labels slide along (or beside) their edges to clear the
  // leading labels, then node circles, then each other, where they can.
  const edges = new Map<string, { x: number; y: number }>();
  const edgeBoxes: LabelBox[] = [];
  for (const edge of edgeLabels) {
    const spots = EDGE_LABEL_SPOTS.map((spot) => {
      const at = edgeAt(edge, spot);
      const box = edgeLabelBox(at.x, at.y, edge.text, zoom);
      let labels = 0;
      for (const other of leadingBoxes.values()) {
        labels += overlapArea(box, other);
      }
      let nodesHit = 0;
      for (const circle of circles) nodesHit += circleOverlapArea(circle, box);
      let edgeLabelsHit = 0;
      for (const other of edgeBoxes) edgeLabelsHit += overlapArea(box, other);
      return { at, box, labels, nodesHit, edgeLabelsHit, side: spot.side };
    });
    const chosen =
      // Moving off the edge is only worth it to clear labels or nodes, not
      // another edge label (it would crowd the nodes' own label spots).
      spots.find(
        (option) =>
          option.side === 0 &&
          option.labels + option.nodesHit + option.edgeLabelsHit === 0,
      ) ??
      spots.find((option) => option.labels + option.nodesHit === 0) ??
      spots.find((option) => option.labels === 0) ??
      spots.reduce((best, option) =>
        option.labels < best.labels ? option : best,
      );
    edges.set(edge.id, chosen.at);
    edgeBoxes.push(chosen.box);
    if (chosen.labels > 0) {
      // Still in a leading label's way: back that label with a halo.
      for (const [id, box] of leadingBoxes) {
        if (overlapArea(chosen.box, box) > 0) {
          nodes.set(id, { ...nodes.get(id)!, halo: true });
        }
      }
    }
  }

  // 3. Every other label, against the edge labels where they now sit.
  const finalObstacles = [...obstacles, ...edgeBoxes];
  for (const candidate of ordered) {
    if (isLeading(candidate.id)) continue;
    const textWidth = widthOf(candidate);
    let best: ReturnType<typeof placementCosts> | null = null;
    for (const placement of PLACEMENTS) {
      const cost = placementCosts(candidate, placement, textWidth, options, {
        placedBoxes,
        circles,
        obstacles: finalObstacles,
      });
      if (
        best === null ||
        cost.labels + cost.obstacles < best.labels + best.obstacles
      ) {
        best = cost;
      }
      if (cost.labels + cost.obstacles === 0) break;
    }
    if (best === null) continue;
    nodes.set(candidate.id, {
      ...best.label,
      halo: best.labels + best.obstacles > 0,
    });
    placedBoxes.push(best.box);
  }
  return { nodes, edges };
}

/** `placeGraphLabels` for node labels alone (no edge labels). */
export function placeNodeLabels(
  candidates: readonly LabelCandidate[],
  options: LabelPlacementOptions,
): Map<string, PlacedLabel> {
  return placeGraphLabels(candidates, [], options).nodes;
}
