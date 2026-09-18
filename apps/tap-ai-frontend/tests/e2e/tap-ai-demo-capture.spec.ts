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
      /\/(ai\/(agents|skills)|conversations|knowledge\/sources)$/u.test(path)
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
