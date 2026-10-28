import { describe, expect, it } from "vitest";

import { WORKSPACE_COPY } from "../../copy";
import {
  formatCost,
  formatDuration,
  formatRelative,
  memberDisplayName,
} from "./format";

const en = WORKSPACE_COPY.en.agentTeam.durationUnits;
const zh = WORKSPACE_COPY.zh.agentTeam.durationUnits;

describe("formatDuration", () => {
  it("rounds to whole seconds before splitting minutes and hours", () => {
    expect(formatDuration(119_600, "en", en)).toBe("2 m 0 s");
    expect(formatDuration(3_599_999, "en", en)).toBe("1 h 0 m");
    expect(formatDuration(59_960, "en", en)).toBe("1 m 0 s");
    expect(formatDuration(1_061_000, "en", en)).toBe("17 m 41 s");
  });

  it("keeps sub-minute and sub-second precision", () => {
    expect(formatDuration(800, "en", en)).toBe("800 ms");
    expect(formatDuration(12_340, "en", en)).toBe("12.3 s");
    expect(formatDuration(null, "en", en)).toBe("—");
  });

  it("uses Chinese units on Chinese screens", () => {
    expect(formatDuration(1_061_000, "zh-CN", zh)).toBe("17 分 41 秒");
    expect(formatDuration(7_260_000, "zh-CN", zh)).toBe("2 小时 1 分");
    expect(formatDuration(800, "zh-CN", zh)).toBe("800 毫秒");
    expect(formatDuration(12_340, "zh-CN", zh)).toBe("12.3 秒");
  });
});

describe("formatCost", () => {
  it("never prints a tiny non-zero amount as zero", () => {
    expect(formatCost("0.00003", "zh-CN")).toBe("<US$0.0001");
    expect(formatCost("0", "zh-CN")).toBe("US$0.00");
    expect(formatCost("1.0507", "zh-CN")).toBe("US$1.0507");
    expect(formatCost("0.0042", "en")).toBe("$0.0042");
  });
});

describe("formatRelative", () => {
  const now = Date.parse("2026-10-28T12:00:00Z");
  it("reads recent times relative to now", () => {
    expect(formatRelative("2026-10-28T11:57:00Z", "en", now)).toBe(
      "3 minutes ago",
    );
    expect(formatRelative("2026-10-26T12:00:00Z", "en", now)).toBe(
      "2 days ago",
    );
    expect(formatRelative("2026-10-28T11:59:40Z", "en", now)).toBe("now");
    expect(formatRelative("2026-10-28T09:00:00Z", "zh-CN", now)).toBe(
      "3小时前",
    );
    expect(formatRelative(null, "en", now)).toBe("—");
  });
});

describe("memberDisplayName", () => {
  const tapper = { kind: "tapper_default" as const, name: "Tapper 默认" };

  it("localises the Tapper default member, whatever name the server sent", () => {
    expect(memberDisplayName(tapper, WORKSPACE_COPY.en.agentTeam)).toBe(
      "Tapper",
    );
    expect(memberDisplayName(tapper, WORKSPACE_COPY.zh.agentTeam)).toBe(
      "Tapper 默认",
    );
  });

  it("keeps every other member's own name", () => {
    const custom = { kind: "custom" as const, name: "Reviewer" };
    const builtIn = { kind: "built_in" as const, name: "Planner" };
    expect(memberDisplayName(custom, WORKSPACE_COPY.en.agentTeam)).toBe(
      "Reviewer",
    );
    expect(memberDisplayName(builtIn, WORKSPACE_COPY.zh.agentTeam)).toBe(
      "Planner",
    );
  });
});
