import { expect, test } from "@playwright/test";

import type {
  SourceAccepted,
  SourceDetail,
} from "../../src/features/knowledge/api/types";
import { preparePublishedFixture } from "./publicationFixture";

const ORIGIN = "http://127.0.0.1:15173";

test("an uploaded source stays out of chat until its governed publication is current", async ({
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

  preparePublishedFixture([revision!.revisionId]);
  await page.reload();
  await page.getByRole("button", { name: "New chat" }).click();
  await expect(
    page.getByRole("checkbox", { name: new RegExp(filename, "u") }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Library", exact: true }).click();
  await page.getByRole("button", { name: `View ${filename}` }).click();
  const publishedDialog = page.getByRole("dialog", { name: filename });
  await publishedDialog
    .getByRole("button", { name: `Review ${filename}` })
    .click();
  await expect(
    publishedDialog.getByText("发布状态：已发布", { exact: false }),
  ).toBeVisible();
  await expect(
    publishedDialog.getByRole("heading", { name: "修订记录" }),
  ).toBeVisible();
});
