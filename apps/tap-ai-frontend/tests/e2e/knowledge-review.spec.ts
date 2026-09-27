import { expect, test } from "@playwright/test";

import type {
  KnowledgeReviewDetail,
  KnowledgeReviewPage,
  SourceAccepted,
  SourceDetail,
} from "../../src/features/knowledge/api/types";
import { writeReviewState } from "./fixtureBuilder";
import { approveExistingReviewFixture } from "./publicationFixture";

const ORIGIN = "http://127.0.0.1:15173";

test("upload, review, independent handoff, publication, chat, and withdrawal use durable authority", async ({
  page,
}) => {
  const runtimeResponse = await page.request.get("/api/v1/runtime-mode");
  expect(runtimeResponse.status()).toBe(200);
  const runtime = (await runtimeResponse.json()) as { projectId: string };
  const knowledge = `/api/v1/projects/${encodeURIComponent(runtime.projectId)}/knowledge`;
  const filename = `review-evidence-${Date.now()}.md`;
  const upload = await page.request.post(`${knowledge}/sources`, {
    headers: {
      Origin: ORIGIN,
      "Idempotency-Key": `review-source-${Date.now()}`,
    },
    multipart: {
      upload: {
        name: filename,
        mimeType: "text/markdown",
        buffer: Buffer.from(
          "# Policy evidence\n\nThe application requires verified identity evidence.",
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
      { timeout: 45_000 },
    )
    .toBeGreaterThan(0);
  const detailResponse = await page.request.get(
    `${knowledge}/sources/${sourceId}?limit=50`,
  );
  const detail = (await detailResponse.json()) as SourceDetail;
  const revision = detail.documents.items.find(
    (item) => item.status === "ready",
  );
  expect(revision).toBeDefined();

  await page.goto("/");
  await expect(
    page.getByRole("checkbox", { name: new RegExp(filename, "u") }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Library", exact: true }).click();
  await page.getByRole("button", { name: `View ${filename}` }).click();
  const dialog = page.getByRole("dialog", { name: filename });
  await dialog.getByRole("button", { name: `Review ${filename}` }).click();
  await expect(
    dialog.getByText("未关联审核记录", { exact: false }),
  ).toBeVisible();
  const openResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname ===
        `${knowledge}/documents/${revision!.documentId}/review`,
  );
  await dialog.getByRole("button", { name: "开始业务审核" }).click();
  expect((await openResponse).status()).toBe(200);
  await expect(dialog.getByText("审核版本 1")).toBeVisible();
  const listed = await page.request.get(`${knowledge}/reviews`, {
    params: { sourceRevisionId: revision!.revisionId },
  });
  expect(listed.status()).toBe(200);
  const reviews = (await listed.json()) as KnowledgeReviewPage;
  expect(reviews.items).toHaveLength(1);
  const reviewId = reviews.items[0]!.reviewId;

  const inventory = dialog.locator(".tapper-review-list");
  await inventory.locator("li").first().getByRole("button").click();
  await expect(
    dialog.getByRole("heading", { name: "原件与提取对照" }),
  ).toBeVisible();
  const previews = dialog.locator(".tapper-review-comparison pre");
  await expect(previews).toHaveCount(2);
  await expect(previews.first()).toContainText(
    /Policy evidence|verified identity/u,
  );
  await expect(previews.last()).toContainText(
    /Policy evidence|verified identity/u,
  );
  await dialog
    .getByRole("textbox", { name: "核对说明" })
    .fill("Checked against the original evidence.");
  await dialog.getByRole("button", { name: "保存核对" }).click();
  await expect(dialog.getByText("审核版本 2")).toBeVisible();
  await dialog.getByRole("button", { name: "提交独立复核" }).click();
  await expect(dialog.getByText("待独立复核")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "批准审核" })).toHaveCount(0);

  const submittedResponse = await page.request.get(
    `${knowledge}/reviews/${reviewId}`,
  );
  expect(submittedResponse.status()).toBe(200);
  const submitted = (await submittedResponse.json()) as KnowledgeReviewDetail;
  expect(submitted.status).toBe("reviewing");
  const selfApproval = await page.request.post(
    `${knowledge}/reviews/${reviewId}/approve`,
    {
      headers: { Origin: ORIGIN, "If-Match": `"${submitted.version}"` },
    },
  );
  expect(selfApproval.status()).toBe(409);
  approveExistingReviewFixture(reviewId, revision!.revisionId);

  await page.reload();
  await page.getByRole("button", { name: "Library", exact: true }).click();
  await page.getByRole("button", { name: `View ${filename}` }).click();
  const approvedDialog = page.getByRole("dialog", { name: filename });
  await approvedDialog
    .getByRole("button", { name: `Review ${filename}` })
    .click();
  await expect(
    approvedDialog.getByText("已批准", { exact: true }),
  ).toBeVisible();
  await approvedDialog.getByRole("button", { name: /发\s*布/u }).click();
  await expect(
    approvedDialog.getByText("发布状态：已发布", { exact: false }),
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
    page.getByRole("heading", { name: /Cited source|原文依据/u }),
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
  const publishedDialog = page.getByRole("dialog", { name: filename });
  await publishedDialog
    .getByRole("button", { name: `Review ${filename}` })
    .click();
  await publishedDialog.getByRole("button", { name: "撤回发布" }).click();
  await page.getByRole("button", { name: "确认撤回" }).click();
  await expect(
    publishedDialog.getByText("发布状态：未发布", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close Knowledge sources" }).click();
  await page.reload();
  await page
    .getByRole("button", { name: /Open source citation 1|打开来源引用 1/u })
    .first()
    .click();
  await expect(page.getByRole("alert")).toContainText(/withdrawn|已撤回/u);
  await page.getByRole("button", { name: "New chat" }).click();
  await expect(
    page.getByRole("checkbox", { name: new RegExp(filename, "u") }),
  ).toHaveCount(0);
  await writeReviewState({
    reviewId,
    revisionId: revision!.revisionId,
    documentId: revision!.documentId,
    sourceId,
  });
});
