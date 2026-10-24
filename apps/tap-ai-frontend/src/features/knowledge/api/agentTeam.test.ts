import { describe, expect, it } from "vitest";

import { agentTeamExportUrl, resolvePeriod } from "./agentTeam";

const today = new Date(Date.UTC(2026, 9, 23));

describe("resolvePeriod", () => {
  it("resolves the four presets in UTC", () => {
    expect(resolvePeriod("last7", today)).toEqual({
      from: "2026-10-17",
      to: "2026-10-23",
    });
    expect(resolvePeriod("last30", today)).toEqual({
      from: "2026-09-24",
      to: "2026-10-23",
    });
    expect(resolvePeriod("thisMonth", today)).toEqual({
      from: "2026-10-01",
      to: "2026-10-23",
    });
    expect(resolvePeriod("lastMonth", today)).toEqual({
      from: "2026-09-01",
      to: "2026-09-30",
    });
  });

  it("passes a custom range through", () => {
    const custom = { from: "2026-01-02", to: "2026-02-03" };
    expect(resolvePeriod("custom", today, custom)).toEqual(custom);
  });

  it("handles January rolling back to December", () => {
    expect(resolvePeriod("lastMonth", new Date(Date.UTC(2026, 0, 15)))).toEqual(
      {
        from: "2025-12-01",
        to: "2025-12-31",
      },
    );
  });
});

describe("agentTeamExportUrl", () => {
  it("builds the export path", () => {
    expect(
      agentTeamExportUrl("p 1", { from: "2026-09-01", to: "2026-09-30" }),
    ).toBe(
      "/api/v1/projects/p%201/ai/agent-team/export?from=2026-09-01&to=2026-09-30",
    );
  });
});
