import { EMPTY } from "./format";

/** Whether a rise is good news (runs), bad news (failures, cost) or neither
 * (total latency grows with use). Colours a delta; the arrow carries the
 * direction so colour is never the only cue. */
export type Polarity = "up-good" | "up-bad" | "neutral";

export interface Delta {
  diff: number;
  /** Rounded percent change against the previous value. */
  percent: number;
  direction: "up" | "down" | "flat";
}

/** Change against a baseline; null when there is none (unknown or zero). */
export function deltaOf(
  current: number | null | undefined,
  previous: number | null | undefined,
): Delta | null {
  if (current === null || current === undefined) return null;
  if (previous === null || previous === undefined || previous === 0)
    return null;
  const diff = current - previous;
  return {
    diff,
    percent: Math.round((diff / previous) * 100),
    direction: diff > 0 ? "up" : diff < 0 ? "down" : "flat",
  };
}

export function deltaTone(
  delta: Delta | null,
  polarity: Polarity,
): "good" | "bad" | "neutral" | "none" {
  if (delta === null) return "none";
  if (delta.direction === "flat" || polarity === "neutral") return "neutral";
  const better =
    (delta.direction === "up") === (polarity === "up-good") ? "good" : "bad";
  return better;
}

export const ARROWS = { up: "▲", down: "▼", flat: "▬" } as const;

/** "▲ 20%" in a soft tint; "—" without a baseline. */
export function DeltaBadge({
  current,
  previous,
  polarity,
  title,
  locale,
}: {
  current: number | null | undefined;
  previous: number | null | undefined;
  polarity: Polarity;
  /** Names the baseline (hover), e.g. the previous period and its value. */
  title?: string;
  locale: string;
}) {
  const delta = deltaOf(current, previous);
  const tone = deltaTone(delta, polarity);
  if (delta === null)
    return (
      <span className="tap-delta" data-tone="none" title={title}>
        {EMPTY}
      </span>
    );
  const percent = new Intl.NumberFormat(locale, {
    maximumFractionDigits: 0,
  }).format(Math.abs(delta.percent));
  return (
    <span className="tap-delta" data-tone={tone} title={title}>
      <span aria-hidden="true" className="tap-delta-arrow">
        {ARROWS[delta.direction]}
      </span>
      <span className="tap-team-sr-only">
        {delta.direction === "down" ? "−" : delta.direction === "up" ? "+" : ""}
      </span>
      {percent}%
    </span>
  );
}
