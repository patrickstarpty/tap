import type { WorkspaceCopy } from "../../copy";

export const EMPTY = "—";

/** The codebase derives language from copy text; keep that check in one place. */
export function teamLocale(copy: WorkspaceCopy): "en" | "zh-CN" {
  return copy.agentTeam.heading === "Agent team" ? "en" : "zh-CN";
}

export function formatCount(
  value: number | null | undefined,
  locale: string,
): string {
  if (value === null || value === undefined) return EMPTY;
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(
    value,
  );
}

export function formatPercent(
  value: number | null | undefined,
  locale: string,
): string {
  if (value === null || value === undefined) return EMPTY;
  return new Intl.NumberFormat(locale, {
    style: "percent",
    maximumFractionDigits: 0,
  }).format(value);
}

export function formatCost(
  value: string | null | undefined,
  locale: string,
): string {
  if (value === null || value === undefined) return EMPTY;
  const amount = Number(value);
  if (!Number.isFinite(amount)) return EMPTY;
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(amount);
}

export function formatDuration(
  ms: number | null | undefined,
  locale: string,
): string {
  if (ms === null || ms === undefined) return EMPTY;
  const number = (value: number, digits = 0) =>
    new Intl.NumberFormat(locale, { maximumFractionDigits: digits }).format(
      value,
    );
  if (ms < 1000) return `${number(ms)} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${number(seconds, 1)} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60)
    return `${number(minutes)} m ${number(Math.round(seconds % 60))} s`;
  return `${number(Math.floor(minutes / 60))} h ${number(minutes % 60)} m`;
}

export function formatTime(
  value: string,
  timeZone: string,
  locale: string,
): string {
  try {
    return new Intl.DateTimeFormat(locale, {
      timeZone,
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).format(new Date(value));
  } catch {
    return value;
  }
}

export function formatDateTime(
  value: string | null | undefined,
  timeZone: string,
  locale: string,
): string {
  if (value === null || value === undefined) return EMPTY;
  try {
    return new Intl.DateTimeFormat(locale, {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).format(new Date(value));
  } catch {
    return value;
  }
}

export function formatDate(
  value: string | null | undefined,
  timeZone: string,
  locale: string,
): string {
  if (value === null || value === undefined) return EMPTY;
  try {
    return new Intl.DateTimeFormat(locale, {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date(value));
  } catch {
    return value;
  }
}
