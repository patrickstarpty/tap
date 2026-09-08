import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

const ORIGIN = "http://127.0.0.1:15173";
const DOCX =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

test("Library rejects hostile uploads and accepts a subsequent safe document", async ({
  page,
}) => {
  const directory = process.env.TAPPER_E2E_HOSTILE_DIR;
  expect(directory).toBeTruthy();
  await page.goto("/");
  const runtime = await page.request.get("/api/v1/runtime-mode");
  const { projectId } = (await runtime.json()) as { projectId: string };
  const path = `/api/v1/projects/${encodeURIComponent(projectId)}/knowledge/documents`;
  const ids: string[] = [];

  async function upload(page: Page, name: string): Promise<string> {
    await page.getByRole("button", { name: "Library", exact: true }).click();
    await page.getByRole("button", { name: "Add source", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Add source" });
    await dialog.getByLabel("Source file", { exact: true }).setInputFiles({
      name: `security-${name}`,
      mimeType: DOCX,
      buffer: await readFile(`${directory}/${name}`),
    });
    const pending = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === path,
    );
    await dialog
      .getByRole("button", { name: "Add source", exact: true })
      .click();
    const accepted = await pending;
    expect(accepted.status()).toBe(202);
    const result = (await accepted.json()) as {
      document: { documentId: string };
    };
    ids.push(result.document.documentId);
    return result.document.documentId;
  }

  try {
    const rejected = await upload(page, "external.docx");
    await expect
      .poll(
        async () => {
          const response = await page.request.get(`${path}/${rejected}`);
          const detail = (await response.json()) as {
            status: string;
            errorCode: string;
          };
          const serialized = JSON.stringify(detail);
          expect(serialized).not.toMatch(
            /192\.0\.2\.1|private-secret|\/opt\/parser|Traceback/,
          );
          return `${detail.status}:${detail.errorCode}`;
        },
        { timeout: 45_000 },
      )
      .toBe("failed:invalid-document");
    const valid = await upload(page, "valid.docx");
    await expect
      .poll(
        async () => {
          const response = await page.request.get(`${path}/${valid}`);
          return ((await response.json()) as { status: string }).status;
        },
        { timeout: 45_000 },
      )
      .toBe("ready");
    expect((await page.request.get("/health/ready")).status()).toBe(200);
  } finally {
    for (const id of ids) {
      expect(
        (
          await page.request.delete(`${path}/${id}`, {
            headers: { Origin: ORIGIN },
          })
        ).status(),
      ).toBe(204);
      await expect
        .poll(async () => (await page.request.get(`${path}/${id}`)).status(), {
          timeout: 45_000,
        })
        .toBe(404);
    }
  }
});
