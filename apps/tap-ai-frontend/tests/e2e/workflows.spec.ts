import { expect, test } from "@playwright/test";

test("workflow details remain user controlled on desktop and mobile", async ({
  page,
}) => {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/logs"))
      await route.fulfill({ json: { items: [], nextCursor: null } });
    else if (path.endsWith("/nodes/sdet"))
      await route.fulfill({
        json: {
          nodeId: "sdet",
          parentNodeId: "root",
          agentId: "sdet",
          agentRevisionId: "rev-sdet",
          displayName: "SDET",
          kind: "agent",
          status: "succeeded",
          attempt: 1,
          planRevision: 2,
          sourceRuntime: "native",
          publicSummary: "Mapped the main paths.",
          artifacts: [],
          attempts: [
            {
              attempt: 1,
              status: "succeeded",
              createdAt: "2026-11-06T08:00:00Z",
            },
          ],
        },
      });
    else throw new Error(`Unexpected workflow fixture request: ${path}`);
  });
  await page.goto("/tests/e2e/workflow-harness.html");
  await expect(
    page.getByRole("complementary", { name: "Workflow details" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "View execution details" }).click();
  await expect(
    page.getByRole("heading", { name: "Task progress" }),
  ).toBeVisible();
  await expect(page.locator("[data-workflow-edge]")).toHaveCount(2);
  await page.screenshot({
    path: "test-results/workflows-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: /SDET Agent/u }).click();
  await expect(
    page.getByRole("button", { name: "Back to workflow" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close workflow details" }).click();
  await expect(
    page.getByRole("complementary", { name: "Workflow details" }),
  ).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "View execution details" }).click();
  await expect(
    page.getByRole("heading", { name: "Node details" }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/workflows-mobile.png",
    fullPage: true,
  });
});
