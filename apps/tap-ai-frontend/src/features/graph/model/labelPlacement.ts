// Pure, React-free label placement for the graph canvas. Node positions come
// from the layout (`layout.ts`) and are never moved here; only each node's
// label is placed below, above, right or left of its node.
//
// Everything is computed in "label space": canvas positions times the
// `zoom` option, with labels at a fixed size in that space (the canvas draws
// them counter-scaled by `1 / zoom`). The canvas passes the square root of
// its zoom, so on screen nodes spread apart with the zoom while labels grow
// only with its square root: crowded labels separate as the view zooms in,
// yet text still grows. Labels are not kept at a strictly fixed on-screen
// size because the SVG's `meet` scaling already shrinks the whole canvas
// when the side panels are open; at 175% such labels would be about 7 px.

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
  /** The visible area, in label space; labels should stay inside it. */
  bounds?: LabelBox;
  /** Areas inside `bounds` that labels must not use either, in label
   * space: e.g. the space under a floating toolbar. */
  blocked?: readonly LabelBox[];
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

/** Area of `box` that falls outside `bounds` or inside a `blocked` area. */
function outsideArea(
  box: LabelBox,
  bounds: LabelBox | undefined,
  blocked: readonly LabelBox[],
): number {
  let outside = bounds ? area(box) - overlapArea(box, bounds) : 0;
  for (const other of blocked) outside += overlapArea(box, other);
  return outside;
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
  /**
   * Which edge label keeps its place when two cannot both be shown: lower
   * tiers are placed first (e.g. 0 for cited edges, 1 for edges at the
   * hovered or selected node, 2 for the rest); ties keep input order.
   */
  tier?: number;
}

export interface GraphLabelPlacement {
  nodes: Map<string, PlacedLabel>;
  /**
   * Where each shown edge label is drawn along its edge, in canvas units.
   * An edge label with no spot clear of the edge labels placed before it is
   * left out (hidden at this zoom).
   */
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

// Side of a spatial-index cell, in label space: about one label's height
// times a few, so a typical box spans only a handful of cells.
const GRID_CELL = 96;
const GRID_OFFSET = 1 << 15;

/**
 * A uniform-grid spatial index over boxes, so overlap sums only visit
 * nearby items instead of every placed label, node and edge label. Sums
 * are exact: an item outside every cell `box` touches cannot overlap it.
 */
class BoxGrid<T> {
  private readonly cells = new Map<number, number[]>();
  private readonly boxes: LabelBox[] = [];
  private readonly values: T[] = [];
  private readonly seen: number[] = [];
  private stamp = 0;

  add(box: LabelBox, value: T): void {
    const index = this.boxes.length;
    this.boxes.push(box);
    this.values.push(value);
    this.seen.push(0);
    const [left, right, top, bottom] = cellRange(box);
    for (let column = left; column <= right; column += 1) {
      for (let row = top; row <= bottom; row += 1) {
        const key = cellKey(column, row);
        const cell = this.cells.get(key);
        if (cell) cell.push(index);
        else this.cells.set(key, [index]);
      }
    }
  }

  /**
   * Sums `cost` (by default, the overlap area with `box`) over the items
   * near `box`, each item once. With `stopAtFirst`, returns as soon as one
   * item costs anything — for callers that only need zero or non-zero.
   */
  sum(
    box: LabelBox,
    cost?: (other: LabelBox, value: T) => number,
    stopAtFirst = false,
  ): number {
    this.stamp += 1;
    const stamp = this.stamp;
    const left = Math.floor(box.left / GRID_CELL);
    const right = Math.floor(box.right / GRID_CELL);
    const top = Math.floor(box.top / GRID_CELL);
    const bottom = Math.floor(box.bottom / GRID_CELL);
    let total = 0;
    for (let column = left; column <= right; column += 1) {
      for (let row = top; row <= bottom; row += 1) {
        const cell = this.cells.get(cellKey(column, row));
        if (!cell) continue;
        for (const index of cell) {
          if (this.seen[index] === stamp) continue;
          this.seen[index] = stamp;
          const other = this.boxes[index]!;
          total += cost
            ? cost(other, this.values[index]!)
            : overlapArea(box, other);
          if (stopAtFirst && total > 0) return total;
        }
      }
    }
    return total;
  }

  /** Whether any item near `box` has a positive `cost` (stops early). */
  any(box: LabelBox, cost?: (other: LabelBox, value: T) => number): boolean {
    return this.sum(box, cost, true) > 0;
  }
}

function cellRange(box: LabelBox): [number, number, number, number] {
  return [
    Math.floor(box.left / GRID_CELL),
    Math.floor(box.right / GRID_CELL),
    Math.floor(box.top / GRID_CELL),
    Math.floor(box.bottom / GRID_CELL),
  ];
}

function cellKey(column: number, row: number): number {
  return (column + GRID_OFFSET) * 2 * GRID_OFFSET + (row + GRID_OFFSET);
}

function circleBox(circle: Circle): LabelBox {
  return {
    left: circle.x - circle.r,
    right: circle.x + circle.r,
    top: circle.y - circle.r,
    bottom: circle.y + circle.r,
  };
}

interface PlacementCost {
  label: Omit<PlacedLabel, "halo">;
  box: LabelBox;
  /** Area outside the bounds or inside a blocked area (cut off or hidden;
   * a halo does not help). */
  outside: number;
  /** Overlap with node labels and other nodes' circles. */
  labels: number;
  /** Overlap with obstacles (edge labels). */
  obstacles: number;
}

// Out-of-view areas (label space, px²) closer than this count as equal:
// the four spots of a label far out of view differ only by rounding.
const OUTSIDE_TOLERANCE = 0.5;

/** Whether a spot costing `outside`/`overlap` beats `best`: staying in
 * view comes first, then overlapping less. */
function isBetter(
  outside: number,
  overlap: number,
  best: { outside: number; overlap: number } | undefined,
): boolean {
  if (best === undefined) return true;
  if (Math.abs(outside - best.outside) > OUTSIDE_TOLERANCE) {
    return outside < best.outside;
  }
  return overlap < best.overlap;
}

/**
 * Places every node's label, and every edge label along its edge.
 *
 * Node labels go in priority order — priority, then emphasized, then by
 * descending degree, ties kept in input order. Each takes the first of
 * below/above/right/left that stays in view (inside `bounds`, outside every
 * `blocked` area) and overlaps no label placed before it, no other node's
 * circle and no edge label (or other obstacle). A spot that leaves the view
 * counts as unusable: when none of the four is clear, the label takes, of
 * those in view, the one with the least overlap area and is flagged for a
 * halo; when all four leave the view, the one least out of view. No label
 * is ever left out.
 *
 * Priority labels (hovered, selected, cited) come first and may claim a
 * spot that is only blocked by an edge label at its midpoint: that edge
 * label then slides along (or beside) its edge out of the way, and every
 * later node label avoids the edge labels where they finally sit.
 *
 * Edge labels go in tier order and never overlap each other: one that
 * collides with an earlier edge label at every spot along its edge (or
 * whose only spots clear of those cover a leading label) is left out, so
 * at a crowded hub the higher-tier labels stay. Only edge labels are ever
 * left out; zooming in spreads the spots apart and brings them back.
 *
 * Overlap checks go through uniform-grid spatial indexes, so a full
 * placement stays near-linear in the number of labels.
 */
export function placeGraphLabels(
  candidates: readonly LabelCandidate[],
  edgeLabels: readonly EdgeLabelCandidate[],
  options: LabelPlacementOptions,
): GraphLabelPlacement {
  const { fontSize, zoom, measureText, obstacles = [], blocked = [] } = options;
  const circleGrid = new BoxGrid<Circle>();
  for (const candidate of candidates) {
    const circle = {
      id: candidate.id,
      x: candidate.x * zoom,
      y: candidate.y * zoom,
      r: candidate.radius * zoom,
    };
    circleGrid.add(circleBox(circle), circle);
  }
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

  // Node labels placed so far, and the boxes node labels must keep clear of.
  const labelGrid = new BoxGrid<null>();
  const obstacleGrid = new BoxGrid<null>();
  for (const obstacle of obstacles) obstacleGrid.add(obstacle, null);
  const nodes = new Map<string, PlacedLabel>();
  const leadingBoxes = new Map<string, LabelBox>();

  const costOf = (
    candidate: LabelCandidate,
    placement: LabelPlacement,
    textWidth: number,
  ): PlacementCost => {
    const label = relativeLabel(
      candidate.radius * zoom,
      placement,
      fontSize,
      textWidth,
    );
    const box = translate(label.box, candidate.x * zoom, candidate.y * zoom);
    const labels =
      labelGrid.sum(box) +
      circleGrid.sum(box, (_, circle) =>
        circle.id === candidate.id ? 0 : circleOverlapArea(circle, box),
      );
    return {
      label,
      box,
      outside: outsideArea(box, options.bounds, blocked),
      labels,
      obstacles: obstacleGrid.sum(box),
    };
  };

  // 1. Leading (priority) labels. An edge label in the way only counts for
  // as much as it would still overlap at its best spot. That is non-zero
  // only if every spot overlaps, so only edges whose midpoint spot does are
  // looked at (found through a grid of midpoint spots, built only when
  // there are leading labels at all).
  const leadingCandidates = ordered.filter((candidate) =>
    isLeading(candidate.id),
  );
  const midpointGrid = new BoxGrid<EdgeLabelCandidate>();
  if (leadingCandidates.length > 0) {
    for (const edge of edgeLabels) {
      const at = edgeAt(edge, EDGE_LABEL_SPOTS[0]!);
      midpointGrid.add(edgeLabelBox(at.x, at.y, edge.text, zoom), edge);
    }
  }
  const unavoidableEdgeOverlap = (box: LabelBox) =>
    midpointGrid.sum(box, (midpoint, edge) => {
      let least = overlapArea(box, midpoint);
      for (const spot of EDGE_LABEL_SPOTS) {
        if (least === 0) break;
        const at = edgeAt(edge, spot);
        least = Math.min(
          least,
          overlapArea(box, edgeLabelBox(at.x, at.y, edge.text, zoom)),
        );
      }
      return least;
    });
  for (const candidate of leadingCandidates) {
    const textWidth = widthOf(candidate);
    let best:
      { cost: PlacementCost; outside: number; overlap: number } | undefined;
    for (const placement of PLACEMENTS) {
      const cost = costOf(candidate, placement, textWidth);
      const overlap =
        cost.labels + cost.obstacles + unavoidableEdgeOverlap(cost.box);
      if (isBetter(cost.outside, overlap, best)) {
        best = { cost, outside: cost.outside, overlap };
      }
      if (cost.outside <= OUTSIDE_TOLERANCE && overlap === 0) break;
    }
    const chosen = best!.cost;
    nodes.set(candidate.id, {
      ...chosen.label,
      halo: chosen.labels + chosen.obstacles > 0,
    });
    labelGrid.add(chosen.box, null);
    leadingBoxes.set(candidate.id, chosen.box);
  }

  // 2. Edge labels, in tier order, slide along (or beside) their edges to
  // clear each other, the leading labels and then node circles.
  const edges = new Map<string, { x: number; y: number }>();
  const edgeGrid = new BoxGrid<null>();
  const edgesByTier = edgeLabels
    .map((edge, index) => ({ edge, index }))
    .sort(
      (a, b) => (a.edge.tier ?? 0) - (b.edge.tier ?? 0) || a.index - b.index,
    )
    .map(({ edge }) => edge);
  for (const edge of edgesByTier) {
    // Only spots clear of the edge labels already placed are eligible.
    // Among them, the preference order is: the first clear of everything;
    // else the first clear of leading labels; else, only if no spot at all
    // is clear of them (as the leading labels were placed expecting), the
    // least overlap with them. Otherwise the label is left out: it is only
    // blocked by other edge labels and must not cover a leading label
    // instead. Only zero or non-zero matters for nodes and other edge
    // labels, so those checks stop at the first overlap.
    let chosen: { at: { x: number; y: number }; box: LabelBox } | undefined;
    let clearOfLabels: typeof chosen;
    let leastOverlap: (typeof chosen & { labels: number }) | undefined;
    // Whether some spot on the edge itself is clear of leading labels and
    // nodes, edge labels aside: moving off the edge is only worth it to
    // clear those, not another edge label (it would crowd the nodes' own
    // label spots, so such a label is left out instead).
    let edgeHasRoom = false;
    let someSpotClearOfLabels = false;
    for (const spot of EDGE_LABEL_SPOTS) {
      // Off-edge spots come after every along-edge one.
      if (spot.side !== 0 && edgeHasRoom) break;
      const at = edgeAt(edge, spot);
      const box = edgeLabelBox(at.x, at.y, edge.text, zoom);
      let labels = 0;
      for (const other of leadingBoxes.values()) {
        labels += overlapArea(box, other);
      }
      const hitsNode =
        labels === 0 &&
        circleGrid.any(box, (_, circle) => circleOverlapArea(circle, box));
      if (labels === 0) someSpotClearOfLabels = true;
      if (spot.side === 0 && labels === 0 && !hitsNode) edgeHasRoom = true;
      if (edgeGrid.any(box)) continue;
      if (!leastOverlap || labels < leastOverlap.labels) {
        leastOverlap = { at, box, labels };
      }
      if (labels > 0) continue;
      clearOfLabels ??= { at, box };
      if (hitsNode) continue;
      chosen = { at, box };
      break;
    }
    chosen ??=
      clearOfLabels ?? (someSpotClearOfLabels ? undefined : leastOverlap);
    if (!chosen) continue;
    const chosenLabels = chosen === leastOverlap ? leastOverlap.labels : 0;
    edges.set(edge.id, chosen.at);
    edgeGrid.add(chosen.box, null);
    obstacleGrid.add(chosen.box, null);
    if (chosenLabels > 0) {
      // Still in a leading label's way: back that label with a halo.
      for (const [id, box] of leadingBoxes) {
        if (overlapArea(chosen.box, box) > 0) {
          nodes.set(id, { ...nodes.get(id)!, halo: true });
        }
      }
    }
  }

  // 3. Every other label, against the edge labels where they now sit.
  for (const candidate of ordered) {
    if (isLeading(candidate.id)) continue;
    const textWidth = widthOf(candidate);
    let best: (PlacementCost & { overlap: number }) | undefined;
    for (const placement of PLACEMENTS) {
      const cost = costOf(candidate, placement, textWidth);
      const overlap = cost.labels + cost.obstacles;
      if (isBetter(cost.outside, overlap, best)) best = { ...cost, overlap };
      if (cost.outside <= OUTSIDE_TOLERANCE && overlap === 0) break;
    }
    nodes.set(candidate.id, {
      ...best!.label,
      halo: best!.labels + best!.obstacles > 0,
    });
    labelGrid.add(best!.box, null);
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
