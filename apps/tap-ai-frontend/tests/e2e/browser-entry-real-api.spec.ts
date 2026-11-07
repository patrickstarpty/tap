import { expect, test } from "@playwright/test";

test("browser run reads saved API result and evidence after refresh", async ({
  page,
}) => {
  expect(process.env.TAP_RUN_BROWSER_ENTRY_UI).toBe("1");
  const taskId = process.env.TAPPER_BROWSER_ENTRY_TASK_ID;
  const replayId = process.env.TAPPER_BROWSER_ENTRY_REPLAY_ID;
  expect(taskId).toMatch(/^\S+$/u);
  expect(replayId).toMatch(/^\S+$/u);
  const captureDir = process.env.TAPPER_BROWSER_ENTRY_CAPTURE_DIR;
  expect(captureDir).toBeTruthy();

  const openRun = async () => {
    await page.getByRole("button", { name: "Agents", exact: true }).click();
    await page.getByRole("tab", { name: "Runs", exact: true }).click();
    await expect(
      page
        .getByRole("region", { name: "Workflow runs" })
        .getByRole("button")
        .filter({ hasText: replayId! }),
    ).toContainText(`Replay of ${taskId}`);
    const run = page
      .getByRole("region", { name: "Workflow runs" })
      .getByRole("button")
      .filter({ has: page.getByText(`Run ${taskId}`, { exact: true }) });
    await expect(run).toBeVisible();
    await run.click();
    await expect(
      page.getByRole("heading", { name: "Task progress" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Browser result" }),
    ).toBeVisible();
    await expect(page.getByText("FOUND", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Screenshot" })).toBeVisible();
  };

  await page.goto("/");
  await openRun();
  await page.screenshot({
    path: `${captureDir}/browser-entry-desktop.png`,
    fullPage: true,
  });
  await page.reload();
  await openRun();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("heading", { name: "Browser result" }),
  ).toBeVisible();
  await page.screenshot({
    path: `${captureDir}/browser-entry-mobile.png`,
    fullPage: true,
  });
});
