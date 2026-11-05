import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  agentTeamExportUrl,
  resolvePeriod,
  useTeamMembers,
  useTeamReport,
} from "./agentTeam";

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

describe("useTeamReport", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends the period preset so the server derives the previous period", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ data: {} }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client }, children);
    const { result } = renderHook(
      () =>
        useTeamReport("p1", { from: "2026-10-01", to: "2026-10-07" }, "last7"),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = String((fetchMock.mock.calls[0] as unknown[])[0]);
    expect(url).toContain(
      "/agent-team/report?from=2026-10-01&to=2026-10-07&preset=last7",
    );
  });
});

describe("useTeamMembers", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shares one request between empty and null-valued filters", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ data: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client }, children);
    const period = { from: "2026-09-28", to: "2026-10-27" };
    const { result } = renderHook(
      () => [
        useTeamMembers("p1", period, { owner: null, status: null, kind: null }),
        useTeamMembers("p1", period, {}),
      ],
      { wrapper },
    );
    await waitFor(() =>
      expect(result.current.every((q) => q.isSuccess)).toBe(true),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
