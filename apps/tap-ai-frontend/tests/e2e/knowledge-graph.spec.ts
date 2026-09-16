import { expect, test, type Route } from "@playwright/test";

const ORIGIN = "http://127.0.0.1:15173";

test("ready knowledge is published as a bounded grounded graph", async ({
  page,
}) => {
  const runtimeResponse = await page.request.get("/api/v1/runtime-mode");
  expect(runtimeResponse.status()).toBe(200);
  const runtime = (await runtimeResponse.json()) as { projectId: string };
  const root = `/api/v1/projects/${encodeURIComponent(runtime.projectId)}`;
  const filename = `graph-evidence-${Date.now()}.md`;
  const upload = await page.request.post(`${root}/knowledge/sources`, {
    headers: {
      Origin: ORIGIN,
      "Idempotency-Key": `graph-source-${Date.now()}`,
    },
    multipart: {
      upload: {
        name: filename,
        mimeType: "text/markdown",
        buffer: Buffer.from(
          "# Claims policy\n\nA verified claim requires supporting evidence.",
        ),
      },
    },
  });
  expect(upload.status()).toBe(202);
  const accepted = (await upload.json()) as { source: { sourceId: string } };
  let revisionId = "";
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${root}/knowledge/sources/${accepted.source.sourceId}?limit=50`,
        );
        const detail = (await response.json()) as {
          documents: { items: Array<{ revisionId: string; status: string }> };
        };
        revisionId =
          detail.documents.items.find((item) => item.status === "ready")
            ?.revisionId ?? "";
        return revisionId;
      },
      { timeout: 45_000 },
    )
    .not.toBe("");

  let snapshotId = "";
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${root}/knowledge/graph/snapshots`,
          {
            params: { sourceRevisionId: revisionId },
          },
        );
        const body = (await response.json()) as {
          items: Array<{ snapshotId: string }>;
        };
        snapshotId = body.items[0]?.snapshotId ?? "";
        return snapshotId;
      },
      { timeout: 15_000 },
    )
    .not.toBe("");

  const graph = await page.request.post(`${root}/knowledge/graph/query`, {
    headers: { Origin: ORIGIN },
    data: { snapshotId, query: "*", nodeLimit: 500 },
  });
  expect(graph.status()).toBe(200);
  const body = (await graph.json()) as {
    nodes: Array<{ nodeId: string }>;
    edges: Array<{ origin: string; evidenceIds: string[] }>;
    evidence: Array<{ evidenceId: string }>;
  };
  expect(body.nodes.length).toBeLessThanOrEqual(500);
  expect(body.edges[0]?.origin).toBe("EXTRACTED");
  const evidenceId = body.evidence[0]!.evidenceId;
  const evidence = await page.request.get(
    `${root}/knowledge/graph/evidence/${evidenceId}?snapshotId=${snapshotId}`,
  );
  expect(evidence.status()).toBe(200);

  await page.goto("/");
  await page.getByRole("button", { name: /Library|知识库/u }).click();
  await page.getByRole("textbox", { name: "Search library" }).fill(filename);
  await expect(
    page
      .getByRole("tabpanel", { name: /Documents|文档列表/u })
      .getByRole("button", { name: `View ${filename}`, exact: true }),
  ).toBeVisible();
  const activeGraphResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      new URL(response.url()).pathname.endsWith("/knowledge/graph/snapshots"),
  );
  await page.getByRole("tab", { name: /Graph|图谱/u }).click();
  const activeGraph = await activeGraphResponse;
  expect(activeGraph.status(), await activeGraph.text()).toBe(200);
  expect(
    ((await activeGraph.json()) as { items: unknown[] }).items.length,
  ).toBeGreaterThan(0);
  const explorer = page.getByRole("region", {
    name: "Knowledge graph explorer",
  });
  await expect(explorer).toBeVisible();
  await expect(explorer.getByText(/nodes in this bounded view/u)).toBeVisible();

  const unavailable = async (route: Route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/problem+json",
      body: "{}",
    });
  };
  const graphSnapshotPattern = /\/knowledge\/graph\/snapshots(?:\?|$)/u;
  try {
    await page.route(graphSnapshotPattern, unavailable);
    await page.reload();
    await page.getByRole("button", { name: /Library|知识库/u }).click();
    await expect(
      page
        .getByRole("tabpanel", { name: /Documents|文档列表/u })
        .getByRole("button", { name: `View ${filename}`, exact: true }),
    ).toBeVisible();
    await page.getByRole("tab", { name: /Graph|图谱/u }).click();
    await expect(page.getByRole("alert")).toContainText(
      "knowledge graph is temporarily unavailable",
    );
  } finally {
    await page.unroute(graphSnapshotPattern, unavailable);
    const deleted = await page.request.delete(
      `${root}/knowledge/sources/${accepted.source.sourceId}`,
      {
        headers: {
          Origin: ORIGIN,
          "Idempotency-Key": `graph-cleanup-${accepted.source.sourceId}`,
        },
      },
    );
    expect(deleted.status()).toBe(204);
    await expect
      .poll(
        async () =>
          (
            await page.request.get(
              `${root}/knowledge/sources/${accepted.source.sourceId}`,
            )
          ).status(),
        { timeout: 45_000 },
      )
      .toBe(404);
  }
});
