// Shared categorical palette for graph communities, padded to 12 entries from
// the existing swatches in widgets/tap/workspace/graphLayout.ts:44-54 (the
// "sources" gray is reused below as the dedicated "other" community color).
export const GRAPH_PALETTE: readonly string[] = [
  "#2563eb",
  "#6366f1",
  "#0d9488",
  "#d97706",
  "#b86b48",
  "#e05b73",
  "#8b5cf6",
  "#0891b2",
  "#15803d",
  "#c2410c",
  "#a21caf",
  "#4d7c0f",
];

export const OTHER_COMMUNITY_COLOR = "#64748b";

export function communityColor(index: number, isOther: boolean): string {
  if (isOther) return OTHER_COMMUNITY_COLOR;
  return GRAPH_PALETTE[index % GRAPH_PALETTE.length];
}
