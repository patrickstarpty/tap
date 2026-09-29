import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

const captureSet = process.env.TAP_PROTOTYPE_CAPTURE_SET ?? "before";
const captureDir =
  process.env.TAP_PROTOTYPE_CAPTURE_DIR ??
  path.resolve(process.cwd(), "../../docs/assets/prototype-states");

async function capture(page: Page, name: string) {
  await page.screenshot({
    path: path.join(captureDir, captureSet, `${name}.png`),
    animations: "disabled",
  });
}

async function openWithFaults(
  page: Page,
  target: string,
  faults: readonly string[],
) {
  await page.addInitScript((injectedFaults) => {
    (window as unknown as { __TAP_PROTOTYPE_FAULTS__?: readonly string[] }).__TAP_PROTOTYPE_FAULTS__ =
      injectedFaults;
    localStorage.clear();
  }, faults);
  await page.goto(target);
}

async function openFresh(page: Page, target: string) {
  await page.addInitScript(() => localStorage.clear());
  await page.goto(target);
}

test.describe("prototype states: before/after", () => {
  test.skip(
    captureSet !== "before" && captureSet !== "after",
    `capture set is ${captureSet}, skipping before/after suite`,
  );

  test("tapper-new-chat", async ({ page }) => {
    await openFresh(page, "/prototype");
    await expect(
      page.getByRole("button", { name: "New chat" }),
    ).toBeVisible();
    await capture(page, "tapper-new-chat");
  });

  test("tapper-answer", async ({ page }) => {
    await openFresh(page, "/prototype");
    await page
      .getByRole("checkbox", { name: /Life underwriting guide · v1\.2\.md/ })
      .click();
    await page
      .getByPlaceholder("Ask about life insurance or testing...")
      .fill("Summarize the health disclosure rules");
    await page.getByRole("button", { name: "Send" }).click();
    await expect(
      page.getByText("Block submission when health disclosure is missing", {
        exact: false,
      }),
    ).toBeVisible({ timeout: 5_000 });
    await capture(page, "tapper-answer");
  });

  test("library-list", async ({ page }) => {
    await openFresh(page, "/prototype?module=library");
    await page.getByRole("tab", { name: "Documents" }).click();
    await expect(
      page.getByRole("tab", { name: "Documents" }),
    ).toHaveAttribute("aria-selected", "true");
    await capture(page, "library-list");
  });

  test("library-graph", async ({ page }) => {
    await openFresh(page, "/prototype?module=library");
    await expect(
      page.getByRole("tab", { name: "Knowledge Graph" }),
    ).toHaveAttribute("aria-selected", "true");
    await capture(page, "library-graph");
  });

  test("agents", async ({ page }) => {
    await openFresh(page, "/prototype?module=agents");
    await expect(page.getByRole("heading", { name: "Agents" })).toBeVisible();
    await capture(page, "agents");
  });

  test("skills", async ({ page }) => {
    await openFresh(page, "/prototype?module=skills");
    await expect(page.getByRole("heading", { name: "Skills" })).toBeVisible();
    await capture(page, "skills");
  });

  test("test-management", async ({ page }) => {
    await openFresh(page, "/prototype?module=test-management");
    await expect(
      page.getByRole("heading", { name: "Test Management" }),
    ).toBeVisible();
    await capture(page, "test-management");
  });

  test("low-code", async ({ page }) => {
    await openFresh(page, "/prototype?module=low-code");
    await expect(
      page.getByRole("heading", { name: "Low Code Automation" }),
    ).toBeVisible();
    await capture(page, "low-code");
  });

  test("test-insights", async ({ page }) => {
    await openFresh(page, "/prototype?module=test-insights");
    await expect(
      page.getByRole("heading", { name: "Test Insights" }),
    ).toBeVisible();
    await capture(page, "test-insights");
  });
});

// Referenced by Task 12/13, which append a `states` capture set gated the
// same way. Keeping the helper here (unused for now) avoids duplicating the
// fault-injection bootstrap when that set is added.
void openWithFaults;
