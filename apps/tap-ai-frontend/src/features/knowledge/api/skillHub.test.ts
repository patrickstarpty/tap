import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { aiAssetKeys } from "./aiAssets";
import {
  skillHubExportUrl,
  skillHubKeys,
  useSkillHubCatalog,
  useSkillHubDetail,
  useSkillHubSummary,
  useSkillHubUsage,
  useTransferSkillOwner,
  useUpdateSkillProfile,
} from "./skillHub";

const period = { from: "2026-10-01", to: "2026-10-30" };

function stubFetch(body: unknown = { data: [] }) {
  const fetchMock = vi.fn<
    (url: string, init?: RequestInit) => Promise<Response>
  >(() =>
    Promise.resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children);
  return { client, wrapper };
}

const urlOf = (fetchMock: ReturnType<typeof stubFetch>, index = 0) =>
  String(fetchMock.mock.calls[index]?.[0]);

afterEach(() => vi.unstubAllGlobals());

describe("skillHubExportUrl", () => {
  it("builds the export path with the period", () => {
    expect(skillHubExportUrl("p 1", period)).toBe(
      "/api/v1/projects/p%201/ai/skill-hub/export?from=2026-10-01&to=2026-10-30",
    );
  });
});

describe("useSkillHubCatalog", () => {
  it("sends the period, every filter and the sort under their query names", async () => {
    const fetchMock = stubFetch();
    const { wrapper } = setup();
    const { result } = renderHook(
      () =>
        useSkillHubCatalog("p1", period, {
          q: "review",
          category: "test-design",
          task: "knowledge.answer",
          source: "imported",
          status: "enabled",
          sort: "last_used_at",
          order: "asc",
        }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const url = new URL(urlOf(fetchMock), "http://localhost");
    expect(url.pathname).toBe("/api/v1/projects/p1/ai/skill-hub/catalog");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      from: "2026-10-01",
      to: "2026-10-30",
      q: "review",
      category: "test-design",
      task: "knowledge.answer",
      source: "imported",
      status: "enabled",
      sort: "last_used_at",
      order: "asc",
    });
  });

  it("omits unset filters and shares one request with an empty filter set", async () => {
    const fetchMock = stubFetch();
    const { wrapper } = setup();
    const { result } = renderHook(
      () => [
        useSkillHubCatalog("p1", period, { q: "", category: undefined }),
        useSkillHubCatalog("p1", period, {}),
      ],
      { wrapper },
    );
    await waitFor(() =>
      expect(result.current.every((query) => query.isSuccess)).toBe(true),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(urlOf(fetchMock)).toBe(
      "/api/v1/projects/p1/ai/skill-hub/catalog?from=2026-10-01&to=2026-10-30",
    );
  });
});

describe("skill hub reads", () => {
  it("fetches the summary for the period", async () => {
    const fetchMock = stubFetch();
    const { wrapper } = setup();
    const { result } = renderHook(() => useSkillHubSummary("p1", period), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(urlOf(fetchMock)).toBe(
      "/api/v1/projects/p1/ai/skill-hub/summary?from=2026-10-01&to=2026-10-30",
    );
  });

  it("fetches one Skill's detail and usage, and waits for a Skill id", async () => {
    const fetchMock = stubFetch();
    const { wrapper } = setup();
    const { result, rerender } = renderHook(
      ({ skillId }: { skillId: string | null }) => [
        useSkillHubDetail("p1", skillId, period),
        useSkillHubUsage("p1", skillId, period),
      ],
      { wrapper, initialProps: { skillId: null as string | null } },
    );
    expect(fetchMock).not.toHaveBeenCalled();
    rerender({ skillId: "bdd scenario" });
    await waitFor(() =>
      expect(result.current.every((query) => query.isSuccess)).toBe(true),
    );
    const urls = fetchMock.mock.calls.map((call) => String(call[0])).sort();
    expect(urls).toEqual([
      "/api/v1/projects/p1/ai/skill-hub/catalog/bdd%20scenario/usage?from=2026-10-01&to=2026-10-30",
      "/api/v1/projects/p1/ai/skill-hub/catalog/bdd%20scenario?from=2026-10-01&to=2026-10-30",
    ]);
  });
});

describe("skill hub writes", () => {
  it("updates the category and refreshes the library and Skill catalogs", async () => {
    const fetchMock = stubFetch({});
    const { client, wrapper } = setup();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useUpdateSkillProfile("p1"), {
      wrapper,
    });
    await act(() =>
      result.current.mutateAsync({ skillId: "s1", category: "writing" }),
    );
    expect(urlOf(fetchMock)).toBe("/api/v1/projects/p1/ai/skills/s1/profile");
    const init = fetchMock.mock.calls[0]?.[1];
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual({ category: "writing" });
    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toContainEqual(skillHubKeys.all("p1"));
    expect(keys).toContainEqual(aiAssetKeys.all("p1"));
  });

  it("transfers the owner with the new owner's actor id", async () => {
    const fetchMock = stubFetch({});
    const { client, wrapper } = setup();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useTransferSkillOwner("p1"), {
      wrapper,
    });
    await act(() =>
      result.current.mutateAsync({ skillId: "s1", newOwnerActorId: "bob" }),
    );
    expect(urlOf(fetchMock)).toBe("/api/v1/projects/p1/ai/skills/s1/owner");
    const init = fetchMock.mock.calls[0]?.[1];
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ newOwnerActorId: "bob" });
    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toContainEqual(skillHubKeys.all("p1"));
    expect(keys).toContainEqual(aiAssetKeys.all("p1"));
  });
});
