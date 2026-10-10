// Pure, React-free label collision avoidance for the graph canvas. Node
// positions come from the layout (`layout.ts`) and are never moved here;
// only each node's label is placed below its node, above it, or hidden, so
// that no two visible labels overlap.

export type LabelPlacement = "below" | "above";

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

export interface LabelBox {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export interface LabelPlacementOptions {
  /** Always labeled (hovered or selected nodes), even if they collide. */
  pinned: ReadonlySet<string>;
  /** Labeled ahead of other nodes (highlighted path, search matches, the
   * focused node's neighbors), but still subject to collisions. */
  emphasized: ReadonlySet<string>;
  /** The label font size in canvas units (must match the canvas CSS). */
  fontSize: number;
  /** Measures a label's rendered width in canvas units (e.g. with the
   * browser's text metrics); falls back to `estimateTextWidth`. */
  measureText?: (text: string) => number;
}

// Distance from the node's edge to the label baseline below it (the label
// sits just under the circle), and to the baseline above it (leaves room for
// the label's descenders plus the selection ring drawn at `radius + 7`).
const BELOW_BASELINE_OFFSET = 16;
const ABOVE_BASELINE_OFFSET = 14;
// Vertical extent of a sans-serif line box around the baseline, per em, on
// the generous side so a box always covers the rendered glyphs.
const ASCENT_EM = 0.95;
const DESCENT_EM = 0.2;
// Fallback advance widths per em when no measurement is available: CJK and
// other full-width characters take a full em; for Latin text, 0.6em is
// above the average advance of a semi-bold sans-serif.
const WIDE_CHAR_EM = 1;
const NARROW_CHAR_EM = 0.6;
const WIDE_CHAR =
  /[\u1100-\u115f\u2e80-\ua4cf\uac00-\ud7a3\uf900-\ufaff\ufe30-\ufe4f\uff00-\uff60\uffe0-\uffe6]/u;
// The label's outline stroke (`stroke-width` in the canvas CSS) plus a
// small gap kept between neighboring labels.
const LABEL_STROKE = 5;
const LABEL_GAP = 2;

const PLACEMENTS: readonly LabelPlacement[] = ["below", "above"];

export function estimateTextWidth(text: string, fontSize: number): number {
  let ems = 0;
  for (const char of text) {
    ems += WIDE_CHAR.test(char) ? WIDE_CHAR_EM : NARROW_CHAR_EM;
  }
  return ems * fontSize;
}

export function labelBaseline(
  node: Pick<LabelCandidate, "y" | "radius">,
  placement: LabelPlacement,
): number {
  return placement === "below"
    ? node.y + node.radius + BELOW_BASELINE_OFFSET
    : node.y - node.radius - ABOVE_BASELINE_OFFSET;
}

export function labelBox(
  node: LabelCandidate,
  placement: LabelPlacement,
  fontSize: number,
  textWidth: number = estimateTextWidth(node.text, fontSize),
): LabelBox {
  const baseline = labelBaseline(node, placement);
  const margin = LABEL_STROKE / 2 + LABEL_GAP / 2;
  const halfWidth = textWidth / 2 + margin;
  return {
    left: node.x - halfWidth,
    right: node.x + halfWidth,
    top: baseline - fontSize * ASCENT_EM - margin,
    bottom: baseline + fontSize * DESCENT_EM + margin,
  };
}

function intersects(a: LabelBox, b: LabelBox): boolean {
  return (
    a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom
  );
}

function tier(id: string, options: LabelPlacementOptions): number {
  if (options.pinned.has(id)) return 0;
  if (options.emphasized.has(id)) return 1;
  return 2;
}

/**
 * Greedily places labels in priority order — pinned, then emphasized, then
 * by descending degree, ties kept in input order — trying below the node
 * first and above it second; a label with neither slot free is hidden
 * (absent from the result). Pinned labels are always placed (in their first
 * free slot, else below), so later labels yield to them.
 */
export function placeNodeLabels(
  candidates: readonly LabelCandidate[],
  options: LabelPlacementOptions,
): Map<string, LabelPlacement> {
  const ordered = candidates
    .map((candidate, index) => ({ candidate, index }))
    .sort(
      (a, b) =>
        tier(a.candidate.id, options) - tier(b.candidate.id, options) ||
        b.candidate.degree - a.candidate.degree ||
        a.index - b.index,
    );

  const { fontSize, measureText } = options;
  const placed: LabelBox[] = [];
  const placements = new Map<string, LabelPlacement>();
  for (const { candidate } of ordered) {
    const textWidth =
      measureText?.(candidate.text) ??
      estimateTextWidth(candidate.text, fontSize);
    let chosen: LabelPlacement | null = null;
    let chosenBox: LabelBox | null = null;
    for (const placement of PLACEMENTS) {
      const box = labelBox(candidate, placement, fontSize, textWidth);
      if (!placed.some((other) => intersects(box, other))) {
        chosen = placement;
        chosenBox = box;
        break;
      }
    }
    if (chosen === null && options.pinned.has(candidate.id)) {
      chosen = "below";
      chosenBox = labelBox(candidate, "below", fontSize, textWidth);
    }
    if (chosen === null || chosenBox === null) continue;
    placements.set(candidate.id, chosen);
    placed.push(chosenBox);
  }
  return placements;
}
