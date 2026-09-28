import { expect, test } from "@playwright/test";
import path from "node:path";

const phase = process.env.TAP_INSIGHTS_E2E_PHASE ?? "upload";
const repoRoot = process.env.TAP_REPO_ROOT ?? path.resolve(process.cwd(), "../..");
const authState = process.env.TAP_INSIGHTS_E2E_AUTH_STATE ??
  path.join(repoRoot, ".superpowers", "artifacts", "task-12", "auth-state.json");
const screenshots = process.env.TAP_INSIGHTS_E2E_SCREENSHOTS ??
  path.join(repoRoot, ".superpowers", "artifacts", "task-12");
const accessToken = process.env.TAP_REPORT_ACCESS_TOKEN ?? "task12-e2e-access-token";
const customerScreenshots = process.env.TAP_CUSTOMER_DEMO_SCREENSHOTS;

async function captureCustomerStep(page: import("@playwright/test").Page, filename: string, focus?: string) {
  if (!customerScreenshots || phase !== "upload") return;
  if (focus) await page.locator(focus).first().scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(customerScreenshots, filename), animations: "disabled" });
}

test.use({ storageState: phase === "upload" ? undefined : authState });

async function openInsights(page: import("@playwright/test").Page) {
  await page.goto("/?module=test-insights");
  await page.getByRole("textbox", { name: "Project ID" }).fill("project-a");
  await page.getByLabel("Access token").fill(accessToken);
  await page.getByRole("button", { name: "Open Insights" }).click();
}

async function expectProjectedReport(page: import("@playwright/test").Page) {
  await expect(page.getByRole("heading", { name: "Test Insights" })).toBeVisible();
  await expect(page.getByLabel("Authorized metrics").getByText("0.00%", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Authorized metrics").getByText("100.00%", { exact: true })).toHaveCount(3);
  await expect(page.getByLabel("Authorized metrics").getByText("1.70 s", { exact: true })).toBeVisible();
  await expect(page.getByRole("row", { name: /RUN-1042/ })).toContainText("BUILD-1042 / main");
  await page.getByRole("button", { name: "Open RUN-1042" }).click();
  const details = page.getByLabel("Run RUN-1042 details");
  await expect(details).toContainText("standard-us");
  await expect(details.getByRole("button", { name: "View report evidence" })).toHaveCount(2);
  await expect(details.getByRole("button", { name: "Download raw report" })).toHaveCount(2);
  const tapperLink = details.getByRole("link", { name: "Open draft in Tapper" });
  await expect(tapperLink).toHaveAttribute("href", /queryId=[^&]+/u);
  await expect(tapperLink).toHaveAttribute("href", /resourceRef=[^&]+/u);
  await expect(tapperLink).not.toHaveAttribute("href", /(?:duration|numerator|denominator|value)=/u);
}

async function expectAllureReport(page: import("@playwright/test").Page) {
  await expect(page.getByLabel("Authorized metrics").getByText("66.67%", { exact: true })).toHaveCount(2);
  await page.getByRole("button", { name: "Open RUN-PYTEST-ALLURE" }).click();
  await captureCustomerStep(page, "13-run-details.png", ".ti-details");
  const details = page.getByLabel("Run RUN-PYTEST-ALLURE details");
  const failed = details.getByRole("article").filter({ hasText: "sample_business#test_identity_required" });
  await failed.getByRole("button", { name: "View report evidence" }).click();
  await expect(failed.getByText("Synthetic missing identity evidence", { exact: false }).first()).toBeVisible();
  await expect(failed.getByText("Validate identity", { exact: true })).toBeVisible();
  await failed.getByRole("button", { name: "Open Synthetic diagnostic image" }).click();
  await expect(failed.getByRole("img", { name: "Synthetic diagnostic image" })).toBeVisible();
  await expect(failed.getByText("Prepare synthetic business context", { exact: true })).toBeVisible();
  await captureCustomerStep(page, "14-failure-evidence.png", ".ti-attempts");
  await captureCustomerStep(page, "15-tapper-handoff.png", ".ti-tapper-handoff");
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
  await openInsights(page);

  if (phase === "upload") {
    await expect(page.getByText("No runs match this authorized query scope.")).toBeVisible();
    await page.getByText("Upload test report").click();
    await page.getByLabel("Report format").selectOption("junit");
    await page.getByLabel("Source").fill("github-actions");
    await page.getByLabel("Run", { exact: true }).fill("RUN-1042");
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
    await page.getByLabel("Complete attempt history").check();
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
    const insightsModule = prototypePage.getByRole("button", {
      name: "Test Insights",
      exact: true,
    });
    await insightsModule.click();
    await expect(insightsModule).toHaveAttribute("aria-current", "page");
    await prototypePage.screenshot({
      path: path.join(screenshots, "after-prototype-1280x720@2x.png"),
      animations: "disabled",
    });
    await prototypeContext.close();
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.getByRole("button", { name: "Close details" }).click();
    await page.getByLabel("Report format").selectOption("allure");
    await page.getByLabel("Source").fill("pytest-allure");
    await page.getByLabel("Run", { exact: true }).fill("RUN-PYTEST-ALLURE");
    await page.getByLabel("Report build").fill("BUILD-ALLURE");
    await page.getByLabel("Allure Results ZIP", { exact: true }).setInputFiles(path.join(repoRoot, "apps/backend/tests/fixtures/insights/pytest-allure/allure-results.zip"));
    await captureCustomerStep(page, "10-report-upload.png", ".ti-intake");
    const previousReceipt = await page.locator(".ti-receipt span").first().textContent();
    const receiptResponse = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith("/insights/reports"));
    await page.getByLabel("Complete attempt history").check();
    await page.getByRole("button", { name: "Upload report" }).click();
    expect((await receiptResponse).status()).toBe(202);
    await expect(page.locator(".ti-receipt span").first()).not.toHaveText(previousReceipt!);
    await expect(page.getByText("Ready for Insights")).toBeVisible({ timeout: 30_000 });
    await captureCustomerStep(page, "11-report-ready.png", ".ti-intake");
    await page.getByText("Upload test report").click();
    await page.getByLabel("Build", { exact: true }).fill("BUILD-ALLURE");
    await page.getByRole("button", { name: "Apply filters", exact: true }).click();
    await expect(page.getByLabel("Authorized metrics").getByText("66.67%", { exact: true })).toHaveCount(2);
    await captureCustomerStep(page, "12-report-metrics.png", ".ti-metrics");
    await expectAllureReport(page);
    await page.screenshot({ path: path.join(screenshots, "allure-evidence-desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await page.screenshot({ path: path.join(screenshots, "allure-evidence-mobile.png"), fullPage: true });
    await context.storageState({ path: authState });
    return;
  }

  await expect(page.getByText("Ready for Insights")).toBeVisible();
  await expectAllureReport(page);
  expect(queryPosts).toBe(0);
  if (phase === "projection-rebuild") {
    await page.getByRole("button", { name: "Close details" }).click();
    await page.getByRole("button", { name: "Apply filters", exact: true }).click();
    await expectAllureReport(page);
  }
  // Legacy JUnit facts must survive the same restarts and rebuild.
  await page.getByRole("button", { name: "Close details" }).click();
  await page.getByLabel("Build", { exact: true }).fill("BUILD-1042");
  await page.getByRole("button", { name: "Apply filters", exact: true }).click();
  await expectProjectedReport(page);
});
