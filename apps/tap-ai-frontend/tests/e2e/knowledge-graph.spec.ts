import { expect, test, type Route } from "@playwright/test";
import {
  preparePublishedFixture,
  waitForProjectGraph,
} from "./publicationFixture";

const ORIGIN = "http://127.0.0.1:15173";

interface ProjectGraphOverview {
  nodes: Array<{ nodeId: string; label: string }>;
}

interface ProjectGraphNodeDetail {
  sources: Array<{ sourceRevisionId: string }>;
}

async function uploadMarkdown(
  page: import("@playwright/test").Page,
  root: string,
  filename: string,
  content: string,
): Promise<{ sourceId: string }> {
  const upload = await page.request.post(`${root}/knowledge/sources`, {
    headers: {
      Origin: ORIGIN,
      "Idempotency-Key": `${filename}-${Date.now()}`,
    },
    multipart: {
      upload: {
        name: filename,
        mimeType: "text/markdown",
        buffer: Buffer.from(content),
      },
    },
  });
  expect(upload.status()).toBe(202);
  const accepted = (await upload.json()) as { source: { sourceId: string } };
  return { sourceId: accepted.source.sourceId };
}

async function waitForReadyRevision(
  page: import("@playwright/test").Page,
  root: string,
  sourceId: string,
): Promise<string> {
  let revisionId = "";
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${root}/knowledge/sources/${sourceId}?limit=50`,
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
  return revisionId;
}

test("graph overview cites a cross-document relation and highlights it in Library", async ({
  page,
}) => {
  const runtimeResponse = await page.request.get("/api/v1/runtime-mode");
  expect(runtimeResponse.status()).toBe(200);
  const runtime = (await runtimeResponse.json()) as { projectId: string };
  const root = `/api/v1/projects/${encodeURIComponent(runtime.projectId)}`;

  const ts = Date.now();
  const filenameA = `graph-a-${ts}.md`;
  const filenameB = `graph-b-${ts}.md`;
  const { sourceId: sourceIdA } = await uploadMarkdown(
    page,
    root,
    filenameA,
    "# Underwriting guide\n\nUnderwriting review requires health disclosure.",
  );
  const { sourceId: sourceIdB } = await uploadMarkdown(
    page,
    root,
    filenameB,
    "# Claims handbook\n\nHealth disclosure is validated by the claims assessor.",
  );
  const revisionIdA = await waitForReadyRevision(page, root, sourceIdA);
  const revisionIdB = await waitForReadyRevision(page, root, sourceIdB);

  preparePublishedFixture([revisionIdA, revisionIdB]);
  await waitForProjectGraph(page, root, [revisionIdA, revisionIdB]);

  // Cross-document node: the overview must carry a node labelled "health
  // disclosure" whose node detail resolves evidence from both published
  // revisions (two source groups), not just the revision it first appears in.
  const overviewParams = new URLSearchParams();
  overviewParams.append("sourceRevisionId", revisionIdA);
  overviewParams.append("sourceRevisionId", revisionIdB);
  const overviewResponse = await page.request.get(
    `${root}/knowledge/graph/overview?${overviewParams.toString()}`,
  );
  expect(overviewResponse.status()).toBe(200);
  const overview = (await overviewResponse.json()) as ProjectGraphOverview;
  const healthDisclosureNode = overview.nodes.find((node) =>
    /health disclosure/iu.test(node.label),
  );
  expect(healthDisclosureNode).toBeDefined();
  const nodeDetailResponse = await page.request.get(
    `${root}/knowledge/graph/nodes/${encodeURIComponent(healthDisclosureNode!.nodeId)}`,
  );
  expect(nodeDetailResponse.status()).toBe(200);
  const nodeDetail =
    (await nodeDetailResponse.json()) as ProjectGraphNodeDetail;
  expect(nodeDetail.sources.length).toBe(2);

  await page.goto("/");
  await page.getByRole("button", { name: /Library|知识库/u }).click();
  await page
    .getByRole("textbox", { name: "Search library" })
    .fill("health disclosure");
  const projectGraphResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      new URL(response.url()).pathname.endsWith("/knowledge/graph/project"),
  );
  await page.getByRole("tab", { name: /Knowledge Graph|知识图谱/u }).click();
  const projectGraph = await projectGraphResponse;
  expect(projectGraph.status(), await projectGraph.text()).toBe(200);
  await expect(
    page
      .getByRole("figure")
      .getByRole("button", { name: /health disclosure/iu }),
  ).toBeVisible();

  // New chat: scope the question to both sources so the relation extracted
  // from document A ("Underwriting review REQUIRES health disclosure") is
  // answerable only from the combined context, and must come back as an
  // edge citation (R1), not a plain chunk citation.
  await page.getByRole("button", { name: "New chat", exact: true }).click();
  await page
    .getByRole("checkbox", { name: new RegExp(filenameA, "u") })
    .check();
  await page
    .getByRole("checkbox", { name: new RegExp(filenameB, "u") })
    .check();

  const [agentsResponse, skillsResponse, modelsResponse] = await Promise.all([
    page.request.get(`${root}/ai/agents`),
    page.request.get(`${root}/ai/skills`),
    page.request.get(`${root}/ai/models`),
  ]);
  expect(agentsResponse.status()).toBe(200);
  expect(skillsResponse.status()).toBe(200);
  expect(modelsResponse.status()).toBe(200);
  const agents = (await agentsResponse.json()) as {
    items: Array<{ displayName: string }>;
  };
  const skills = (await skillsResponse.json()) as {
    items: Array<{ displayName: string }>;
  };
  const models = (await modelsResponse.json()) as {
    defaultAlias: string;
    items: Array<{ alias: string; displayName: string }>;
  };
  const model = models.items.find((item) => item.alias === models.defaultAlias);
  expect(agents.items[0]).toBeDefined();
  expect(skills.items[0]).toBeDefined();
  expect(model).toBeDefined();
  await page.getByRole("button", { name: "Add to message" }).click();
  await page.getByRole("menuitem", { name: "Use Agents" }).click();
  await page
    .getByRole("option", { name: agents.items[0]!.displayName })
    .click();
  await page.getByRole("button", { name: "Add to message" }).click();
  await page.getByRole("menuitem", { name: "Use Skills" }).click();
  await page
    .getByRole("option", { name: skills.items[0]!.displayName })
    .click();
  await page
    .getByRole("button", {
      name: `Select model, current model ${model!.displayName}`,
    })
    .click();
  await page.getByRole("menuitemradio", { name: model!.displayName }).click();

  await page
    .getByRole("textbox", { name: "Message Tapper" })
    .fill(
      "What is the relationship between underwriting review and health disclosure?",
    );
  await page.getByRole("button", { name: "Send" }).click();

  const relationCitation = page
    .getByRole("button", {
      name: /Open relation citation R1|打开关系引用 R1/u,
    })
    .first();
  await expect(relationCitation).toBeVisible({ timeout: 45_000 });
  await relationCitation.click();
  await expect(
    page.getByRole("heading", { name: /Relation evidence|关系依据/u }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /View in Library|在 Library 中查看/u })
    .click();
  await expect(
    page.getByRole("tab", { name: /Knowledge Graph|知识图谱/u }),
  ).toHaveAttribute("aria-selected", "true");
  const highlightedPath = page.getByRole("region", {
    name: /Highlighted path|高亮路径/u,
  });
  await expect(highlightedPath).toBeVisible();
  await expect(highlightedPath).toContainText(/underwriting review/iu);
  await expect(highlightedPath).toContainText(/health disclosure/iu);

  const unavailable = async (route: Route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/problem+json",
      body: "{}",
    });
  };
  const graphProjectPattern = /\/knowledge\/graph\/project(?:\?|$)/u;
  try {
    await page.route(graphProjectPattern, unavailable);
    await page.goto("/");
    await page.getByRole("button", { name: /Library|知识库/u }).click();
    await page.getByRole("tab", { name: /Knowledge Graph|知识图谱/u }).click();
    await expect(page.getByRole("alert")).toContainText(
      /The knowledge graph is temporarily unavailable|知识图谱暂时无法加载/u,
    );
  } finally {
    await page.unroute(graphProjectPattern, unavailable);
    for (const sourceId of [sourceIdA, sourceIdB]) {
      const deleted = await page.request.delete(
        `${root}/knowledge/sources/${sourceId}`,
        {
          headers: {
            Origin: ORIGIN,
            "Idempotency-Key": `graph-cleanup-${sourceId}`,
          },
        },
      );
      expect(deleted.status()).toBe(204);
    }
    for (const sourceId of [sourceIdA, sourceIdB]) {
      await expect
        .poll(
          async () =>
            (
              await page.request.get(`${root}/knowledge/sources/${sourceId}`)
            ).status(),
          { timeout: 45_000 },
        )
        .toBe(404);
    }
  }
});
