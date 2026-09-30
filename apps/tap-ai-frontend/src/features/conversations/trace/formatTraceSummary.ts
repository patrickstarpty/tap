import type { TurnTraceSummary } from "../api/client";

function formatCost(summary: TurnTraceSummary, locale: "en" | "zh"): string {
  const amount =
    summary.costUsd == null || summary.costUsd.trim().length === 0
      ? null
      : Number(summary.costUsd);
  if (amount === null || Number.isNaN(amount)) {
    return locale === "zh" ? "成本未知" : "cost unknown";
  }
  const formatted = `$${amount.toFixed(4)}`;
  if (!summary.costIncomplete) return formatted;
  return locale === "zh"
    ? `${formatted}+（部分成本未知）`
    : `${formatted}+ (partly unknown)`;
}

export function formatTraceSummary(
  summary: TurnTraceSummary,
  locale: "en" | "zh",
): string {
  const seconds = (summary.totalDurationMs / 1000).toFixed(1);
  const duration = `${seconds}s`;
  const tokens = `${summary.inputTokens}/${summary.outputTokens} tokens`;
  const cost = formatCost(summary, locale);
  const requested = summary.requestedModels.join(", ");
  const upstream = summary.upstreamModels.join(", ");
  const models = `${requested} → ${upstream}`;
  return `${duration} · ${tokens} · ${cost} · ${models}`;
}
