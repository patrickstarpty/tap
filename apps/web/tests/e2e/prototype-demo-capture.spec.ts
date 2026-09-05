import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test, type Page, type Route } from "@playwright/test";

import type { components } from "../../src/shared/api/generated/schema";

const OUTPUT_DIR = resolve(process.cwd(), "../../docs/assets/prototype-demo");

const OUTPUTS = [
  "01-tapper-new-chat.jpg",
  "02-tapper-conversation-minimap.jpg",
  "03-tapper-model-selector.jpg",
  "04-tapper-context-menu.jpg",
  "05-tapper-source-picker.jpg",
  "06-tapper-agent-picker.jpg",
  "07-tapper-skill-picker.jpg",
  "08-tapper-selected-context.jpg",
  "09-tapper-agent-catalog.jpg",
  "10-tapper-create-agent.jpg",
  "11-tapper-skill-catalog.jpg",
  "12-tapper-create-skill.jpg",
  "13-tapper-library-empty.jpg",
  "14-tapper-library-all.jpg",
  "15-tapper-library-filtered.jpg",
  "16-tapper-add-source.jpg",
  "17-tapper-knowledge-graph.jpg",
  "18-tapper-knowledge-graph-node.jpg",
  "19-test-management-plans.jpg",
  "20-test-plan-detail-linked.jpg",
  "21-test-plan-run-config.jpg",
  "22-test-plan-run-result.jpg",
  "23-test-plan-detail-unlinked.jpg",
  "24-test-management-test-data.jpg",
  "25-automation-library.jpg",
  "26-create-automation.jpg",
  "27-web-automation-bdd-mapping.jpg",
  "28-web-automation-action-editor.jpg",
  "29-web-automation-ai-agent.jpg",
  "30-web-automation-run-history.jpg",
  "31-mobile-automation-device.jpg",
  "32-mobile-automation-run-result.jpg",
  "33-tapper-test-plan-first.jpg",
  "34-tapper-test-plan-review.jpg",
  "34b-tapper-generate-linked-automation.jpg",
  "35-tapper-channel-choice.jpg",
  "36-tapper-linked-artifacts.jpg",
  "37-tapper-minimap-preview.jpg",
  "38-tapper-sources-collapsed.jpg",
  "39-tapper-sidebar-collapsed.jpg",
] as const;

class CaptureManifest<Name extends string> {
  private readonly captures = new Map<Name, string | null>();
  private readonly expected: ReadonlySet<Name>;

  constructor(private readonly expectedOrder: readonly Name[]) {
    this.expected = new Set(expectedOrder);
  }

  start(name: Name) {
    if (!this.expected.has(name)) {
      throw new Error(`Unexpected capture: ${name}`);
    }
    if (this.captures.has(name)) {
      throw new Error(`Capture produced more than once: ${name}`);
    }
    this.captures.set(name, null);
  }

  finish(name: Name, digest: string) {
    if (!this.captures.has(name)) {
      throw new Error(`Capture was not started: ${name}`);
    }
    this.captures.set(name, digest);
  }

  verifyComplete() {
    const missing = this.expectedOrder.filter(
      (name) => !this.captures.has(name),
    );
    if (missing.length > 0) {
      throw new Error(`Missing captures: ${missing.join(", ")}`);
    }

    const unfinished = this.expectedOrder.filter(
      (name) => this.captures.get(name) === null,
    );
    if (unfinished.length > 0) {
      throw new Error(`Unfinished captures: ${unfinished.join(", ")}`);
    }

    const outputsByDigest = new Map<string, Name[]>();
    for (const name of this.expectedOrder) {
      const digest = this.captures.get(name)!;
      const names = outputsByDigest.get(digest) ?? [];
      outputsByDigest.set(digest, [...names, name]);
    }
    const identicalOutputs = [...outputsByDigest.values()].find(
      (names) => names.length > 1,
    );
    if (identicalOutputs !== undefined) {
      throw new Error(
        `Byte-identical captures: ${identicalOutputs.join(", ")}`,
      );
    }
  }
}

const captureManifest = new CaptureManifest(OUTPUTS);

type DocumentAccepted = components["schemas"]["DocumentAccepted"];
type DocumentDetail = components["schemas"]["DocumentDetail"];
type DocumentSummary = components["schemas"]["DocumentSummary"];
type RetrievalAnswerResponse = components["schemas"]["RetrievalAnswerResponse"];

const MOCK_DOCUMENTS: DocumentSummary[] = [
  {
    chunkCount: 18,
    documentId: "doc_life_underwriting_guide",
    errorCode: null,
    errorSummary: null,
    filename: "Life underwriting guide.pdf",
    mediaType: "application/pdf",
    stage: "ready",
    status: "ready",
    updatedAt: "2026-09-05T02:00:00Z",
  },
  {
    chunkCount: 11,
    documentId: "doc_health_disclosure_rules",
    errorCode: null,
    errorSummary: null,
    filename: "Health disclosure rules.md",
    mediaType: "text/markdown",
    stage: "ready",
    status: "ready",
    updatedAt: "2026-09-05T02:00:00Z",
  },
  {
    chunkCount: 0,
    documentId: "doc_beneficiary_workflow",
    errorCode: null,
    errorSummary: null,
    filename: "Beneficiary workflow.docx",
    mediaType:
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    stage: "embedding",
    status: "processing",
    updatedAt: "2026-09-05T02:00:00Z",
  },
  {
    chunkCount: 0,
    documentId: "doc_archived_product_notes",
    errorCode: "document-parse-failed",
    errorSummary: "The document could not be parsed.",
    filename: "Archived product notes.txt",
    mediaType: "text/plain",
    stage: "parsing",
    status: "failed",
    updatedAt: "2026-09-05T02:00:00Z",
  },
];

const MOCK_UPLOAD: DocumentSummary = {
  chunkCount: 0,
  documentId: "doc_uploaded_application_notes",
  errorCode: null,
  errorSummary: null,
  filename: "Application notes.txt",
  mediaType: "text/plain",
  stage: "stored",
  status: "queued",
  updatedAt: "2026-09-05T02:00:00Z",
};

const MOCK_ANSWER_TEXT =
  "Life underwriting requires complete identity, coverage, and health disclosure evidence.";

const MOCK_ANSWER: RetrievalAnswerResponse = {
  abstained: false,
  abstentionReason: null,
  answer: MOCK_ANSWER_TEXT,
  citations: [
    {
      chunkContentHash: "sha256:prototype-chunk",
      chunkId: "chunk_underwriting_guide_01",
      citationId: "citation_underwriting_guide_01",
      contentRole: "source",
      derivedFromChunkIds: null,
      evidenceLabel: "Life underwriting guide",
      logicalChunkId: "logical_underwriting_guide_01",
      source: {
        anchor: {
          type: "document",
          page: 4,
          headingPath: ["Underwriting evidence"],
          startOffset: 0,
          endOffset: 84,
          bbox: null,
        },
        revision: "rev-underwriting-20260905",
        revisionKind: "blob_version",
        sourceContentHash: "sha256:prototype-source",
        sourceId: "doc_life_underwriting_guide",
        sourceType: "document",
      },
    },
  ],
  claims: [
    {
      answerEnd: MOCK_ANSWER_TEXT.length,
      answerStart: 0,
      citationIds: ["citation_underwriting_guide_01"],
      claimId: "claim_underwriting_guide_01",
      text: MOCK_ANSWER_TEXT,
    },
  ],
  contextSnapshotId: "context_prototype_capture",
  corpusVersion: "corpus_prototype_capture",
  degradationReasons: null,
  degradedMode: false,
  queryPlanId: "query_plan_prototype_capture",
  retrievalProfileId: "retrieval_profile_prototype_capture",
  traceId: "trace_prototype_capture",
};

const INGESTION_STAGES = [
  "stored",
  "parsing",
  "chunking",
  "embedding",
  "publishing",
  "ready",
] as const;

function documentDetail(document: DocumentSummary): DocumentDetail {
  const activeStage = INGESTION_STAGES.indexOf(document.stage);
  return {
    ...document,
    normalizedPreview: "Deterministic prototype document preview.",
    revisionId: `revision_${document.documentId}`,
    sourceContentHash: `sha256:${document.documentId}`,
    stages: INGESTION_STAGES.map((stage, index) => ({
      completedAt:
        index < activeStage || document.status === "ready"
          ? "2026-09-05T02:00:00Z"
          : null,
      errorCode:
        document.status === "failed" && index === activeStage
          ? document.errorCode
          : null,
      stage,
      state:
        document.status === "failed" && index === activeStage
          ? "failed"
          : index < activeStage || document.status === "ready"
            ? "completed"
            : index === activeStage
              ? "processing"
              : "pending",
    })),
  };
}

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    body: JSON.stringify(body),
    contentType: "application/json",
    status,
  });
}

async function installKnowledgeRoutes(
  page: Page,
  documents: readonly DocumentSummary[],
) {
  await page.route("**/v1/knowledge/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();

    if (path === "/v1/knowledge/documents" && method === "GET") {
      await fulfillJson(route, { items: documents, nextCursor: null });
      return;
    }
    if (path === "/v1/knowledge/documents" && method === "POST") {
      const receipt: DocumentAccepted = {
        document: MOCK_UPLOAD,
        duplicate: false,
        jobId: "job_prototype_capture",
      };
      await fulfillJson(route, receipt, 202);
      return;
    }
    if (path === "/v1/knowledge/answers" && method === "POST") {
      await fulfillJson(route, MOCK_ANSWER);
      return;
    }
    if (path.endsWith("/retry") && method === "POST") {
      const documentId = path.split("/").at(-2);
      const document =
        documents.find((item) => item.documentId === documentId) ?? MOCK_UPLOAD;
      const receipt: DocumentAccepted = {
        document,
        duplicate: false,
        jobId: "job_prototype_retry",
      };
      await fulfillJson(route, receipt, 202);
      return;
    }
    if (path.startsWith("/v1/knowledge/documents/") && method === "GET") {
      const documentId = path.split("/").at(-1);
      const document =
        documents.find((item) => item.documentId === documentId) ?? MOCK_UPLOAD;
      await fulfillJson(route, documentDetail(document));
      return;
    }
    if (path.startsWith("/v1/knowledge/documents/") && method === "DELETE") {
      await route.fulfill({ status: 204 });
      return;
    }

    await fulfillJson(
      route,
      {
        detail: "Unexpected Knowledge route in prototype capture.",
        status: 404,
        title: "Not found",
        type: "about:blank",
      },
      404,
    );
  });
}

async function startFlow(
  page: Page,
  documents: readonly DocumentSummary[] = MOCK_DOCUMENTS,
) {
  await page.clock.install({ time: new Date("2026-09-05T10:00:00+08:00") });
  await page.addInitScript(() => window.localStorage.clear());
  await installKnowledgeRoutes(page, documents);
  await page.goto("/");
  await expect(
    page.getByRole("textbox", { name: "Message Tapper" }),
  ).toBeVisible();
}

async function capture(page: Page, name: (typeof OUTPUTS)[number]) {
  await expect(page.getByLabel("TAP platform")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Tapper", includeHidden: true }),
  ).toBeVisible();
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  captureManifest.start(name);
  const imagePath = resolve(OUTPUT_DIR, name);
  await page.screenshot({
    animations: "disabled",
    caret: "hide",
    path: imagePath,
    type: "jpeg",
    quality: 90,
    fullPage: false,
  });
  const imageBytes = readFileSync(imagePath);
  captureManifest.finish(
    name,
    createHash("sha256").update(imageBytes).digest("hex"),
  );
  const source = `data:image/jpeg;base64,${imageBytes.toString("base64")}`;
  const dimensions = await page.evaluate(
    (url) =>
      new Promise<[number, number]>((resolveImage, rejectImage) => {
        const image = new Image();
        image.onload = () =>
          resolveImage([image.naturalWidth, image.naturalHeight]);
        image.onerror = () => rejectImage(new Error("invalid JPEG"));
        image.src = url;
      }),
    source,
  );
  expect(dimensions).toEqual([1280, 720]);
}

async function sendMessage(page: Page, prompt: string) {
  await page.getByRole("textbox", { name: "Message Tapper" }).fill(prompt);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(prompt, { exact: true })).toBeVisible();
}

async function openComposerMenu(page: Page) {
  await page.getByRole("button", { name: "Add to message" }).click();
  await expect(
    page.getByRole("menu", { name: "Add to message" }),
  ).toBeVisible();
}

test.use({ viewport: { width: 1280, height: 720 } });

test("checked-in screenshot inventory is canonical", () => {
  expect(OUTPUTS).toHaveLength(40);
  const actual = readdirSync(OUTPUT_DIR)
    .filter((name) => name.endsWith(".jpg"))
    .sort();
  expect(actual).toEqual([...OUTPUTS].sort());
});

test("capture manifest rejects a duplicate output name", () => {
  const manifest = new CaptureManifest(["one.jpg"] as const);
  manifest.start("one.jpg");

  expect(() => manifest.start("one.jpg")).toThrow(
    "Capture produced more than once: one.jpg",
  );
});

test("capture manifest rejects a missing output", () => {
  const manifest = new CaptureManifest(["one.jpg", "two.jpg"] as const);
  manifest.start("one.jpg");
  manifest.finish("one.jpg", "digest-one");

  expect(() => manifest.verifyComplete()).toThrow("Missing captures: two.jpg");
});

test("capture manifest rejects identical bytes for distinct outputs", () => {
  const manifest = new CaptureManifest(["one.jpg", "two.jpg"] as const);
  manifest.start("one.jpg");
  manifest.finish("one.jpg", "same-digest");
  manifest.start("two.jpg");
  manifest.finish("two.jpg", "same-digest");

  expect(() => manifest.verifyComplete()).toThrow(
    "Byte-identical captures: one.jpg, two.jpg",
  );
});

test("01 captures the fresh Tapper conversation", async ({ page }) => {
  await startFlow(page);
  await expect(
    page.getByRole("heading", { name: "What can I do for you?" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Tapper" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(page.getByRole("button", { name: "New chat" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await capture(page, "01-tapper-new-chat.jpg");
});

test("02 and 37 capture conversation minimap states", async ({ page }) => {
  await startFlow(page);
  const firstQuestion = "What evidence is required for life underwriting?";
  const secondQuestion = "How is a missing health disclosure handled?";
  await sendMessage(page, firstQuestion);
  await sendMessage(page, secondQuestion);

  const conversation = page.getByRole("log", { name: "Conversation" });
  await expect(
    conversation.getByText(firstQuestion, { exact: true }),
  ).toBeVisible();
  await expect(
    conversation.getByText(secondQuestion, { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Questions in this conversation" }),
  ).toBeVisible();
  await capture(page, "02-tapper-conversation-minimap.jpg");

  const activeTick = page.getByRole("button", {
    name: `Jump to question 2: ${secondQuestion}`,
  });
  await expect(activeTick).toHaveAttribute("aria-current", "true");
  await activeTick.hover();
  await expect(page.getByRole("tooltip")).toHaveText(secondQuestion);
  await capture(page, "37-tapper-minimap-preview.jpg");
});

test("03 captures the model-only selector", async ({ page }) => {
  await startFlow(page);
  await page
    .getByRole("button", { name: /Select model, current model GPT-5\.6 Sol/u })
    .click();
  await expect(page.getByRole("menu", { name: "Models" })).toBeVisible();
  await expect(page.getByRole("menu", { name: "Add to message" })).toHaveCount(
    0,
  );
  await capture(page, "03-tapper-model-selector.jpg");
});

test("04 through 08 capture composer context selection", async ({ page }) => {
  await startFlow(page);

  await openComposerMenu(page);
  await expect(
    page.getByRole("menuitem", { name: "Add from Library" }),
  ).toBeVisible();
  await expect(
    page.getByRole("menuitem", { name: "Use Agents" }),
  ).toBeVisible();
  await expect(
    page.getByRole("menuitem", { name: "Use Skills" }),
  ).toBeVisible();
  await capture(page, "04-tapper-context-menu.jpg");

  await page.getByRole("menuitem", { name: "Add from Library" }).click();
  const sourceDialog = page.getByRole("dialog", { name: "Add from Library" });
  await expect(sourceDialog).toBeVisible();
  await expect(
    sourceDialog.getByRole("option", { name: "Life underwriting guide.pdf" }),
  ).toBeVisible();
  await capture(page, "05-tapper-source-picker.jpg");
  await sourceDialog
    .getByRole("option", { name: "Life underwriting guide.pdf" })
    .click();

  await openComposerMenu(page);
  await page.getByRole("menuitem", { name: "Use Agents" }).click();
  const agentDialog = page.getByRole("dialog", { name: "Use Agents" });
  await expect(agentDialog).toBeVisible();
  await expect(
    agentDialog.getByRole("option", { name: "Life Underwriting Analyst" }),
  ).toBeVisible();
  await capture(page, "06-tapper-agent-picker.jpg");
  await agentDialog
    .getByRole("option", { name: "Life Underwriting Analyst" })
    .click();

  await openComposerMenu(page);
  await page.getByRole("menuitem", { name: "Use Skills" }).click();
  const skillDialog = page.getByRole("dialog", { name: "Use Skills" });
  await expect(skillDialog).toBeVisible();
  await expect(
    skillDialog.getByRole("option", { name: "BDD Scenario Design" }),
  ).toBeVisible();
  await capture(page, "07-tapper-skill-picker.jpg");
  await skillDialog
    .getByRole("option", { name: "BDD Scenario Design" })
    .click();

  const context = page.getByRole("group", { name: "Message context" });
  await expect(
    context.getByText("Life underwriting guide.pdf", { exact: true }),
  ).toBeVisible();
  await expect(
    context.getByText("Life Underwriting Analyst", { exact: true }),
  ).toBeVisible();
  await expect(
    context.getByText("BDD Scenario Design", { exact: true }),
  ).toBeVisible();
  await capture(page, "08-tapper-selected-context.jpg");
});

test("09 through 12 capture Agent and Skill catalogs", async ({ page }) => {
  await startFlow(page);

  await page.getByRole("button", { name: "Agent" }).click();
  await expect(page.getByRole("heading", { name: "Agents" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Agent catalog" })).toBeVisible();
  await capture(page, "09-tapper-agent-catalog.jpg");

  await page.getByRole("button", { name: "Create agent" }).click();
  await expect(
    page.getByRole("dialog", { name: "Create agent" }),
  ).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Name" })).toBeVisible();
  await capture(page, "10-tapper-create-agent.jpg");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Create agent" })).toHaveCount(
    0,
  );

  await page.getByRole("button", { name: "Skills" }).click();
  await expect(page.getByRole("heading", { name: "Skills" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Skill catalog" })).toBeVisible();
  await capture(page, "11-tapper-skill-catalog.jpg");

  await page.getByRole("button", { name: "Create skill" }).click();
  await expect(
    page.getByRole("dialog", { name: "Create skill" }),
  ).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Instructions" }),
  ).toBeVisible();
  await capture(page, "12-tapper-create-skill.jpg");
});

test("13 captures the empty Library", async ({ page }) => {
  await startFlow(page, []);
  await page.getByRole("button", { name: "Library" }).click();
  await expect(page.getByRole("heading", { name: "Library" })).toBeVisible();
  await expect(
    page.getByText("No matching sources", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("0/0 sources", { exact: true })).toBeVisible();
  await capture(page, "13-tapper-library-empty.jpg");
});

test("14 through 18 capture Library list and graph states", async ({
  page,
}) => {
  await startFlow(page);
  await page.getByRole("button", { name: "Library" }).click();

  await expect(
    page.getByRole("list", { name: "Library sources" }),
  ).toBeVisible();
  await expect(page.getByText("4/4 sources", { exact: true })).toBeVisible();
  await capture(page, "14-tapper-library-all.jpg");

  await page
    .getByRole("textbox", { name: "Search library" })
    .fill("underwriting");
  await page.getByRole("combobox", { name: "Type" }).selectOption("PDF");
  await page.getByRole("combobox", { name: "Status" }).selectOption("ready");
  await expect(page.getByText("1/4 sources", { exact: true })).toBeVisible();
  await expect(
    page
      .getByRole("tabpanel", { name: "All" })
      .getByText("Life underwriting guide.pdf", { exact: true }),
  ).toBeVisible();
  await capture(page, "15-tapper-library-filtered.jpg");

  await page.getByRole("button", { name: "Clear filters" }).click();
  await page.getByRole("button", { name: "Add source" }).click();
  await expect(page.getByRole("dialog", { name: "Add source" })).toBeVisible();
  await expect(page.getByLabel("Source file")).toBeVisible();
  await capture(page, "16-tapper-add-source.jpg");
  await page.keyboard.press("Escape");

  await page.getByRole("tab", { name: "Knowledge Graph" }).click();
  const graph = page.getByRole("group", {
    name: "Life insurance knowledge graph",
  });
  await expect(graph).toBeVisible();
  await page.getByRole("button", { name: "Zoom out" }).click();
  await page.getByRole("button", { name: "Zoom out" }).click();
  await expect(page.getByRole("status", { name: "Zoom level" })).toHaveText(
    "50%",
  );
  await expect(
    page.getByRole("region", { name: "Node details" }),
  ).toContainText("Select a node to inspect its relationships.");
  const graphBox = await graph.boundingBox();
  expect(graphBox).not.toBeNull();
  if (graphBox === null) throw new Error("Graph capture geometry is missing");
  for (const label of [
    "Life underwriting guide.pdf",
    "Beneficiary workflow.docx",
  ]) {
    const labelBox = await graph
      .getByText(label, { exact: true })
      .boundingBox();
    expect(labelBox).not.toBeNull();
    if (labelBox === null)
      throw new Error(`Graph label geometry is missing: ${label}`);
    expect(labelBox.x).toBeGreaterThanOrEqual(graphBox.x);
    expect(labelBox.x + labelBox.width).toBeLessThanOrEqual(
      graphBox.x + graphBox.width,
    );
    expect(labelBox.y).toBeGreaterThanOrEqual(graphBox.y);
    expect(labelBox.y + labelBox.height).toBeLessThanOrEqual(
      Math.min(graphBox.y + graphBox.height, page.viewportSize()?.height ?? 0),
    );
  }
  await capture(page, "17-tapper-knowledge-graph.jpg");

  await page
    .getByRole("button", {
      name: "Life insurance application · Concept · Application",
    })
    .click();
  const inspector = page.getByRole("region", { name: "Node details" });
  await expect(
    inspector.getByRole("heading", { name: "Life insurance application" }),
  ).toBeVisible();
  await expect(inspector).toContainText("Relationships");
  await capture(page, "18-tapper-knowledge-graph-node.jpg");
});

test("19 through 24 capture Test Management journeys", async ({ page }) => {
  await startFlow(page);
  await page.getByRole("button", { name: "Test Management" }).click();

  await expect(
    page.getByRole("heading", { name: "Test Management" }),
  ).toBeVisible();
  await expect(
    page.getByRole("table", { name: "Test plan list" }),
  ).toBeVisible();
  await capture(page, "19-test-management-plans.jpg");

  await page.getByRole("button", { name: "Open TP-101" }).click();
  await expect(
    page.getByRole("heading", {
      name: "Life insurance application underwriting",
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Linked Automation" }),
  ).toContainText("AUTO-101");
  await capture(page, "20-test-plan-detail-linked.jpg");

  await page
    .getByRole("combobox", { name: "Execution Agent" })
    .selectOption("ado-web-agent-03");
  await expect(
    page.getByRole("button", { name: "Run automation" }),
  ).toBeEnabled();
  await expect(
    page.getByRole("combobox", { name: "Execution Agent" }),
  ).toHaveValue("ado-web-agent-03");
  await capture(page, "21-test-plan-run-config.jpg");

  await page.getByRole("button", { name: "Run automation" }).click();
  await expect(page.getByText("RUN-001", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Completed · Simulated", { exact: true }),
  ).toBeVisible();
  await capture(page, "22-test-plan-run-result.jpg");

  await page.getByRole("button", { name: "Back to Test Plans" }).click();
  await page.getByRole("button", { name: "Open TP-102" }).click();
  await expect(
    page.getByRole("region", { name: "Linked Automation" }),
  ).toContainText("No Automation linked");
  await expect(
    page.getByText("Link an Automation to run this plan", { exact: true }),
  ).toBeVisible();
  await capture(page, "23-test-plan-detail-unlinked.jpg");

  await page.getByRole("button", { name: "Back to Test Plans" }).click();
  await page.getByRole("tab", { name: "Test Data" }).click();
  await expect(page.getByRole("tabpanel", { name: "Test Data" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Reusable test data" }),
  ).toBeVisible();
  await capture(page, "24-test-management-test-data.jpg");
});

test("25 through 32 capture Low Code Automation journeys", async ({ page }) => {
  await startFlow(page);
  await page.getByRole("button", { name: "Low Code Automation" }).click();

  await expect(
    page.getByRole("heading", { name: "Low Code Automation" }),
  ).toBeVisible();
  await expect(
    page.getByRole("table", { name: "Low Code Automation" }),
  ).toBeVisible();
  await capture(page, "25-automation-library.jpg");

  await page.getByRole("button", { name: "New automation" }).click();
  await page
    .getByRole("textbox", { name: "Automation title" })
    .fill("Quote verification journey");
  await page
    .getByRole("textbox", { name: "Describe what to automate" })
    .fill("Verify a life insurance quote in the web application");
  await page
    .getByRole("combobox", { name: "Automation type" })
    .selectOption("web");
  await expect(
    page.getByRole("button", { name: "Generate BDD" }),
  ).toBeVisible();
  await capture(page, "26-create-automation.jpg");
  await page.getByRole("button", { name: "Generate BDD" }).click();
  await expect(
    page.getByRole("heading", { name: "Quote verification journey" }),
  ).toBeVisible();

  await page
    .getByRole("button", { name: "Back to Automation Library" })
    .click();
  await page.getByRole("button", { name: "Open AUTO-101" }).click();
  await expect(
    page.getByRole("heading", {
      name: "Life insurance application automation",
    }),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "BDD Builder" })).toBeVisible();
  await expect(
    page.getByText("Navigate", { exact: true }).first(),
  ).toBeVisible();
  await capture(page, "27-web-automation-bdd-mapping.jpg");

  await page.getByRole("button", { name: "Edit automation actions 1" }).click();
  await expect(
    page.getByRole("combobox", { name: "Action 1 for BDD step 1" }),
  ).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Locator or target 1 for BDD step 1" }),
  ).toBeVisible();
  await capture(page, "28-web-automation-action-editor.jpg");

  await page.getByRole("tab", { name: "AI Agent" }).click();
  const agentPanel = page.getByRole("region", { name: "Automation AI Agent" });
  await agentPanel
    .getByRole("textbox", { name: "Message Automation AI Agent" })
    .fill("Add validation for missing health disclosures.");
  await agentPanel.getByRole("button", { name: "Propose changes" }).click();
  await expect(agentPanel).toContainText(
    "Suggested change: add a validation scenario for missing health disclosures.",
  );
  await capture(page, "29-web-automation-ai-agent.jpg");

  await page.getByRole("tab", { name: "Run" }).click();
  await page
    .getByRole("combobox", { name: "Execution Agent" })
    .selectOption("ado-web-agent-03");
  await page.getByRole("button", { name: "Run automation" }).click();
  await expect(page.getByText("RUN-001", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Automation run history" }),
  ).toContainText("Completed · Simulated");
  await capture(page, "30-web-automation-run-history.jpg");

  await page
    .getByRole("button", { name: "Back to Automation Library" })
    .click();
  await page.getByRole("button", { name: "Open AUTO-102" }).click();
  await expect(
    page.getByRole("heading", { name: "Claims photo upload" }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "Run platform" })
    .selectOption("ios");
  await page
    .getByRole("combobox", { name: "Device" })
    .selectOption("iphone-15");
  await expect(
    page.getByRole("button", { name: "Run automation" }),
  ).toBeEnabled();
  await expect(page.getByRole("combobox", { name: "Device" })).toHaveValue(
    "iphone-15",
  );
  await capture(page, "31-mobile-automation-device.jpg");

  await page.getByRole("button", { name: "Run automation" }).click();
  await expect(page.getByText("RUN-002", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Completed · Simulated", { exact: true }),
  ).toBeVisible();
  await capture(page, "32-mobile-automation-run-result.jpg");
});

test("33, 34, 34b, and 36 capture the linked generation journey", async ({
  page,
}) => {
  await startFlow(page);
  const prompt =
    "Generate a Web automation script for a life insurance application";
  await sendMessage(page, prompt);
  const artifact = page.getByRole("article", { name: "Generated automation" });

  await expect(
    artifact.getByText("Create a Test Plan first?", { exact: true }),
  ).toBeVisible();
  await expect(
    artifact.getByRole("button", { name: "Create Test Plan first" }),
  ).toBeVisible();
  await capture(page, "33-tapper-test-plan-first.jpg");

  await artifact
    .getByRole("button", { name: "Create Test Plan first" })
    .click();
  await expect(
    artifact.getByText("Test Plan ready", { exact: true }),
  ).toBeVisible();
  await expect(
    artifact.getByText(/TP-103 · 3 scenarios · Draft/u),
  ).toBeVisible();
  await capture(page, "34-tapper-test-plan-review.jpg");

  const generateButton = artifact.getByRole("button", {
    name: "Generate linked automation",
  });
  await generateButton.scrollIntoViewIfNeeded();
  await generateButton.focus();
  await expect(generateButton).toBeVisible();
  await expect(generateButton).toBeFocused();
  const bddHeadings = [
    artifact.getByText("Feature: Life insurance application underwriting", {
      exact: true,
    }),
    artifact.getByText("Scenario: Complete application enters underwriting", {
      exact: true,
    }),
    artifact.getByText("Scenario: Missing health disclosure is blocked", {
      exact: true,
    }),
    artifact.getByText("Scenario: High coverage requires manual review", {
      exact: true,
    }),
  ];
  const composer = page.getByRole("form", { name: "Message composer" });
  const viewport = page.viewportSize();
  const composerBox = await composer.boundingBox();
  const generateBox = await generateButton.boundingBox();
  expect(viewport).not.toBeNull();
  expect(composerBox).not.toBeNull();
  expect(generateBox).not.toBeNull();
  if (viewport === null || composerBox === null || generateBox === null) {
    throw new Error("Required capture geometry is unavailable");
  }
  expect(generateBox.x).toBeGreaterThanOrEqual(0);
  expect(generateBox.y).toBeGreaterThanOrEqual(0);
  expect(generateBox.x + generateBox.width).toBeLessThanOrEqual(viewport.width);
  expect(generateBox.y + generateBox.height).toBeLessThanOrEqual(
    viewport.height,
  );
  expect(generateBox.y + generateBox.height).toBeLessThanOrEqual(composerBox.y);
  for (const heading of bddHeadings) {
    const headingBox = await heading.boundingBox();
    expect(headingBox).not.toBeNull();
    if (headingBox === null) throw new Error("BDD heading geometry is missing");
    expect(headingBox.y).toBeGreaterThanOrEqual(0);
    expect(headingBox.y + headingBox.height).toBeLessThanOrEqual(composerBox.y);
  }
  await capture(page, "34b-tapper-generate-linked-automation.jpg");

  await generateButton.click();
  await expect(
    artifact.getByText("Automation draft ready", { exact: true }),
  ).toBeVisible();
  await expect(artifact).toContainText("Linked to Test Plan");
  const openAutomationButton = artifact.getByRole("button", {
    name: "Open in Low Code Automation",
  });
  await openAutomationButton.scrollIntoViewIfNeeded();
  await openAutomationButton.focus();
  await expect(openAutomationButton).toBeFocused();
  await capture(page, "36-tapper-linked-artifacts.jpg");
});

test("35 captures explicit automation channel choice", async ({ page }) => {
  await startFlow(page);
  await sendMessage(
    page,
    "Generate an automation for a life insurance application",
  );
  const artifact = page.getByRole("article", { name: "Generated automation" });
  await artifact
    .getByRole("button", { name: "Create Test Plan first" })
    .click();
  await artifact
    .getByRole("button", { name: "Generate linked automation" })
    .click();
  await expect(
    artifact.getByText("Choose Web or Mobile", { exact: true }),
  ).toBeVisible();
  await expect(
    artifact.getByRole("button", { name: "Create Web automation" }),
  ).toBeVisible();
  await expect(
    artifact.getByRole("button", { name: "Create Mobile automation" }),
  ).toBeVisible();
  await capture(page, "35-tapper-channel-choice.jpg");
});

test("38 captures collapsed Knowledge sources", async ({ page }) => {
  await startFlow(page);
  await page
    .getByRole("button", { name: "Collapse Knowledge sources" })
    .click();
  await expect(
    page.getByRole("button", { name: "Expand Knowledge sources" }),
  ).toHaveAttribute("aria-expanded", "false");
  await expect(
    page.getByRole("heading", { name: "What can I do for you?" }),
  ).toBeVisible();
  await capture(page, "38-tapper-sources-collapsed.jpg");
});

test("39 captures collapsed Tapper sidebar", async ({ page }) => {
  await startFlow(page);
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await expect(
    page.getByRole("button", { name: "Expand sidebar" }),
  ).toHaveAttribute("aria-expanded", "false");
  await expect(
    page.getByRole("heading", { name: "What can I do for you?" }),
  ).toBeVisible();
  await capture(page, "39-tapper-sidebar-collapsed.jpg");
});

test("full capture manifest is complete and byte-distinct", () => {
  captureManifest.verifyComplete();
});
