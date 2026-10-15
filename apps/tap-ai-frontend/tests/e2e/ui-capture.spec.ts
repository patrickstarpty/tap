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
    expect(route.request().method()).toBe("GET");
    await route.fulfill({ json: CAPTURE_KNOWLEDGE_SOURCES });
  } else if (path.endsWith("/knowledge/published-sources")) {
    expect(route.request().method()).toBe("GET");
    await route.fulfill({ json: CAPTURE_PUBLISHED_SOURCES });
  } else if (path.endsWith("/knowledge/graph/project")) {
    expect(route.request().method()).toBe("GET");
    await route.fulfill({ json: CAPTURE_GRAPH_PROJECT });
  } else if (path.endsWith("/knowledge/graph/overview")) {
    expect(route.request().method()).toBe("GET");
    await route.fulfill({ json: CAPTURE_GRAPH_OVERVIEW });
  } else if (/\/knowledge\/graph\/nodes\/node-disclosure$/u.test(path)) {
    // Scoped to the one node this fixture actually backs -- any other node
    // id falls through to the "Unexpected capture API request" throw below,
    // so a test that accidentally selects the wrong node fails loudly
    // instead of silently rendering "Health disclosure" detail for it.
    expect(route.request().method()).toBe("GET");
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

/**
 * The force-layout worker (`features/graph/model/layout.ts`) keeps refining
 * node positions asynchronously after the canvas first renders (seeded
 * fallback positions, then the worker's reply) -- there is no DOM
 * signal/data-attribute exposed for "layout settled" (confirmed by reading
 * `KnowledgeGraph.tsx`/`layout.ts`; the capture harness deliberately does
 * not add a test-only hook to product code). This polls each `.tap-graph-node-core` circle's `cx`/`cy` and
 * waits until two consecutive samples agree, which is a reasonable
 * test-only proxy for "the worker has stopped moving things" without
 * touching `GraphOverview`/`KnowledgeGraph`.
 */
const GRAPH_LAYOUT_SETTLE_STREAK = 3;

async function waitForGraphLayoutSettled(page: Page): Promise<void> {
  // Reset both the previous-sample snapshot and the equal-streak counter at
  // the start of *every* call -- a leftover snapshot from an earlier call
  // (e.g. the overview capture) could otherwise spuriously count as an
  // immediate "match" against the very first sample taken for a different
  // canvas state (after a node selection or a highlight-fit zoom), ending
  // the wait before the new state has actually settled.
  await page.evaluate(() => {
    delete (window as unknown as { __tapLayoutSnapshot?: string })
      .__tapLayoutSnapshot;
    delete (window as unknown as { __tapLayoutSettleStreak?: number })
      .__tapLayoutSettleStreak;
  });
  await page.waitForFunction(
    (requiredStreak) => {
      const circles = document.querySelectorAll(
        ".tap-graph-node-core",
      ) as NodeListOf<SVGCircleElement>;
      if (circles.length === 0) return false;
      const snapshot = Array.from(circles)
        .map(
          (circle) =>
            `${circle.getAttribute("cx")}:${circle.getAttribute("cy")}`,
        )
        .join(",");
      const w = window as unknown as {
        __tapLayoutSnapshot?: string;
        __tapLayoutSettleStreak?: number;
      };
      const streak =
        w.__tapLayoutSnapshot === snapshot
          ? (w.__tapLayoutSettleStreak ?? 0) + 1
          : 0;
      w.__tapLayoutSnapshot = snapshot;
      w.__tapLayoutSettleStreak = streak;
      return streak >= requiredStreak;
    },
    GRAPH_LAYOUT_SETTLE_STREAK,
    { timeout: 5_000, polling: 100 },
  );
}

/**
 * Share of the smaller box's area two labels may overlap when the node label
 * involved is drawn over a halo (the canvas only does that when no free spot
 * exists around the node).
 */
const HALO_OVERLAP_TOLERANCE = 0.1;

/**
 * Asserts, from the browser's real text geometry, that every drawn node has
 * a label; that a node in view has its label in view too (neither cut off
 * by the canvas edge nor under the floating toolbar), and a node out of
 * view its label out of view; that node labels
 * overlap neither each other nor the visible edge labels -- except
 * slightly, over a halo -- and that edge labels do not overlap each other.
 * Labels are re-placed shortly after the view settles, so this polls.
 */
async function expectNodeLabelsReadable(page: Page): Promise<void> {
  const measure = () =>
    page.evaluate((tolerance) => {
      type Box = { left: number; right: number; top: number; bottom: number };
      const area = (box: Box) =>
        (box.right - box.left) * (box.bottom - box.top);
      const overlap = (a: Box, b: Box) => {
        const width = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const height = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        return width > 0 && height > 0 ? width * height : 0;
      };
      const nodes = [...document.querySelectorAll(".tap-graph-node")];
      const nodeLabels = nodes.flatMap((node) => {
        const group = node.querySelector(".tap-graph-node-label-group");
        const text = group?.querySelector("text");
        if (!group || !text) return [];
        return [
          {
            name: text.textContent ?? "",
            halo: group.getAttribute("data-halo") === "true",
            box: text.getBoundingClientRect() as Box,
          },
        ];
      });
      const edgeLabels = [
        ...document.querySelectorAll(".tap-graph-edge-label"),
      ].map((group) => ({
        name: group.querySelector("text")?.textContent ?? "",
        halo: false,
        box: group.querySelector("rect")!.getBoundingClientRect() as Box,
      }));
      const problems: string[] = [];
      const halos: string[] = [];
      // A node fully in view (inside the canvas, clear of the toolbar)
      // keeps its label in view, at any zoom and pan.
      const canvas = document
        .querySelector(".tap-graph-canvas")!
        .getBoundingClientRect();
      const toolbar = document
        .querySelector(".tap-graph-toolbar")!
        .getBoundingClientRect();
      const inside = (box: Box, slack = 1) =>
        box.left >= canvas.left - slack &&
        box.right <= canvas.right + slack &&
        box.top >= canvas.top - slack &&
        box.bottom <= canvas.bottom + slack;
      nodes.forEach((node) => {
        const core = node.querySelector(".tap-graph-node-core");
        const text = node.querySelector(".tap-graph-node-label");
        if (!core || !text) return;
        const coreBox = core.getBoundingClientRect() as Box;
        // A node wholly out of view keeps its label out of view too, so no
        // stray label shows without its node.
        if (overlap(coreBox, canvas) === 0) {
          if (overlap(text.getBoundingClientRect() as Box, canvas) > 0) {
            problems.push(`${text.textContent ?? ""} shows without its node`);
          }
          return;
        }
        if (!inside(coreBox, 0) || overlap(coreBox, toolbar) > 0) return;
        const textBox = text.getBoundingClientRect() as Box;
        if (!inside(textBox)) {
          problems.push(`${text.textContent ?? ""} is cut off by the canvas`);
        }
        if (overlap(textBox, toolbar) > 1) {
          problems.push(`${text.textContent ?? ""} is under the toolbar`);
        }
      });
      // Edge labels never overlap each other (beyond rounding where two
      // pills touch): one with no free spot is hidden instead.
      edgeLabels.forEach((a, i) => {
        for (const b of edgeLabels.slice(i + 1)) {
          if (overlap(a.box, b.box) > 1) {
            problems.push(`edge labels ${a.name} / ${b.name} overlap`);
          }
        }
      });
      nodeLabels.forEach((a, i) => {
        for (const b of [...nodeLabels.slice(i + 1), ...edgeLabels]) {
          const shared = overlap(a.box, b.box);
          if (shared === 0) continue;
          const share = shared / Math.min(area(a.box), area(b.box));
          const pair = `${a.name} / ${b.name} (${(share * 100).toFixed(1)}%)`;
          if ((a.halo || b.halo) && share < tolerance) halos.push(pair);
          else problems.push(pair);
        }
      });
      return {
        nodes: nodes.length,
        labeled: nodeLabels.length,
        problems,
        halos,
      };
    }, HALO_OVERLAP_TOLERANCE);
  await expect
    .poll(async () => (await measure()).problems, { timeout: 3_000 })
    .toEqual([]);
  const report = await measure();
  expect(report.nodes).toBeGreaterThan(0);
  expect(report.labeled).toBe(report.nodes);
  expect(report.problems).toEqual([]);
  await test.info().attach("label-overlaps-over-halo", {
    body: JSON.stringify(report.halos),
    contentType: "application/json",
  });
}

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
  await waitForGraphLayoutSettled(page);
  await expectNodeLabelsReadable(page);
  const graph = await capture(page, "05-knowledge-graph");
  await page.getByRole("tab", { name: "Documents", exact: true }).click();
  await expect(page.getByRole("tabpanel", { name: "Documents" })).toBeVisible();
  expect(await capture(page, "06-documents")).not.toBe(graph);
});

// Regression coverage for real-model revalidation report 2 (§9 Important
// 1/§5): with a project carrying many topic groups, the community sidebar
// -- unbounded, in the same CSS grid row as the canvas with
// `align-items: stretch` -- used to stretch the whole `.tap-graph-workspace`
// row (and so the canvas) well past the viewport, leaving the first screen's
// canvas empty and the graph itself scrolled out of view below it
// (`TapperWorkspace.css`'s `.tap-graph-workspace`/`.tap-graph-communities`).
// This project/overview pair is deliberately local to this test (not added
// to the shared `graphCaptureFixture`, which every other capture in this
// file also renders against) -- 24 communities is enough to reproduce the
// stretch on a 720px-tall viewport without perturbing any existing capture.
function buildManyCommunitiesProject() {
  const communities = Array.from({ length: 24 }, (_, index) => ({
    communityId: `community-${String(index)}`,
    label: `Topic group ${String(index)}`,
    size: 4,
  }));
  return {
    graphVersion: 1,
    status: "READY",
    nodeCount: communities.length * 4,
    edgeCount: communities.length * 3,
    mergedAt: "2026-10-05T08:00:00Z",
    communities,
    extractingRevisionIds: [],
    partialRevisionIds: [],
  };
}

function buildManyCommunitiesOverview() {
  const nodes: {
    nodeId: string;
    nodeType: string;
    label: string;
    canonicalKey: string;
    degree: number;
    communityId: string;
    aliases: string[];
  }[] = [];
  const edges: {
    edgeId: string;
    sourceNodeId: string;
    targetNodeId: string;
    relationType: string;
    relationLabel: string;
    confidence: number;
    origin: string;
  }[] = [];
  for (let community = 0; community < 24; community += 1) {
    for (let member = 0; member < 4; member += 1) {
      const nodeId = `node-${String(community)}-${String(member)}`;
      nodes.push({
        nodeId,
        nodeType: "CONCEPT",
        label: `Node ${String(community)}-${String(member)}`,
        canonicalKey: nodeId,
        degree: 2,
        communityId: `community-${String(community)}`,
        aliases: [],
      });
      if (member > 0) {
        edges.push({
          edgeId: `edge-${String(community)}-${String(member)}`,
          sourceNodeId: `node-${String(community)}-${String(member - 1)}`,
          targetNodeId: nodeId,
          relationType: "RELATED_TO",
          relationLabel: "related to",
          confidence: 0.9,
          origin: "EXTRACTED",
        });
      }
    }
  }
  return { graphVersion: 1, nodes, edges };
}

test("keeps the graph canvas inside the first screen when a project has many topic groups", async ({
  page,
}) => {
  const manyCommunitiesProject = buildManyCommunitiesProject();
  const manyCommunitiesOverview = buildManyCommunitiesOverview();
  // Registered after `beforeEach`'s catch-all route, so Playwright matches
  // these two handlers first and everything else (sources, highlight, …)
  // still falls through to the shared fixture.
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/knowledge/graph/project")) {
      await route.fulfill({ json: manyCommunitiesProject });
    } else if (path.endsWith("/knowledge/graph/overview")) {
      await route.fulfill({ json: manyCommunitiesOverview });
    } else {
      await route.fallback();
    }
  });

  await page.getByRole("button", { name: "Library", exact: true }).click();
  await page.getByRole("tab", { name: "Knowledge Graph" }).click();
  await expect(
    page.getByRole("checkbox", { name: "Topic group 0 · 4 nodes" }),
  ).toBeVisible();
  await waitForGraphLayoutSettled(page);

  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();
  const canvasBox = await page.locator(".tap-graph-canvas").boundingBox();
  expect(canvasBox).not.toBeNull();
  // The canvas itself never grows past the workspace's bounded row height,
  // so its node circles stay inside the viewport without scrolling.
  expect(canvasBox!.y + canvasBox!.height).toBeLessThanOrEqual(
    viewport!.height,
  );
  const nodeCore = page.locator(".tap-graph-node-core").first();
  await expect(nodeCore).toBeVisible();
  const nodeBox = await nodeCore.boundingBox();
  expect(nodeBox).not.toBeNull();
  expect(nodeBox!.y).toBeGreaterThanOrEqual(0);
  expect(nodeBox!.y).toBeLessThanOrEqual(viewport!.height);

  // The sidebar's own overflow scrolls internally instead of stretching the
  // row -- its scrollHeight exceeds its own clientHeight once there are more
  // topic groups than fit, confirming the list didn't just get hidden.
  const communitiesScrollable = await page
    .locator(".tap-graph-communities")
    .evaluate((el) => el.scrollHeight > el.clientHeight);
  expect(communitiesScrollable).toBe(true);
});

// Narrow-viewport regression for the fix above: below 821px,
// `.tap-graph-inspector` becomes an in-flow `grid-row: 3` under the canvas
// (`TapperWorkspace.css`'s `max-width: 820px` rule), not an overlay — so
// capping `.tap-graph-workspace`'s height there (as the ≥821px-only fix
// does not) would leave too little room for the canvas, the caption and the
// stacked inspector together once a node's detail panel is open. This must
// keep the pre-fix `min-height` (grow-to-fit) behaviour at 800x900.
test("keeps the graph canvas and detail panel readable on a narrow viewport with the detail panel open", async ({
  page,
}) => {
  await page.setViewportSize({ width: 800, height: 900 });
  await page.getByRole("button", { name: "Library", exact: true }).click();
  await page.getByRole("tab", { name: "Knowledge Graph" }).click();
  // Below 820px the community column starts collapsed (`KnowledgeGraph.tsx`
  // seeds `communitiesOpen` from the same breakpoint) -- wait on the canvas
  // itself, not the (hidden) community checkboxes.
  await expect(page.locator(".tap-graph-canvas")).toBeVisible();
  await waitForGraphLayoutSettled(page);

  await page.getByRole("button", { name: /Health disclosure/u }).click();
  const nodeDetails = page.getByRole("region", { name: "Node details" });
  await expect(nodeDetails).toContainText("Aliases");

  // The canvas keeps its unsqueezed (CSS min-height) size at this viewport
  // both before and after the fix -- it is the *inspector* and the
  // canvas/inspector overlap below that actually regressed, not the canvas
  // itself.
  const canvasBox = await page.locator(".tap-graph-canvas").boundingBox();
  expect(canvasBox).not.toBeNull();
  expect(canvasBox!.height).toBeGreaterThanOrEqual(400);

  // The canvas and the stacked detail panel occupy disjoint vertical space
  // -- the canvas does not overflow onto the panel below it.
  const inspectorBox = await page.locator(".tap-graph-inspector").boundingBox();
  expect(inspectorBox).not.toBeNull();
  expect(canvasBox!.y + canvasBox!.height).toBeLessThanOrEqual(
    inspectorBox!.y + 1,
  );

  // The overview caption below the canvas is present and not clipped to
  // zero height by an over-eager container cap.
  const caption = page.locator("#tap-library-graph-caption");
  await expect(caption).toBeVisible();
  const captionBox = await caption.boundingBox();
  expect(captionBox).not.toBeNull();
  expect(captionBox!.height).toBeGreaterThan(0);

  // The detail panel itself has real room -- not squeezed down to a sliver
  // by a height cap meant for the ≥821px overlay layout.
  expect(inspectorBox!.height).toBeGreaterThanOrEqual(400);
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
  await waitForGraphLayoutSettled(page);
  await expectNodeLabelsReadable(page);
  const overview = await capture(page, "10-graph-overview");

  // Step 2: node detail for "Health disclosure" (aliases, sources, grouped
  // relations).
  await page.getByRole("button", { name: /Health disclosure/u }).click();
  const nodeDetails = page.getByRole("region", { name: "Node details" });
  await expect(nodeDetails).toContainText("Aliases");
  await waitForGraphLayoutSettled(page);
  await expectNodeLabelsReadable(page);
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
      /\/conversations\/conversation-relation\/turns\/turn-relation\/citations\/(e1|e5)$/u.test(
        path,
      )
    ) {
      const citationId = path.slice(path.lastIndexOf("/") + 1) as "e1" | "e5";
      body = CAPTURE_RELATION_CONVERSATION.citations[citationId];
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
  // highlighted-path state. The turn cites both `e1` (claim 1) and `e5`
  // (claim 2), so "View in Library" must highlight both -- asserted against
  // the actual `POST .../highlight` request body, not just the rendered
  // text list.
  await edgeChip.click();
  await expect(
    page.getByRole("heading", { name: "Relation evidence" }),
  ).toBeVisible();
  const highlightRequest = page.waitForRequest(
    (request) =>
      request.url().includes("/knowledge/graph/highlight") &&
      request.method() === "POST",
  );
  await page.getByRole("button", { name: "View in Library" }).click();
  expect((await highlightRequest).postDataJSON()).toEqual({
    edgeIds: ["e1", "e5"],
  });
  await expect(
    page.getByRole("region", { name: "Highlighted path" }),
  ).toBeVisible();
  await waitForGraphLayoutSettled(page);
  await expectNodeLabelsReadable(page);
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
