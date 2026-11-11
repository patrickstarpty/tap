import { expect, it, vi } from "vitest";
import { createLcaClient, LcaHttpError } from "./client";

it("uses project-scoped same-origin requests and caller-owned replay keys", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(new Response(JSON.stringify({ id: "run-1" })));
  const client = createLcaClient(fetcher);
  await client.createRun(
    "project a",
    "flow-1",
    { expected_version: 2, parameters: { count: 3 } },
    "stable-key",
  );
  expect(fetcher).toHaveBeenCalledWith(
    "/api/projects/project%20a/lca/flows/flow-1/runs",
    expect.objectContaining({
      method: "POST",
      credentials: "same-origin",
      body: JSON.stringify({ expected_version: 2, parameters: { count: 3 } }),
    }),
  );
  expect(
    new Headers(fetcher.mock.calls[0][1].headers).get("Idempotency-Key"),
  ).toBe("stable-key");
  expect(
    new Headers(fetcher.mock.calls[0][1].headers).has("Authorization"),
  ).toBe(false);
});

it("reports unavailable capability and does not retry a mutation", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(new Response("unavailable", { status: 503 }));
  await expect(createLcaClient(fetcher).listFlows("p")).rejects.toEqual(
    expect.objectContaining({ status: 503 }),
  );
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(new LcaHttpError(409).message).toContain("changed");
});

it("reads evidence only through its project and run authorized route", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response("image-bytes"));
  const bytes = await createLcaClient(fetcher).getEvidence(
    "p",
    "run-1",
    "evidence-1",
  );
  expect(await bytes.text()).toBe("image-bytes");
  expect(fetcher.mock.calls[0][0]).toBe(
    "/api/projects/p/lca/runs/run-1/evidence/evidence-1",
  );
  expect(fetcher.mock.calls[0][1].credentials).toBe("same-origin");
});

it.each([
  ['attachment; filename="invoice.csv"', "invoice.csv"],
  ['attachment; filename="report_2026.pdf"', "report_2026.pdf"],
  ['attachment; filename="../invoice.csv"', null],
  ['attachment; filename="C:\\report.pdf"', null],
  ['attachment; filename="invoice\u0001.csv"', null],
  ['attachment; filename=".."', null],
  [null, null],
])(
  "preserves only a safe attachment filename from %s",
  async (header, filename) => {
    const headers = new Headers();
    if (header) headers.set("Content-Disposition", header);
    const fetcher = vi
      .fn()
      .mockResolvedValue(new Response("file-bytes", { headers }));
    const download = await createLcaClient(fetcher).getEvidenceDownload(
      "p",
      "run-1",
      "download-1",
    );
    expect(download.filename).toBe(filename);
    expect(await download.blob.text()).toBe("file-bytes");
    expect(fetcher.mock.calls[0][0]).toBe(
      "/api/projects/p/lca/runs/run-1/evidence/download-1",
    );
    expect(fetcher.mock.calls[0][1]).toEqual({
      credentials: "same-origin",
      cache: "no-store",
    });
  },
);
