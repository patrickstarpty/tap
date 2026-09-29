import type { components } from "../../src/shared/api/generated/schema";
import { expect, test } from "@playwright/test";
import type {
  KnowledgeReviewDetail,
  SourceAccepted,
  SourceDetail,
} from "../../src/features/knowledge/api/types";
import {
  DEFAULT_CHUNK_SETTINGS,
  type ChunkPage,
  type ManagedChunk,
} from "../../src/features/knowledge/api/chunks";
import { writeReviewState } from "./fixtureBuilder";
const ORIGIN = "http://127.0.0.1:15173";

test("uploaded knowledge supports direct chunk editing, indexing, chat and disabling with original history intact", async ({
  page,
}) => {
  test.setTimeout(360_000);
  const runtimeResponse = await page.request.get("/api/v1/runtime-mode");
  expect(runtimeResponse.status()).toBe(200);
  const runtime = (await runtimeResponse.json()) as { projectId: string };
  const knowledge = `/api/v1/projects/${encodeURIComponent(runtime.projectId)}/knowledge`;
  const filename = `chunk-evidence-${Date.now()}.md`;
  const upload = await page.request.post(`${knowledge}/sources`, {
    headers: {
      Origin: ORIGIN,
      "Idempotency-Key": `chunk-source-${Date.now()}`,
    },
    multipart: {
      upload: {
        name: filename,
        mimeType: "text/markdown",
        buffer: Buffer.from(
          `# Policy evidence ${filename}\n\nThe application requires verified identity evidence.`,
        ),
      },
    },
  });
  expect(upload.status()).toBe(202);
  const accepted = (await upload.json()) as SourceAccepted;
  const sourceId = accepted.source.sourceId;
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${knowledge}/sources/${sourceId}?limit=50`,
        );
        expect(response.status()).toBe(200);
        return ((await response.json()) as SourceDetail).readyCount;
      },
      { timeout: 45000 },
    )
    .toBeGreaterThan(0);
  const detail = (await (
    await page.request.get(`${knowledge}/sources/${sourceId}?limit=50`)
  ).json()) as SourceDetail;
  const revision = detail.documents.items.find(
    (item) => item.status === "ready",
  )!;
  const chunkUrl = `${knowledge}/documents/${revision.documentId}/chunks`;
  // Historical fixture survives independently of direct chunk indexing.
  const opened = await page.request.post(
    `${knowledge}/documents/${revision.documentId}/review`,
    {
      headers: {
        Origin: ORIGIN,
        "Idempotency-Key": `historical-review-${revision.documentId}`,
      },
      data: {
        sourceRevisionId: revision.revisionId,
      } satisfies components["schemas"]["KnowledgeReviewOpenRequest"],
    },
  );
  expect(opened.status()).toBe(200);
  const historical = (await opened.json()) as KnowledgeReviewDetail;
  await page.goto("/");
  await page.getByRole("button", { name: "Library", exact: true }).click();
  await page.getByRole("button", { name: `View ${filename}` }).click();
  const dialog = page.getByRole("dialog", { name: filename });
  await dialog.getByRole("button", { name: `管理切片 ${filename}` }).click();
  await expect(dialog.getByRole("heading", { name: "切片管理" })).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "开始业务审核" }),
  ).toHaveCount(0);
  await dialog
    .getByRole("button", { name: /编辑切片/ })
    .first()
    .click();
  await page
    .getByRole("textbox", { name: "切片内容", exact: true })
    .fill(
      "The application requires verified identity evidence and a signed passport record.",
    );
  await page.getByRole("button", { name: "保存并索引" }).click();
  await expect(dialog.getByText("已编辑").first()).toBeVisible();
  await expect
    .poll(
      async () => {
        const response = await page.request.get(chunkUrl);
        const data = (await response.json()) as ChunkPage;
        return (
          data.items.length > 0 &&
          data.items.every((item) => item.indexStatus === "ready")
        );
      },
      { timeout: 45000 },
    )
    .toBe(true);
  await page.reload();
  await page.getByRole("button", { name: "Library", exact: true }).click();
  await page.getByRole("button", { name: `View ${filename}` }).click();
  await dialog.getByRole("button", { name: `管理切片 ${filename}` }).click();
  await expect(
    dialog
      .locator(".tapper-chunk-content")
      .filter({ hasText: "signed passport record" }),
  ).toBeVisible();
  const original = await page.request.get(
    `${knowledge}/documents/${revision.documentId}/original`,
  );
  expect(original.status()).toBe(200);
  const originalText = await original.text();
  expect(originalText).toContain("verified identity evidence");
  expect(originalText).not.toContain("signed passport record");
  await expect(
    dialog.getByRole("link", { name: "查看原始文件" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close Knowledge sources" }).click();
  await page.getByRole("button", { name: "New chat" }).click();
  await expect(
    page.getByRole("checkbox", { name: new RegExp(filename, "u") }),
  ).toBeVisible();
  await page.getByRole("checkbox", { name: new RegExp(filename, "u") }).check();
  const root = `/api/v1/projects/${encodeURIComponent(runtime.projectId)}`;
  const [agentsResponse, skillsResponse, modelsResponse] = await Promise.all([
    page.request.get(`${root}/ai/agents`),
    page.request.get(`${root}/ai/skills`),
    page.request.get(`${root}/ai/models`),
  ]);
  expect(agentsResponse.status()).toBe(200);
  expect(skillsResponse.status()).toBe(200);
  expect(modelsResponse.status()).toBe(200);
  const agents = (await agentsResponse.json()) as {
    items: Array<{ displayName: string }>;
  };
  const skills = (await skillsResponse.json()) as {
    items: Array<{ displayName: string }>;
  };
  const models = (await modelsResponse.json()) as {
    defaultAlias: string;
    items: Array<{ alias: string; displayName: string }>;
  };
  const model = models.items.find((item) => item.alias === models.defaultAlias);
  expect(agents.items[0]).toBeDefined();
  expect(skills.items[0]).toBeDefined();
  expect(model).toBeDefined();
  await page.getByRole("button", { name: "Add to message" }).click();
  await page.getByRole("menuitem", { name: "Use Agents" }).click();
  await page
    .getByRole("option", { name: agents.items[0]!.displayName })
    .click();
  await page.getByRole("button", { name: "Add to message" }).click();
  await page.getByRole("menuitem", { name: "Use Skills" }).click();
  await page
    .getByRole("option", { name: skills.items[0]!.displayName })
    .click();
  await page
    .getByRole("button", {
      name: `Select model, current model ${model!.displayName}`,
    })
    .click();
  await page.getByRole("menuitemradio", { name: model!.displayName }).click();
  await page
    .getByRole("textbox", { name: "Message Tapper" })
    .fill("What identity evidence is required?");
  await page.getByRole("button", { name: "Send" }).click();
  const citation = page
    .getByRole("button", { name: /Open source citation 1|打开来源引用 1/u })
    .first();
  await expect(citation).toBeVisible({ timeout: 45_000 });
  await citation.click();
  await expect(
    page.getByRole("heading", { name: /Cited evidence|引用依据/u }),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "Message Tapper" })
    .fill("Does the policy require verified identity evidence?");
  await page.getByRole("button", { name: "Send" }).click();
  const followUp = page.locator(".tap-turn").filter({
    hasText: "Does the policy require verified identity evidence?",
  });
  await expect(
    followUp.getByRole("button", {
      name: /Open source citation 1|打开来源引用 1/u,
    }),
  ).toBeVisible({ timeout: 45_000 });

  await page.getByRole("button", { name: "Library", exact: true }).click();
  await page.getByRole("button", { name: `View ${filename}` }).click();
  await dialog.getByRole("button", { name: `管理切片 ${filename}` }).click();
  // Exercise real creation and deletion without a review transition.
  await dialog.getByRole("button", { name: "新增切片" }).click();
  await page
    .getByRole("textbox", { name: "切片内容", exact: true })
    .fill("Temporary evidence for deletion.");
  await page.getByRole("button", { name: "保存并索引" }).click();
  const temporary = dialog
    .locator(".tapper-chunk")
    .filter({ hasText: "Temporary evidence for deletion." });
  await expect(temporary).toBeVisible();
  await temporary.getByRole("button", { name: /^删\s*除$/u }).click();
  await page.getByRole("button", { name: "确认删除" }).click();
  await expect(temporary).toHaveCount(0);
  const chunks = (await (await page.request.get(chunkUrl)).json()) as ChunkPage;
  const disabled = await page.request.post(`${chunkUrl}/batch`, {
    headers: { Origin: ORIGIN },
    data: {
      action: "disable",
      items: chunks.items.map(({ chunkId, version }) => ({ chunkId, version })),
    } satisfies components["schemas"]["KnowledgeChunkBatch"],
  });
  expect(disabled.status()).toBe(200);
  await page.reload();
  await page.getByRole("button", { name: "New chat" }).click();
  await expect(
    page.getByRole("checkbox", { name: new RegExp(filename, "u") }),
  ).toHaveCount(0);

  // Real pre-upload parsing and persisted parent/child maintenance share settings.
  for (const parentMode of ["paragraph", "full_doc"] as const) {
    const settings = {
      ...DEFAULT_CHUNK_SETTINGS,
      mode: "parent_child" as const,
      parentMode,
      maxLength: 180,
      overlap: 0,
      childMaxLength: 45,
    } satisfies components["schemas"]["KnowledgeChunkSettings"];
    const parentFile = {
      name: `parent-${parentMode}-${Date.now()}.txt`,
      mimeType: "text/plain",
      buffer: Buffer.from(
        `Identity evidence requires a valid passport. The applicant must provide their full legal name. The reviewer checks the date of birth. Record ${parentMode} ${Date.now()}.`,
      ),
    };
    const previewResponse = await page.request.post(
      `${knowledge}/chunks/preview`,
      {
        headers: { Origin: ORIGIN },
        multipart: { upload: parentFile, settings: JSON.stringify(settings) },
      },
    );
    expect(previewResponse.status()).toBe(200);
    const preview = (await previewResponse.json()) as {
      items: ManagedChunk[];
      total: number;
    };
    const uploaded = await page.request.post(`${knowledge}/sources`, {
      headers: {
        Origin: ORIGIN,
        "Idempotency-Key": `parent-${parentMode}-${Date.now()}`,
      },
      multipart: { upload: parentFile, settings: JSON.stringify(settings) },
    });
    expect(uploaded.status()).toBe(202);
    const parentReceipt = (await uploaded.json()) as SourceAccepted;
    const parentSource = parentReceipt.source.sourceId;
    const parentDocument = parentReceipt.accepted.document.documentId;
    const parentUrl = `${knowledge}/documents/${parentDocument}/chunks`;
    const load = async () => {
      const response = await page.request.get(parentUrl);
      expect(response.status()).toBe(200);
      return (await response.json()) as ChunkPage;
    };
    try {
      await expect
        .poll(
          async () => {
            const response = await page.request.get(
              `${knowledge}/sources/${parentSource}?limit=50`,
            );
            expect(response.status()).toBe(200);
            const progress = (await response.json()) as SourceDetail;
            expect(progress.failedCount).toBe(0);
            return progress.readyCount;
          },
          { timeout: 90000 },
        )
        .toBeGreaterThan(0);
      let current = await load();
      expect(current.items.map((item) => item.content)).toEqual(
        preview.items.map((item) => item.content),
      );
      expect(current.total).toBe(preview.total);
      const parent = current.items[0]!;
      expect(parent.children!.length).toBeGreaterThan(1);
      if (parentMode === "full_doc") {
        const rejected = await page.request.patch(
          `${parentUrl}/${parent.chunkId}`,
          {
            headers: { Origin: ORIGIN },
            data: {
              version: parent.version,
              content: "Replace full document",
            } satisfies components["schemas"]["KnowledgeChunkChange"],
          },
        );
        expect(rejected.status()).toBe(409);
      } else {
        const child = parent.children![0]!;
        const editedChild = await page.request.patch(
          `${parentUrl}/${child.chunkId}`,
          {
            headers: { Origin: ORIGIN },
            data: {
              version: child.version,
              content: "Manually verified child evidence.",
            } satisfies components["schemas"]["KnowledgeChunkChange"],
          },
        );
        expect(editedChild.status()).toBe(200);
        current = await load();
        expect(current.items[0]!.content).toBe(parent.content);
        expect(current.items[0]!.children![0]!.content).toBe(
          "Manually verified child evidence.",
        );
        const kept = await page.request.patch(
          `${parentUrl}/${parent.chunkId}`,
          {
            headers: { Origin: ORIGIN },
            data: {
              version: current.items[0]!.version,
              content: "Updated parent evidence with preserved child content.",
              regenerateChildren: false,
            } satisfies components["schemas"]["KnowledgeChunkChange"],
          },
        );
        expect(kept.status()).toBe(200);
        current = await load();
        expect(current.items[0]!.children![0]!.content).toBe(
          "Manually verified child evidence.",
        );
        const regenerated = await page.request.patch(
          `${parentUrl}/${parent.chunkId}`,
          {
            headers: { Origin: ORIGIN },
            data: {
              version: current.items[0]!.version,
              content: "Regenerated evidence requires renewed identification.",
              regenerateChildren: true,
            } satisfies components["schemas"]["KnowledgeChunkChange"],
          },
        );
        expect(regenerated.status()).toBe(200);
        current = await load();
        expect(
          current.items[0]!.children!.map((item) => item.content).join(""),
        ).toContain("Regenerated evidence");
        expect(
          current.items[0]!.children!.some(
            (item) => item.content === "Manually verified child evidence.",
          ),
        ).toBe(false);
      }
    } finally {
      const removed = await page.request.delete(
        `${knowledge}/sources/${parentSource}`,
        {
          headers: {
            Origin: ORIGIN,
            "Idempotency-Key": `cleanup-${parentSource}`,
          },
        },
      );
      expect(removed.status()).toBe(204);
      await expect
        .poll(
          async () =>
            (
              await page.request.get(
                `${knowledge}/sources/${parentSource}?limit=50`,
              )
            ).status(),
          { timeout: 45000 },
        )
        .toBe(404);
    }
  }
  await writeReviewState({
    reviewId: historical.reviewId,
    revisionId: revision.revisionId,
    documentId: revision.documentId,
    sourceId,
  });
});
