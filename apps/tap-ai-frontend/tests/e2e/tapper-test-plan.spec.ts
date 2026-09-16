import { expect, test } from "@playwright/test";

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
      modelAlias: "tapper-chat",
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
  let inputSnapshotDigest = "";
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
          inputSnapshotDigest = turn.inputSnapshotDigest;
          answerEvidenceSnapshotDigest =
            turn.answerEvidenceSnapshotDigest ?? "";
        }
        return answerEvidenceSnapshotDigest;
      },
      { timeout: 45_000 },
    )
    .not.toBe("");

  const generation = await page.request.post(`${root}/test-plans/generations`, {
    headers: {
      Origin: ORIGIN,
      "Idempotency-Key": `test-plan-generation-${marker}`,
    },
    data: {
      conversationId: accepted.conversationId,
      turnId: accepted.turnId,
      inputSnapshotDigest,
      answerEvidenceSnapshotDigest,
      modelAlias: "tapper-chat",
      agentRevisionId: agents.items[0]!.revisionId,
      skillRevisionIds: [skills.items[0]!.revisionId],
      objective: "Verify successful card checkout",
    },
  });
  expect(generation.status(), `generation: ${await generation.text()}`).toBe(
    202,
  );
  const job = (await generation.json()) as {
    jobId: string;
    testPlanId: string;
    revisionId: string;
    deepLink: string;
  };
  await expect
    .poll(
      async () => {
        const status = (await (
          await page.request.get(`${root}/test-plans/generations/${job.jobId}`)
        ).json()) as { status: string };
        return status.status;
      },
      { timeout: 45_000 },
    )
    .toBe("DRAFT_READY");

  await page.goto(job.deepLink);
  await expect(
    page.getByRole("heading", { name: "Generated Test Plan" }),
  ).toBeVisible();
  await expect(
    page.getByText(/1 source citations|1 条来源依据/u),
  ).toBeVisible();
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
  expect((await publish).status()).toBe(200);
  await expect(
    page.getByRole("button", { name: /Published|已发布/u }),
  ).toBeDisabled();
});
