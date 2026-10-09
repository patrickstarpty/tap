import { createHash } from "node:crypto";

import { expect, test, type Page, type Route } from "@playwright/test";

import {
  CAPTURE_GRAPH_HIGHLIGHT,
  CAPTURE_GRAPH_NODE,
  CAPTURE_GRAPH_OVERVIEW,
  CAPTURE_GRAPH_PROJECT,
  CAPTURE_KNOWLEDGE_SOURCES,
  CAPTURE_PUBLISHED_SOURCES,
  CAPTURE_RELATION_CONVERSATION,
} from "./graphCaptureFixture";

// Current independently deployed AI shell, not the archived mixed-product demo.
// No live backend or provider is contacted; these are deterministic UI captures.
test.use({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 2 });

/**
 * Shared knowledge/graph route fulfillment (sources, published sources, the
 * project/overview/node-detail graph endpoints, and the `POST .../highlight`
 * mutation), reused by `beforeEach`'s default route table and the edge
 * citation/highlight test's conversation-scoped reroute below — both need
 * the same graph fixture to back the Library's Knowledge Graph tab. Returns
 * `true` once it has fulfilled the route, `false` when `path` isn't one of
 * these endpoints (the caller then falls through to its own handling).
 */
async function fulfillGraphRoutes(
  route: Route,
  path: string,
): Promise<boolean> {
  if (path.endsWith("/knowledge/sources")) {
    await route.fulfill({ json: CAPTURE_KNOWLEDGE_SOURCES });
  } else if (path.endsWith("/knowledge/published-sources")) {
    await route.fulfill({ json: CAPTURE_PUBLISHED_SOURCES });
  } else if (path.endsWith("/knowledge/graph/project")) {
    await route.fulfill({ json: CAPTURE_GRAPH_PROJECT });
  } else if (path.endsWith("/knowledge/graph/overview")) {
    await route.fulfill({ json: CAPTURE_GRAPH_OVERVIEW });
  } else if (/\/knowledge\/graph\/nodes\/[^/]+$/u.test(path)) {
    await route.fulfill({ json: CAPTURE_GRAPH_NODE });
  } else if (path.endsWith("/knowledge/graph/highlight")) {
    expect(route.request().method()).toBe("POST");
    await route.fulfill({ json: CAPTURE_GRAPH_HIGHLIGHT });
  } else {
    return false;
  }
  return true;
}

// Five items (> the four-card batch size) so the "Show others" control renders,
// matching the prototype's e01-suggestions-default reference state.
const CAPTURE_PROMPT_SUGGESTIONS = [
  {
    id: "suggestion-capture-1",
    question: "What evidence is required for applicants over 60?",
    sources: [
      { sourceId: "source-capture-guide", name: "Underwriting guide.md" },
    ],
  },
  {
    id: "suggestion-capture-2",
    question: "Which fields are mandatory on the approved disclosure policy?",
    sources: [
      { sourceId: "source-capture-policy", name: "Disclosure policy.pdf" },
    ],
  },
  {
    id: "suggestion-capture-3",
    question: "How do I look up the premium rate for a 45-year-old non-smoker?",
    sources: [{ sourceId: "source-capture-rates", name: "Premium rates.xlsx" }],
  },
  {
    id: "suggestion-capture-4",
    question: "Do the test rules cover every decision boundary in the guide?",
    sources: [
      { sourceId: "source-capture-rules", name: "Underwriting test rules.pdf" },
      { sourceId: "source-capture-guide", name: "Underwriting guide.md" },
    ],
  },
  {
    id: "suggestion-capture-5",
    question: "What is the surrender value calculation basis?",
    sources: [
      { sourceId: "source-capture-surrender", name: "Surrender terms.md" },
    ],
  },
];

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown;
    if (path === "/api/v1/runtime-mode") {
      body = {
        mode: "validation",
        identityMode: "validation",
        projectId: "project-capture",
        actorId: "actor-capture",
      };
    } else if (path.endsWith("/ai/models")) {
      body = {
        defaultAlias: "qwen-plus",
        items: [
          {
            alias: "qwen-plus",
            displayName: "Capture model",
            capabilities: ["chat", "structured"],
          },
        ],
      };
    } else if (path.endsWith("/prompt-suggestions")) {
      body = { items: CAPTURE_PROMPT_SUGGESTIONS };
    } else if (await fulfillGraphRoutes(route, path)) {
      return;
    } else if (/\/(ai\/(agents|skills)|conversations)$/u.test(path)) {
      body = { items: [], nextCursor: null };
    } else {
      throw new Error(
        `Unexpected capture API request: ${route.request().method()} ${path}`,
      );
    }
    expect(route.request().method()).toBe("GET");
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await expect(
    page.getByRole("img", { name: "TAP AI", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", {
      name: /Select model, current model Capture model/u,
    }),
  ).toBeVisible();
  await expect(
    page.getByText("Loading conversations…", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Low Code Automation", exact: true }),
  ).toHaveCount(0);
});

async function capture(page: Page, name: string) {
  await page.evaluate(() => document.fonts.ready);
  const bytes = await page.screenshot({
    path: test.info().outputPath(`${name}.png`),
    animations: "disabled",
    caret: "hide",
    scale: "device",
  });
  // PNG IHDR stores width and height; reject accidental viewport/scale drift.
  expect([bytes.readUInt32BE(16), bytes.readUInt32BE(20)]).toEqual([
    2560, 1440,
  ]);
  await test.info().attach(name, { body: bytes, contentType: "image/png" });
  return createHash("sha256").update(bytes).digest("hex");
}

test("captures the fresh Tapper conversation and model selector", async ({
  page,
}) => {
  await expect(
    page.getByRole("heading", { name: "What can I do for you?" }),
  ).toBeVisible();
  const fresh = await capture(page, "01-tapper");
  await page
    .getByRole("button", { name: /Select model, current model Capture model/u })
    .click();
  await expect(page.getByRole("menu", { name: "Models" })).toBeVisible();
  expect(await capture(page, "02-model-selector")).not.toBe(fresh);
});

test("captures AI Agent and Skill catalogs", async ({ page }) => {
  await page.getByRole("button", { name: "Agents", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Agents", exact: true }),
  ).toBeVisible();
  const agents = await capture(page, "03-agents");
  await page.getByRole("button", { name: "Skills", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Skills", exact: true }),
  ).toBeVisible();
  expect(await capture(page, "04-skills")).not.toBe(agents);
});

test("captures Library graph and document views", async ({ page }) => {
  await page.getByRole("button", { name: "Library", exact: true }).click();
  await page.getByRole("tab", { name: "Knowledge Graph" }).click();
  await expect(
    page.getByRole("tab", { name: "Knowledge Graph" }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(
    page.getByRole("checkbox", { name: "Underwriting · 12 nodes" }),
  ).toBeVisible();
  const graph = await capture(page, "05-knowledge-graph");
  await page.getByRole("tab", { name: "Documents", exact: true }).click();
  await expect(page.getByRole("tabpanel", { name: "Documents" })).toBeVisible();
  expect(await capture(page, "06-documents")).not.toBe(graph);
});

test("captures the graph overview, node detail, edge citations and highlight", async ({
  page,
}) => {
  // Step 1: the community-partitioned overview canvas.
  await page.getByRole("button", { name: "Library", exact: true }).click();
  await page.getByRole("tab", { name: "Knowledge Graph" }).click();
  await expect(
    page.getByRole("checkbox", { name: "Underwriting · 12 nodes" }),
  ).toBeVisible();
  const overview = await capture(page, "10-graph-overview");

  // Step 2: node detail for "Health disclosure" (aliases, sources, grouped
  // relations).
  await page.getByRole("button", { name: /Health disclosure/u }).click();
  const nodeDetails = page.getByRole("region", { name: "Node details" });
  await expect(nodeDetails).toContainText("Aliases");
  const nodeDetail = await capture(page, "11-graph-node-detail");
  expect(nodeDetail).not.toBe(overview);

  // Step 3/4 reroute to a durable conversation whose turn renders an edge
  // citation chip, the same reroute idiom as the Insights test above.
  await page.unroute("**/api/v1/**");
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (await fulfillGraphRoutes(route, path)) return;
    let body: unknown;
    if (path === "/api/v1/runtime-mode") {
      body = {
        mode: "validation",
        identityMode: "validation",
        projectId: "project-capture",
        actorId: "actor-capture",
      };
    } else if (path.endsWith("/ai/models")) {
      body = {
        defaultAlias: "qwen-plus",
        items: [
          {
            alias: "qwen-plus",
            displayName: "Capture model",
            capabilities: ["chat", "structured"],
          },
        ],
      };
    } else if (path.endsWith("/prompt-suggestions")) {
      body = { items: CAPTURE_PROMPT_SUGGESTIONS };
    } else if (path.endsWith("/conversations")) {
      body = {
        items: [CAPTURE_RELATION_CONVERSATION.summary],
        nextCursor: null,
      };
    } else if (path.endsWith("/conversations/conversation-relation")) {
      body = CAPTURE_RELATION_CONVERSATION.detail;
    } else if (path.endsWith("/conversations/conversation-relation/events")) {
      body = CAPTURE_RELATION_CONVERSATION.events;
    } else if (
      path.endsWith(
        "/conversations/conversation-relation/turns/turn-relation/citations/e1",
      )
    ) {
      body = CAPTURE_RELATION_CONVERSATION.citation;
    } else if (/\/ai\/(agents|skills)$/u.test(path)) {
      body = { items: [], nextCursor: null };
    } else {
      throw new Error(
        `Unexpected capture API request: ${route.request().method()} ${path}`,
      );
    }
    expect(route.request().method()).toBe("GET");
    await route.fulfill({ json: body });
  });
  await page.goto("/?projectId=project-capture");
  await page
    .getByRole("button", { name: "Relation question", exact: true })
    .click();
  const edgeChip = page.getByRole("button", {
    name: "Open relation citation R1",
  });
  await expect(edgeChip).toBeVisible();
  await edgeChip.hover();
  await expect(
    page.getByText(
      "Health disclosure must be completed before underwriting review proceeds.",
    ),
  ).toBeVisible();
  const edgeCitations = await capture(page, "12-answer-edge-citations");
  expect(new Set([overview, nodeDetail, edgeCitations]).size).toBe(3);

  // Step 4: open the evidence panel and follow "View in Library" into the
  // highlighted-path state.
  await edgeChip.click();
  await expect(
    page.getByRole("heading", { name: "Relation evidence" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "View in Library" }).click();
  await expect(
    page.getByRole("region", { name: "Highlighted path" }),
  ).toBeVisible();
  const highlight = await capture(page, "13-graph-highlight");

  expect(new Set([overview, nodeDetail, edgeCitations, highlight]).size).toBe(
    4,
  );
});

test("captures a restored Insights explanation before and after refresh", async ({
  page,
}) => {
  await page.unroute("**/api/v1/**");
  let denied = false;
  const summary = {
    conversationId: "conversation-capture",
    title: "Investigate test failure",
    createdAt: "2026-09-25T08:00:00Z",
    updatedAt: "2026-09-25T08:00:00Z",
  };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown;
    if (path === "/api/v1/runtime-mode") {
      body = {
        mode: "validation",
        identityMode: "validation",
        projectId: "project-capture",
        actorId: "actor-capture",
      };
    } else if (path.endsWith("/ai/models")) {
      body = {
        defaultAlias: "qwen-plus",
        items: [
          {
            alias: "qwen-plus",
            displayName: "Capture model",
            capabilities: ["chat", "structured"],
          },
        ],
      };
    } else if (
      path.endsWith(
        "/insights/explanations/conversation-capture/turns/turn-capture",
      )
    ) {
      if (denied) {
        await route.fulfill({
          status: 403,
          json: { detail: "Authorization withdrawn" },
        });
        return;
      }
      body = {
        queryId: "query-capture",
        metricVersion: "insights-metrics-v1",
        asOf: "2026-09-25T08:00:00Z",
        facts: [
          {
            metricId: "first_pass_rate",
            numerator: 1,
            denominator: 2,
            value: 0.5,
            completeness: "complete",
            missingReasons: [],
            evidenceRefs: ["receipt-capture"],
          },
        ],
        reportCoverage: [],
        hypotheses: [
          "Request timeout may be associated with retry recovery. [receipt-capture]",
          "Permission mismatch may be associated with access failure. [source-capture]",
        ],
        evidenceExcerpts: [
          {
            citationId: "receipt-capture",
            text: "Request timed out before retry passed.",
          },
          {
            citationId: "source-capture",
            text: "Permission configuration mismatch denied access.",
          },
        ],
        missingInformation: [
          "Please provide HTTP 403 logs.",
          "Permission change history is needed.",
        ],
        stopReason: "completed",
      };
    } else if (path.endsWith("/conversations")) {
      body = { items: [summary], nextCursor: null };
    } else if (path.endsWith("/conversations/conversation-capture")) {
      body = {
        ...summary,
        turns: [
          {
            turnId: "turn-capture",
            state: "completed",
            attempt: 1,
            inputSnapshotDigest: `sha256:${"a".repeat(64)}`,
            answerEvidenceSnapshotDigest: null,
            input: {
              message: "Why did this run fail?",
              modelAlias: "qwen-plus",
              sourceRevisionIds: [],
              documentRevisionIds: [],
              resolvedResources: [],
              agentRevisionId: null,
              agentLabel: null,
              skillRevisionIds: [],
              skillLabels: [],
              insightsQueryId: "query-capture",
            },
          },
        ],
      };
    } else if (path.endsWith("/conversations/conversation-capture/events")) {
      body = { items: [], nextCursor: null };
    } else if (path.endsWith("/prompt-suggestions")) {
      body = { items: CAPTURE_PROMPT_SUGGESTIONS };
    } else if (
      /\/(ai\/(agents|skills)|knowledge\/(sources|publications))$/u.test(path)
    ) {
      body = { items: [], nextCursor: null };
    } else {
      body = { items: [], nextCursor: null };
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/?projectId=project-capture");
  const panel = page.getByRole("region", { name: "Insights explanation" });
  await expect(panel).toContainText("50% (1/2)");
  await expect(panel).toContainText("Request timeout may be associated");
  await expect(panel).toContainText("Permission mismatch may be associated");
  await expect(panel).toContainText("Please provide HTTP 403 logs.");
  const first = await capture(page, "07-insights-restored");
  await page.reload();
  await expect(panel).toContainText("Permission change history is needed.");
  expect(await capture(page, "08-insights-after-refresh")).toBe(first);
  denied = true;
  await page.getByRole("button", { name: "New chat", exact: true }).click();
  const forbidden = page.waitForResponse(
    (response) =>
      response.url().includes("/insights/explanations/") &&
      response.status() === 403,
  );
  await page
    .getByRole("button", { name: "Investigate test failure", exact: true })
    .click();
  await forbidden;
  await expect(page.getByRole("alert")).toContainText("unavailable");
  await expect(panel).toHaveCount(0);
  await expect(
    page.getByText("Request timed out before retry passed.", { exact: true }),
  ).toHaveCount(0);
  await capture(page, "09-insights-withdrawn");
});
