import type { AgentTeamMemberRow } from "../../../../../features/knowledge/api/agentTeam";
import type { WorkspaceCopy } from "../../copy";

export const EMPTY = "—";

/** The codebase derives language from copy text; keep that check in one place. */
export function teamLocale(copy: WorkspaceCopy): "en" | "zh-CN" {
  return copy.agentTeam.heading === "Agent team" ? "en" : "zh-CN";
}

/** The name a member is shown under. The server sends the Tapper default
 * member with one fixed-language name, so it is localised here. */
export function memberDisplayName(
  member: Pick<AgentTeamMemberRow, "kind" | "name">,
  text: WorkspaceCopy["agentTeam"],
): string {
  return member.kind === "tapper_default"
    ? text.tapperDefaultName
    : member.name;
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

/** "3 minutes ago" / "3 分钟前"; the absolute time belongs in a tooltip. */
export function formatRelative(
  value: string | null | undefined,
  locale: string,
  now: number = Date.now(),
): string {
  if (value === null || value === undefined) return EMPTY;
  const then = Date.parse(value);
  if (Number.isNaN(then)) return EMPTY;
  const seconds = Math.round((then - now) / 1000);
  const format = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  const abs = Math.abs(seconds);
  if (abs < 45) return format.format(0, "second");
  if (abs < 3600) return format.format(Math.round(seconds / 60), "minute");
  if (abs < 86_400) return format.format(Math.round(seconds / 3600), "hour");
  if (abs < 30 * 86_400)
    return format.format(Math.round(seconds / 86_400), "day");
  if (abs < 365 * 86_400)
    return format.format(Math.round(seconds / (30 * 86_400)), "month");
  return format.format(Math.round(seconds / (365 * 86_400)), "year");
}

/** Axis ticks and other tight spots: 1.2K / 1.2万. */
export function formatCompact(value: number, locale: string): string {
  return new Intl.NumberFormat(locale, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

/** A bucket key (YYYY-MM-DD) as a short axis label, e.g. 10/12. */
export function formatShortDate(key: string, locale: string): string {
  const ms = Date.parse(`${key}T00:00:00Z`);
  if (Number.isNaN(ms)) return key;
  return new Intl.DateTimeFormat(locale, {
    timeZone: "UTC",
    month: "numeric",
    day: "numeric",
  }).format(ms);
}

/** Short weekday for a bucket key, e.g. 周一 / Mon. */
export function formatWeekday(key: string, locale: string): string {
  const ms = Date.parse(`${key}T00:00:00Z`);
  if (Number.isNaN(ms)) return "";
  return new Intl.DateTimeFormat(locale, {
    timeZone: "UTC",
    weekday: "short",
  }).format(ms);
}
