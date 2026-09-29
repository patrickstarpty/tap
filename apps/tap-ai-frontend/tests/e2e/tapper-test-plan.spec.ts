import { expect, test } from "@playwright/test";
import { preparePublishedFixture } from "./publicationFixture";

const ORIGIN = "http://127.0.0.1:15173";
let cleanupSourceId: string | null = null;

test.afterEach(async ({ request }) => {
  if (cleanupSourceId === null) return;
  const runtime = (await (
    await request.get("/api/v1/runtime-mode")
  ).json()) as {
    projectId: string;
  };
  const deleted = await request.delete(
    `/api/v1/projects/${encodeURIComponent(runtime.projectId)}/knowledge/sources/${cleanupSourceId}`,
    {
      headers: {
        Origin: ORIGIN,
        "Idempotency-Key": `cleanup-test-plan-${cleanupSourceId}`,
      },
    },
  );
  expect(deleted.status()).toBe(204);
  await expect
    .poll(
      async () =>
        (
          await request.get(
            `/api/v1/projects/${encodeURIComponent(runtime.projectId)}/knowledge/sources/${cleanupSourceId}`,
          )
        ).status(),
      { timeout: 45_000 },
    )
    .toBe(404);
  cleanupSourceId = null;
});

test("Tapper generates, reviews, deep-links, and publishes a grounded Test Plan", async ({
  page,
}) => {
  const runtime = (await (
    await page.request.get("/api/v1/runtime-mode")
  ).json()) as { projectId: string };
  const root = `/api/v1/projects/${encodeURIComponent(runtime.projectId)}`;
  const marker = Date.now();
  const uploaded = await page.request.post(`${root}/knowledge/sources`, {
    headers: {
      Origin: ORIGIN,
      "Idempotency-Key": `test-plan-source-${marker}`,
    },
    multipart: {
      upload: {
        name: `checkout-${marker}.md`,
        mimeType: "text/markdown",
        buffer: Buffer.from(
          "# Checkout policy\n\nA successful card payment creates one confirmed order.",
        ),
      },
    },
  });
  expect(uploaded.status(), `upload: ${await uploaded.text()}`).toBe(202);
  const source = (await uploaded.json()) as { source: { sourceId: string } };
  cleanupSourceId = source.source.sourceId;
  let sourceRevisionId = "";
  await expect
    .poll(
      async () => {
        const detail = (await (
          await page.request.get(
            `${root}/knowledge/sources/${source.source.sourceId}?limit=50`,
          )
        ).json()) as {
          documents: { items: Array<{ revisionId: string; status: string }> };
        };
        sourceRevisionId =
          detail.documents.items.find((item) => item.status === "ready")
            ?.revisionId ?? "";
        return sourceRevisionId;
      },
      { timeout: 45_000 },
    )
    .not.toBe("");
  preparePublishedFixture([sourceRevisionId]);

  const agents = (await (
    await page.request.get(`${root}/ai/agents`)
  ).json()) as { items: Array<{ revisionId: string }> };
  const skills = (await (
    await page.request.get(`${root}/ai/skills`)
  ).json()) as { items: Array<{ revisionId: string }> };
  const conversation = await page.request.post(`${root}/conversations`, {
    headers: { Origin: ORIGIN, "Idempotency-Key": `test-plan-chat-${marker}` },
    data: {
      message: "Design tests for the documented successful card payment rule.",
      modelAlias: "qwen-plus",
      sourceRevisionIds: [sourceRevisionId],
      documentRevisionIds: [],
      agentRevisionId: agents.items[0]!.revisionId,
      skillRevisionIds: [skills.items[0]!.revisionId],
    },
  });
  expect(
    conversation.status(),
    `conversation: ${await conversation.text()}`,
  ).toBe(202);
  const accepted = (await conversation.json()) as {
    conversationId: string;
    turnId: string;
  };
  let answerEvidenceSnapshotDigest = "";
  await expect
    .poll(
      async () => {
        const detail = (await (
          await page.request.get(
            `${root}/conversations/${accepted.conversationId}`,
          )
        ).json()) as {
          turns: Array<{
            state: string;
            inputSnapshotDigest: string;
            answerEvidenceSnapshotDigest?: string;
          }>;
        };
        const turn = detail.turns[0];
        if (turn?.state === "completed") {
          answerEvidenceSnapshotDigest =
            turn.answerEvidenceSnapshotDigest ?? "";
        }
        return answerEvidenceSnapshotDigest;
      },
      { timeout: 45_000 },
    )
    .not.toBe("");

  await page.goto("/");
  const generationResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/test-plans/generations"),
  );
  await page
    .getByRole("button", {
      name: /Generate Test Plan draft|生成测试计划草稿/u,
    })
    .click();
  const generation = await generationResponse;
  expect(generation.status(), `generation: ${await generation.text()}`).toBe(
    202,
  );
  const job = (await generation.json()) as {
    jobId: string;
    testPlanId: string;
    revisionId: string;
    deepLink: string;
  };
  const openDraft = page.getByRole("button", {
    name: /Open generated draft|打开生成的草稿/u,
  });
  await expect(openDraft).toBeVisible({ timeout: 45_000 });
  await openDraft.click();
  await expect(
    page.getByRole("heading", { name: "Generated Test Plan" }),
  ).toBeVisible();
  await expect(
    page.getByText(/1 source citations|1 条来源依据/u),
  ).toBeVisible();
  await page
    .getByLabel(/Plan objective|计划目标/u)
    .fill("Verify successful card checkout and review failures");
  const save = page.waitForResponse(
    (response) =>
      response.request().method() === "PATCH" &&
      response
        .url()
        .endsWith(`/test-plans/${job.testPlanId}/revisions/${job.revisionId}`),
  );
  await page.getByRole("button", { name: /Save draft|保存草稿/u }).click();
  expect((await save).status()).toBe(200);
  await page
    .getByLabel(/Review reason|评审理由/u)
    .fill("Reviewed the requirement, BDD result, and approved evidence.");
  const review = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response
        .url()
        .endsWith(
          `/test-plans/${job.testPlanId}/revisions/${job.revisionId}/reviews`,
        ),
  );
  await page
    .getByRole("button", { name: /Accept modified|修改后采纳/u })
    .click();
  expect((await review).status()).toBe(200);
  const publish = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response
        .url()
        .endsWith(
          `/test-plans/${job.testPlanId}/revisions/${job.revisionId}/publish`,
        ),
  );
  await page
    .getByRole("button", { name: /Approve and publish|批准并发布/u })
    .click();
  const publishResponse = await publish;
  expect(
    publishResponse.status(),
    `publish: ${await publishResponse.text()}`,
  ).toBe(200);
  await expect(
    page.getByRole("button", { name: /Published|已发布/u }),
  ).toBeDisabled();
});
