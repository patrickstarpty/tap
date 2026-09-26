import { expect, test } from "@playwright/test";
import path from "node:path";

const phase = process.env.TAP_INSIGHTS_E2E_PHASE ?? "upload";
const repoRoot = process.env.TAP_REPO_ROOT ?? path.resolve(process.cwd(), "../..");
const authState = process.env.TAP_INSIGHTS_E2E_AUTH_STATE ??
  path.join(repoRoot, ".superpowers", "artifacts", "task-12", "auth-state.json");
const screenshots = process.env.TAP_INSIGHTS_E2E_SCREENSHOTS ??
  path.join(repoRoot, ".superpowers", "artifacts", "task-12");

test.use({ storageState: phase === "upload" ? undefined : authState });

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.__TAP_INSIGHTS_ACCESS_TOKEN__ = "task12-e2e-access-token";
    window.__TAP_PROJECT_ID__ = "project-a";
  });
});

async function expectProjectedReport(page: import("@playwright/test").Page) {
  await expect(page.getByRole("heading", { name: "Test Insights" })).toBeVisible();
  await expect(page.getByLabel("Authorized metrics").getByText("0.00%", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Authorized metrics").getByText("100.00%", { exact: true })).toHaveCount(3);
  await expect(page.getByLabel("Authorized metrics").getByText("1.70 s", { exact: true })).toBeVisible();
  await expect(page.getByRole("row", { name: /RUN-1042/ })).toContainText("BUILD-1042 / main");
  await page.getByRole("button", { name: "Open RUN-1042" }).click();
  const details = page.getByLabel("Run RUN-1042 details");
  await expect(details).toContainText("standard-us");
  await expect(details.getByText("Step details were not provided by this report.")).toHaveCount(2);
  await expect(details.getByText("No screenshot was attached to this attempt.")).toHaveCount(2);
  await expect(details.getByRole("button", { name: "Download raw report" })).toHaveCount(2);
}

test("real JUnit changes authorized cards and details and survives owned restarts", async ({
  browser,
  context,
  page,
}) => {
  let queryPosts = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/insights/queries"))
      queryPosts += 1;
  });
  await page.goto("/?module=test-analytics");

  if (phase === "upload") {
    await expect(page.getByText("No runs match this authorized query scope.")).toBeVisible();
    await page.getByText("Upload JUnit report").click();
    await page.getByLabel("Source").fill("github-actions");
    await page.getByLabel("Run").fill("RUN-1042");
    await page.getByLabel("Report build").fill("BUILD-1042");
    await page.getByLabel("Report branch").fill("main");
    await page.getByLabel("Batch").fill("batch-1");
    await page.getByLabel("Shard", { exact: true }).fill("1");
    await page.getByLabel("Application commit").fill("app-1042");
    await page.getByLabel("Script commit").fill("script-1042");
    await page.getByLabel("Report environment").fill("qa");
    await page.getByLabel("Configuration").fill("browser-chromium");
    await page.getByLabel("Started at").fill(new Date().toISOString().slice(0, 16));
    await page.getByLabel("JUnit XML report").setInputFiles(
      path.join(repoRoot, "apps", "backend", "tests", "fixtures", "insights", "reports", "retry.xml"),
    );
    await page.getByRole("button", { name: "Upload report" }).click();
    await expect(page.getByText("Ready for Insights")).toBeVisible({ timeout: 30_000 });
    await expectProjectedReport(page);
    await page.screenshot({ path: path.join(screenshots, "after-desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(screenshots, "after-mobile-390.png"), fullPage: true });
    const prototypeContext = await browser.newContext({
      viewport: { width: 1280, height: 720 },
      deviceScaleFactor: 2,
    });
    const prototypePage = await prototypeContext.newPage();
    await prototypePage.goto("/prototype");
    await prototypePage.getByRole("button", { name: "Test Analytics", exact: true }).click();
    await prototypePage.screenshot({
      path: path.join(screenshots, "after-prototype-1280x720@2x.png"),
    });
    await prototypeContext.close();
    await context.storageState({ path: authState });
    return;
  }

  await expect(page.getByText("Ready for Insights")).toBeVisible();
  await expectProjectedReport(page);
  expect(queryPosts).toBe(0);
});
