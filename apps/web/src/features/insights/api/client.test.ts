import { expect, it, vi } from "vitest";

import { createInsightsClient } from "./client";

function response(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

it("collects every authorized detail page instead of treating the first 50 as complete", async () => {
  const fetch = vi.fn(async (input: string | URL | Request) => {
    const url = String(input);
    if (url.includes("cursor=50"))
      return response({ queryId: "query-1", items: [{ runId: "run-2" }], nextCursor: null });
    return response({ queryId: "query-1", items: [{ runId: "run-1" }], nextCursor: "50" });
  }) as unknown as typeof window.fetch;
  const client = createInsightsClient({ token: () => "token", fetch });

  const page = await client.listRuns("project-a", "query-1");

  expect(page.items).toEqual([{ runId: "run-1" }, { runId: "run-2" }]);
  expect(page.nextCursor).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(String(vi.mocked(fetch).mock.calls[1]![0])).toContain("cursor=50");
});
