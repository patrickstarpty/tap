import { createHash } from "node:crypto";

import { expect, test, type Page } from "@playwright/test";

// Current independently deployed AI shell, not the archived mixed-product demo.
// No live backend or provider is contacted; these are deterministic UI captures.
test.use({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 2 });

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
        defaultAlias: "tapper-chat",
        items: [
          {
            alias: "tapper-chat",
            displayName: "Capture model",
            capabilities: ["chat"],
          },
        ],
      };
    } else if (
      /\/(ai\/(agents|skills)|conversations|knowledge\/(sources|published-sources))$/u.test(
        path,
      )
    ) {
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
  const graph = await capture(page, "05-knowledge-graph");
  await page.getByRole("tab", { name: "Documents", exact: true }).click();
  await expect(page.getByRole("tabpanel", { name: "Documents" })).toBeVisible();
  expect(await capture(page, "06-documents")).not.toBe(graph);
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
        defaultAlias: "tapper-chat",
        items: [
          {
            alias: "tapper-chat",
            displayName: "Capture model",
            capabilities: ["chat"],
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
              modelAlias: "tapper-chat",
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
