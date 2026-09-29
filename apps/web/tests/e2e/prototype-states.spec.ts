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

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function selectSource(page: Page, name: string) {
  await page
    .getByRole("checkbox", { name: new RegExp(escapeRegExp(name)) })
    .click();
}

async function ask(page: Page, question: string) {
  await page
    .getByPlaceholder("Ask about life insurance or testing...")
    .fill(question);
  await page.getByRole("button", { name: "Send" }).click();
}

// Sample source ids that are not part of the `useDocumentReview` document
// list (SAMPLE_FILES and SAMPLE_REPRESENTATIVE_SOURCES). Removing all of
// them, plus the review documents, empties the merged source list used by
// the composer's sources panel and the Library workspace.
const NON_REVIEW_SOURCE_IDS = [
  "sample-test-cases",
  "sample-underwriting",
  "sample-beneficiary",
  "sample-exploratory",
  "sample-log",
  "sample-slides",
  "sample-csv",
  "sample-json",
  "sample-yaml",
  "sample-xml",
  "sample-html",
  "sample-rtf",
  "sample-odt",
  "sample-doc",
  "sample-ods",
  "sample-xls",
  "sample-odp",
  "sample-ppt",
  "sample-product-savings-01",
  "sample-nb-issue",
  "sample-ps-beneficiary",
  "sample-cl-medical",
  "sample-system-policy",
  "sample-code-beneficiary",
  "sample-test-beneficiary-retry",
  "sample-automation-beneficiary",
  "sample-run-servicing",
  "sample-defect-duplicate",
];

const REVIEW_SOURCE_IDS = [
  "underwriting-v12",
  "underwriting-scan",
  "underwriting-evidence-pdf",
  "premium-rates-xlsx",
  "approval-flow-complex",
  "health-disclosure-approved",
];

function baseSnapshot(removedSourceIds: readonly string[]) {
  return {
    version: 2,
    activeConversationId: "chat-1",
    conversations: [
      {
        id: "chat-1",
        title: "New chat",
        turns: [],
        modelId: "gpt-5.6-sol",
        selectedSourceIds: [],
        selectedAgentIds: [],
        selectedSkillIds: [],
      },
    ],
    artifacts: { automations: [], testPlans: [], runs: [] },
    library: {
      open: false,
      examplesLoaded: true,
      sampleLoaded: true,
      localSources: [],
      removedSourceIds,
    },
  };
}

async function openWithSnapshot(
  page: Page,
  target: string,
  snapshot: Record<string, unknown>,
) {
  await page.addInitScript((serialized) => {
    localStorage.clear();
    localStorage.setItem("tap.prototype.workspace.v2", serialized);
  }, JSON.stringify(snapshot));
  await page.goto(target);
}

test.describe("prototype states: A-D interaction states", () => {
  test.skip(
    captureSet !== "states",
    `capture set is ${captureSet}, skipping states suite`,
  );

  // A: answer evidence and citations

  test("a1-answer-trace", async ({ page }) => {
    await openFresh(page, "/prototype");
    await selectSource(page, "Life underwriting guide · v1.2.md");
    await ask(page, "Summarize the health disclosure rules");
    const traceButton = page.getByRole("button", { name: /Searched/ });
    await expect(traceButton).toBeVisible({ timeout: 4_000 });
    await traceButton.click();
    await expect(page.getByText("Search", { exact: true })).toBeVisible();
    await capture(page, "a1-answer-trace");
  });

  test("a2-context-used", async ({ page }) => {
    await openFresh(page, "/prototype");
    await selectSource(page, "Life underwriting guide · v1.2.md");
    await ask(page, "Summarize the health disclosure rules");
    const contextButton = page.getByRole("button", {
      name: "Sources and configuration used",
    });
    await expect(contextButton).toBeVisible({ timeout: 4_000 });
    await contextButton.click();
    await expect(
      page
        .locator(".tap-answer-context")
        .getByText("Life underwriting guide · v1.2.md", { exact: true }),
    ).toBeVisible();
    await capture(page, "a2-context-used");
  });

  test("a3-citation-panel", async ({ page }) => {
    await openFresh(page, "/prototype");
    await selectSource(page, "Life underwriting guide · v1.2.md");
    await ask(page, "Summarize the health disclosure rules");
    const citation = page.getByRole("button", {
      name: "[1] Life underwriting guide · v1.2.md",
    });
    await expect(citation).toBeVisible({ timeout: 4_000 });
    await citation.click();
    await expect(
      page.getByRole("heading", { name: "Citation [1]" }),
    ).toBeVisible();
    await capture(page, "a3-citation-panel");
  });

  test("a4-citation-stale", async ({ page }) => {
    await openFresh(page, "/prototype");
    await selectSource(page, "Life underwriting guide · v1.2.md");
    await selectSource(page, "Underwriting test rules.pdf");
    await ask(page, "Summarize the health disclosure rules");
    const citation = page.getByRole("button", {
      name: "[2] Underwriting test rules.pdf",
    });
    await expect(citation).toBeVisible({ timeout: 4_000 });
    await citation.click();
    await expect(
      page.getByText(
        "This source has been updated. The cited passage may have changed.",
      ),
    ).toBeVisible();
    await capture(page, "a4-citation-stale");
  });

  test("a5-citation-verification-failed", async ({ page }) => {
    await openWithFaults(page, "/prototype", [
      "citation-verification-failed",
    ]);
    await selectSource(page, "Life underwriting guide · v1.2.md");
    await ask(page, "Summarize the health disclosure rules");
    const citation = page.getByRole("button", {
      name: "[1] Life underwriting guide · v1.2.md",
    });
    await expect(citation).toBeVisible({ timeout: 4_000 });
    await citation.click();
    await expect(
      page.getByText("The citation could not be verified."),
    ).toBeVisible();
    await capture(page, "a5-citation-verification-failed");
  });

  test("a7-conflict", async ({ page }) => {
    await openFresh(page, "/prototype");
    await selectSource(page, "Beneficiary change workflow.docx");
    await selectSource(page, "Beneficiary test cases.xlsx");
    await ask(page, "What changes a beneficiary?");
    await expect(
      page.getByText("The two sources reach different conclusions."),
    ).toBeVisible({ timeout: 4_000 });
    await capture(page, "a7-conflict");
  });

  test("a8-source-changed", async ({ page }) => {
    await openWithFaults(page, "/prototype", ["source-version-changed"]);
    await selectSource(page, "Life underwriting guide · v1.2.md");
    await ask(page, "Summarize the health disclosure rules");
    await expect(
      page.getByText("Sources were updated while answering. Please resubmit."),
    ).toBeVisible({ timeout: 4_000 });
    await capture(page, "a8-source-changed");
  });

  test("a9-retrieval-limited", async ({ page }) => {
    await openFresh(page, "/prototype");
    await selectSource(page, "Underwriting evidence.pdf");
    await ask(page, "Summarize the health disclosure rules");
    await expect(
      page.getByText(
        "Some sources could not be searched. This answer uses the remaining sources.",
      ),
    ).toBeVisible({ timeout: 4_000 });
    await capture(page, "a9-retrieval-limited");
  });

  // B: composer, history, sources and library failures

  test("b1-queued", async ({ page }) => {
    await openFresh(page, "/prototype");
    await selectSource(page, "Life underwriting guide · v1.2.md");
    await ask(page, "Summarize the health disclosure rules");
    await expect(page.getByText("Waiting to start…")).toBeVisible({
      timeout: 2_000,
    });
    await capture(page, "b1-queued");
  });

  test("b2-send-failed", async ({ page }) => {
    await openWithFaults(page, "/prototype", ["send-failed"]);
    await page
      .getByPlaceholder("Ask about life insurance or testing...")
      .fill("Summarize the health disclosure rules");
    await page.getByRole("button", { name: "Send" }).click();
    await expect(
      page.getByText(
        "Message was not sent. Your draft is still here. Please try again.",
      ),
    ).toBeVisible();
    await capture(page, "b2-send-failed");
  });

  test("b3-stop-failed", async ({ page }) => {
    await openWithFaults(page, "/prototype", ["stop-failed"]);
    await selectSource(page, "Life underwriting guide · v1.2.md");
    await ask(page, "Summarize the health disclosure rules");
    const stopButton = page.getByRole("button", { name: "Stop", exact: true });
    await expect(stopButton).toBeVisible({ timeout: 4_000 });
    await stopButton.click();
    await expect(
      page.getByText(
        "The response may still be running. Please try again shortly.",
      ),
    ).toBeVisible();
    await capture(page, "b3-stop-failed");
  });

  test("b4-stream-interrupted", async ({ page }) => {
    await openWithFaults(page, "/prototype", ["stream-interrupted"]);
    await selectSource(page, "Life underwriting guide · v1.2.md");
    await ask(page, "Summarize the health disclosure rules");
    await expect(
      page.getByText("Conversation updates stopped. Your message is saved."),
    ).toBeVisible({ timeout: 4_000 });
    await capture(page, "b4-stream-interrupted");
  });

  test("b5-model-unavailable", async ({ page }) => {
    await openFresh(page, "/prototype");
    await page.getByRole("button", { name: /Select model/ }).click();
    await expect(page.getByRole("menu", { name: "Models" })).toBeVisible();
    await expect(page.getByText("Unavailable")).toBeVisible();
    await capture(page, "b5-model-unavailable");
  });

  test("b5-no-models", async ({ page }) => {
    await openWithFaults(page, "/prototype", ["no-models"]);
    await expect(page.getByText("No models available")).toBeVisible();
    await capture(page, "b5-no-models");
  });

  test("b7-history-load-failed", async ({ page }) => {
    await openWithFaults(page, "/prototype", ["history-load-failed"]);
    await expect(
      page.getByText("Chat history could not be loaded."),
    ).toBeVisible();
    await capture(page, "b7-history-load-failed");
  });

  test("b8-history-load-more", async ({ page }) => {
    await openFresh(page, "/prototype");
    const loadMore = page.getByRole("button", { name: "Load more" });
    await expect(loadMore).toBeVisible();
    await loadMore.scrollIntoViewIfNeeded();
    await capture(page, "b8-history-load-more");
  });

  test("b9-delete-failed", async ({ page }) => {
    await openWithFaults(page, "/prototype", ["conversation-delete-failed"]);
    await page
      .getByRole("button", { name: /More options for/ })
      .first()
      .click();
    await page.getByRole("menuitem", { name: "Delete" }).click();
    await expect(
      page.getByRole("heading", { name: "Delete chat?" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(
      page.getByText("The chat could not be deleted. Please try again."),
    ).toBeVisible();
    await capture(page, "b9-delete-failed");
  });

  test("b10-sources-processing", async ({ page }) => {
    const snapshot = baseSnapshot([
      ...NON_REVIEW_SOURCE_IDS,
      ...REVIEW_SOURCE_IDS.filter((id) => id !== "underwriting-v12"),
    ]);
    await page.addInitScript(
      ({ serializedSnapshot, documentReviews }) => {
        localStorage.clear();
        localStorage.setItem("tap.prototype.workspace.v2", serializedSnapshot);
        localStorage.setItem(
          "tap.prototype.document-reviews.v3",
          documentReviews,
        );
      },
      {
        serializedSnapshot: JSON.stringify(snapshot),
        documentReviews: JSON.stringify([
          {
            id: "underwriting-v12",
            name: "Life underwriting guide · v1.2.md",
            version: "v1.2",
            state: "processing",
            checks: [false, false, false, false],
            revision: 1,
            history: [],
          },
        ]),
      },
    );
    await page.goto("/prototype");
    await expect(
      page.getByText("Sources are processing. They can be selected when ready."),
    ).toBeVisible();
    await capture(page, "b10-sources-processing");
  });

  test("b10-sources-load-failed", async ({ page }) => {
    await openWithFaults(page, "/prototype", ["sources-load-failed"]);
    await expect(
      page.getByText("Knowledge sources could not be loaded."),
    ).toBeVisible();
    await capture(page, "b10-sources-load-failed");
  });

  test("b11-library-empty", async ({ page }) => {
    await openWithSnapshot(
      page,
      "/prototype?module=library",
      baseSnapshot([...NON_REVIEW_SOURCE_IDS, ...REVIEW_SOURCE_IDS]),
    );
    await expect(page.getByText("No knowledge sources yet")).toBeVisible();
    await capture(page, "b11-library-empty");
  });

  test("b11-library-load-failed", async ({ page }) => {
    await openWithFaults(page, "/prototype?module=library", [
      "library-load-failed",
    ]);
    await expect(
      page.getByText("Library could not be loaded."),
    ).toBeVisible();
    await capture(page, "b11-library-load-failed");
  });

  test("b12-uploading", async ({ page }) => {
    await openFresh(page, "/prototype?module=library");
    await page.getByRole("button", { name: "Add source" }).click();
    await page
      .getByLabel("Source file")
      .setInputFiles({
        name: "sample-policy.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("Sample policy text for the prototype gallery."),
      });
    await page.getByRole("button", { name: "Next" }).click();
    await page
      .getByRole("button", { name: "Add source", exact: true })
      .last()
      .click();
    await expect(page.getByText("Uploading…")).toBeVisible({
      timeout: 2_000,
    });
    await capture(page, "b12-uploading");
  });

  test("b12-upload-failed", async ({ page }) => {
    await openWithFaults(page, "/prototype?module=library", [
      "upload-failed",
    ]);
    await page.getByRole("button", { name: "Add source" }).click();
    await page
      .getByLabel("Source file")
      .setInputFiles({
        name: "sample-policy.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("Sample policy text for the prototype gallery."),
      });
    await page.getByRole("button", { name: "Next" }).click();
    await page
      .getByRole("button", { name: "Add source", exact: true })
      .last()
      .click();
    await expect(
      page.getByText("Upload failed. Please try again."),
    ).toBeVisible({ timeout: 2_000 });
    await capture(page, "b12-upload-failed");
  });

  test("b13-chunk-index-failed", async ({ page }) => {
    await openFresh(page, "/prototype?module=library");
    await page.getByRole("tab", { name: "Documents" }).click();
    await page
      .getByRole("button", { name: "Manage chunks Underwriting evidence.pdf" })
      .click();
    await expect(page.getByText("Index failed")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Retry indexing" }),
    ).toBeVisible();
    await capture(page, "b13-chunk-index-failed");
  });

  // C: Library source management

  test("c1-upload-chunk-preview", async ({ page }) => {
    await openFresh(page, "/prototype?module=library");
    await page.getByRole("button", { name: "Add source" }).click();
    await page
      .getByLabel("Source file")
      .setInputFiles({
        name: "sample-policy.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("Sample policy text for the prototype gallery."),
      });
    await page.getByRole("button", { name: "Next" }).click();
    await page.getByRole("button", { name: "Preview chunks" }).click();
    await expect(page.getByRole("list")).toBeVisible();
    await capture(page, "c1-upload-chunk-preview");
  });

  test("c3-source-detail", async ({ page }) => {
    await openFresh(page, "/prototype?module=library");
    await page.getByRole("tab", { name: "Documents" }).click();
    await page
      .getByRole("button", {
        name: "View Underwriting rules — scanned.pdf",
      })
      .click();
    await expect(
      page.getByRole("button", { name: "Retry", exact: true }),
    ).toBeVisible();
    await capture(page, "c3-source-detail");
  });

  test("c4-graph-empty", async ({ page }) => {
    await openFresh(page, "/prototype?module=library");
    await page.getByText("Published source graph").click();
    await page
      .getByRole("combobox", { name: "Source" })
      .selectOption({ label: "Exploratory testing checklist.md" });
    await expect(
      page.getByText("This source has no published graph yet."),
    ).toBeVisible();
    await capture(page, "c4-graph-empty");
  });

  test("c4-graph-load-failed", async ({ page }) => {
    await openWithFaults(page, "/prototype?module=library", [
      "graph-load-failed",
    ]);
    await expect(
      page.getByText("The knowledge graph could not be loaded."),
    ).toBeVisible();
    await capture(page, "c4-graph-load-failed");
  });

  // D: Agents and Skills

  test("d-builtin-readonly", async ({ page }) => {
    await openFresh(page, "/prototype?module=agents");
    await expect(page.getByText("Built-in").first()).toBeVisible();
    await capture(page, "d-builtin-readonly");
  });

  test("d-skill-preview", async ({ page }) => {
    await openFresh(page, "/prototype?module=skills");
    await page.getByRole("button", { name: "Create skill" }).click();
    await page
      .getByRole("textbox", { name: "Name" })
      .fill("health-disclosure-check");
    await page
      .getByRole("textbox", { name: "Description" })
      .fill("Check that health disclosure is present before submission.");
    await expect(
      page.getByLabel("SKILL.md preview").getByText("health-disclosure-check"),
    ).toBeVisible();
    await capture(page, "d-skill-preview");
  });
});

test.describe("prototype states: cross-module journeys", () => {
  test.skip(
    captureSet !== "after",
    `capture set is ${captureSet}, skipping journeys suite`,
  );

  test("floating-assistant-handoff", async ({ page }) => {
    await openFresh(page, "/prototype?module=test-management");
    await expect(
      page.getByRole("heading", { name: "Test Management" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Ask Tapper" }).click();
    const continueButton = page.getByRole("button", {
      name: "Continue in Tapper",
    });
    await expect(continueButton).toBeVisible();
    await continueButton.click();
    await expect(
      page.getByPlaceholder("Ask about life insurance or testing..."),
    ).toBeVisible();
  });

  test("tapper-test-plan-automation-link", async ({ page }) => {
    await openFresh(page, "/prototype");
    await ask(
      page,
      "Generate an automation script for a life insurance application",
    );

    const createPlanFirst = page.getByRole("button", {
      name: "Create Test Plan first",
    });
    await expect(createPlanFirst).toBeVisible({ timeout: 3_000 });
    await createPlanFirst.click();

    const generateLinked = page.getByRole("button", {
      name: "Generate linked automation",
    });
    await expect(generateLinked).toBeVisible();
    await generateLinked.click();

    const createWebAutomation = page.getByRole("button", {
      name: "Create Web automation",
    });
    await expect(createWebAutomation).toBeVisible();
    await createWebAutomation.click();

    const openTestPlanFromChat = page.getByRole("button", {
      name: "Open Test Plan",
      exact: true,
    });
    await expect(openTestPlanFromChat).toBeVisible();
    await openTestPlanFromChat.click();

    // Hop 1: Tapper -> Test Plan.
    const testPlanHeading = page.locator("#test-plan-detail-heading");
    await expect(testPlanHeading).toBeVisible();
    const testPlanId = await page
      .locator(".tap-test-plan-detail-header .tap-asset-id")
      .textContent();
    const testPlanTitle = await testPlanHeading.textContent();
    expect(testPlanId).toMatch(/^TP-/);

    // Hop 2: Test Plan -> linked Automation.
    const openAutomationButton = page.getByRole("button", {
      name: /^Open Automation AUTO-/,
    });
    await expect(openAutomationButton).toBeVisible();
    await openAutomationButton.click();

    const automationHeading = page.locator("#automation-detail-heading");
    await expect(automationHeading).toBeVisible();
    const automationId = await page
      .locator(".tap-automation-title-row .tap-asset-id")
      .textContent();
    expect(automationId).toMatch(/^AUTO-/);

    // Hop 3: Automation -> back to the same linked Test Plan.
    const openTestPlanFromAutomation = page.getByRole("button", {
      name: new RegExp(`^Open Test Plan ${testPlanId}$`),
    });
    await expect(openTestPlanFromAutomation).toBeVisible();
    await openTestPlanFromAutomation.click();

    await expect(testPlanHeading).toBeVisible();
    await expect(testPlanHeading).toHaveText(testPlanTitle ?? "");
    await expect(
      page.locator(".tap-test-plan-detail-header .tap-asset-id"),
    ).toHaveText(testPlanId ?? "");
  });
});
