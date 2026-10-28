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
  const currency = (n: number) =>
    new Intl.NumberFormat(locale, {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: COST_DIGITS,
    }).format(n);
  // A real but tiny spend must not read as zero.
  if (amount !== 0 && Math.abs(amount) < COST_FLOOR)
    return `<${currency(COST_FLOOR)}`;
  return currency(amount);
}

const COST_DIGITS = 4;
const COST_FLOOR = 10 ** -COST_DIGITS;

export type DurationUnits = WorkspaceCopy["agentTeam"]["durationUnits"];

export function formatDuration(
  ms: number | null | undefined,
  locale: string,
  units: DurationUnits,
): string {
  if (ms === null || ms === undefined) return EMPTY;
  const number = (value: number, digits = 0) =>
    new Intl.NumberFormat(locale, { maximumFractionDigits: digits }).format(
      value,
    );
  const part = (value: number, unit: string, digits = 0) =>
    `${number(value, digits)} ${unit}`;
  if (Math.round(ms) < 1000) return part(ms, units.ms);
  // Round once at the displayed precision, then split, so a value never
  // reads "60 s" or "1 m 60 s".
  const tenths = Math.round(ms / 100);
  if (tenths < 600) return part(tenths / 10, units.s, 1);
  const total = Math.round(ms / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours === 0)
    return `${part(minutes, units.m)} ${part(total % 60, units.s)}`;
  return `${part(hours, units.h)} ${part(minutes, units.m)}`;
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
