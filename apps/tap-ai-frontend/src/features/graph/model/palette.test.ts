import { describe, expect, it } from "vitest";
import { GRAPH_PALETTE } from "./palette";

// WCAG 2.x contrast ratio, used here for the non-text (graphical object)
// minimum of 3:1 against the white canvas background.
function srgbChannelToLinear(channel: number): number {
  const normalized = channel / 255;
  return normalized <= 0.03928
    ? normalized / 12.92
    : Math.pow((normalized + 0.055) / 1.055, 2.4);
}

function relativeLuminance(hex: string): number {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  const [R, G, B] = [r, g, b].map(srgbChannelToLinear);
  return 0.2126 * R + 0.7152 * G + 0.0722 * B;
}

function contrastRatio(hexA: string, hexB: string): number {
  const luminanceA = relativeLuminance(hexA);
  const luminanceB = relativeLuminance(hexB);
  const lighter = Math.max(luminanceA, luminanceB);
  const darker = Math.min(luminanceA, luminanceB);
  return (lighter + 0.05) / (darker + 0.05);
}

describe("GRAPH_PALETTE", () => {
  it("has 12 colors", () => {
    expect(GRAPH_PALETTE).toHaveLength(12);
  });

  it("meets the 3:1 non-text contrast minimum against white for every color", () => {
    for (const color of GRAPH_PALETTE) {
      expect(contrastRatio(color, "#ffffff")).toBeGreaterThanOrEqual(3);
    }
  });
});
