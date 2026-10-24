import { Buffer } from "node:buffer";
import { readFileSync } from "node:fs";
import { inflateRawSync } from "node:zlib";

import { expect, test, type Locator, type Page } from "@playwright/test";

import { preparePublishedFixture } from "./publicationFixture";

const ORIGIN = "http://127.0.0.1:15173";

// Created by the journey; module-level (not `test.step`-scoped) so the
// `afterEach` cleanup still runs when the test fails partway through.
// Every journey spec removes what it created so the exact document-set
// assertion in `persistence.spec.ts`'s app-restart phase isn't perturbed.
const created: {
  agentId: string | null;
  conversationIds: string[];
  sourceId: string | null;
} = { agentId: null, conversationIds: [], sourceId: null };

test.afterEach(async ({ request }) => {
  const runtime = (await (
    await request.get("/api/v1/runtime-mode")
  ).json()) as { projectId: string };
  const root = `/api/v1/projects/${encodeURIComponent(runtime.projectId)}`;
  const headers = (key: string) => ({
    Origin: ORIGIN,
    "Idempotency-Key": `cleanup-agent-team-${key}`,
  });
  for (const conversationId of created.conversationIds) {
    const deleted = await request.delete(
      `${root}/conversations/${conversationId}`,
      { headers: headers(conversationId) },
    );
    expect(deleted.ok()).toBe(true);
  }
  created.conversationIds = [];
  if (created.agentId !== null) {
    const disabled = await request.post(
      `${root}/ai/agents/${created.agentId}/disable`,
      { headers: headers(created.agentId) },
    );
    expect(disabled.ok()).toBe(true);
    created.agentId = null;
  }
  if (created.sourceId !== null) {
    const sourceId = created.sourceId;
    const deleted = await request.delete(
      `${root}/knowledge/sources/${sourceId}`,
      { headers: headers(sourceId) },
    );
    expect(deleted.status()).toBe(204);
    await expect
      .poll(
        async () =>
          (await request.get(`${root}/knowledge/sources/${sourceId}`)).status(),
        { timeout: 45_000 },
      )
      .toBe(404);
    created.sourceId = null;
  }
});

/**
 * Reads every entry of a ZIP held in memory with Node built-ins only (no zip
 * library is reachable from this package): walk the central directory, then
 * inflate (method 8) or copy (method 0) each entry's data.
 */
function readZip(zip: Buffer): Map<string, Buffer> {
  let end = zip.length - 22;
  while (end >= 0 && zip.readUInt32LE(end) !== 0x06054b50) end -= 1;
  expect(end).toBeGreaterThanOrEqual(0);
  const count = zip.readUInt16LE(end + 10);
  let cursor = zip.readUInt32LE(end + 16);
  const entries = new Map<string, Buffer>();
  for (let index = 0; index < count; index += 1) {
    expect(zip.readUInt32LE(cursor)).toBe(0x02014b50);
    const method = zip.readUInt16LE(cursor + 10);
    const compressedSize = zip.readUInt32LE(cursor + 20);
    const nameLength = zip.readUInt16LE(cursor + 28);
    const extraLength = zip.readUInt16LE(cursor + 30);
    const commentLength = zip.readUInt16LE(cursor + 32);
    const localOffset = zip.readUInt32LE(cursor + 42);
    const name = zip.toString("utf8", cursor + 46, cursor + 46 + nameLength);
    const dataStart =
      localOffset +
      30 +
      zip.readUInt16LE(localOffset + 26) +
      zip.readUInt16LE(localOffset + 28);
    const data = zip.subarray(dataStart, dataStart + compressedSize);
    entries.set(name, method === 8 ? inflateRawSync(data) : Buffer.from(data));
    cursor += 46 + nameLength + extraLength + commentLength;
  }
  return entries;
}

async function openAgentsTab(page: Page, tab: string) {
  await page.getByRole("button", { name: "Agents", exact: true }).click();
  await page.getByRole("tab", { name: tab, exact: true }).click();
  await expect(
    page.getByRole("tab", { name: tab, selected: true }),
  ).toBeVisible();
}

function agentCard(page: Page, agentName: string): Locator {
  return page.locator(".tap-team-card").filter({ hasText: agentName });
}

/** Starts a new chat with `agentName` selected, sends `prompt`, waits for the
 * Turn to complete and returns the conversation id. */
async function askWithAgent(
  page: Page,
  root: string,
  agentName: string,
  sourceFilename: string,
  prompt: string,
): Promise<string> {
  await page.getByRole("button", { name: "Tapper", exact: true }).click();
  await page.getByRole("button", { name: "New chat", exact: true }).click();
  await page
    .getByRole("checkbox", { name: new RegExp(sourceFilename, "u") })
    .check();
  await page.getByRole("button", { name: "Add to message" }).click();
  await page.getByRole("menuitem", { name: "Use Agents" }).click();
  await page.getByRole("option", { name: agentName }).click();
  await page.getByRole("textbox", { name: "Message Tapper" }).fill(prompt);
  const accepted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === `${root}/conversations`,
  );
  await page.getByRole("button", { name: "Send" }).click();
  const response = await accepted;
  expect(response.status()).toBe(202);
  const { conversationId } = (await response.json()) as {
    conversationId: string;
  };
  created.conversationIds.push(conversationId);
  await expect
    .poll(
      async () => {
        const detail = await page.request.get(
          `${root}/conversations/${conversationId}`,
        );
        const body = (await detail.json()) as { turns: { state: string }[] };
        return body.turns[0]?.state;
      },
      { timeout: 45_000 },
    )
    .toBe("completed");
  return conversationId;
}

test("an Agent joins the team, serves two turns, gets a thumbs-down that its owner resolves, and appears in the report export", async ({
  page,
}) => {
  const runtime = (await (
    await page.request.get("/api/v1/runtime-mode")
  ).json()) as { projectId: string; actorId: string };
  const root = `/api/v1/projects/${encodeURIComponent(runtime.projectId)}`;
  const aiRoot = `${root}/ai`;

  // Evidence source: the deterministic gateway answers inline (a completed
  // Turn) only when a published source is selected.
  const sourceFilename = `agent-team-evidence-${Date.now()}.md`;
  const upload = await page.request.post(`${root}/knowledge/sources`, {
    headers: {
      Origin: ORIGIN,
      "Idempotency-Key": `agent-team-source-${Date.now()}`,
    },
    multipart: {
      upload: {
        name: sourceFilename,
        mimeType: "text/markdown",
        buffer: Buffer.from(
          `# Agent team evidence ${Date.now()}\n\nClaims must list a claimant name, policy number and incident date.`,
        ),
      },
    },
  });
  expect(upload.status()).toBe(202);
  const sourceId = ((await upload.json()) as { source: { sourceId: string } })
    .source.sourceId;
  created.sourceId = sourceId;
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${root}/knowledge/sources/${sourceId}?limit=50`,
        );
        return ((await response.json()) as { readyCount: number }).readyCount;
      },
      { timeout: 45_000 },
    )
    .toBeGreaterThan(0);
  const detail = (await (
    await page.request.get(`${root}/knowledge/sources/${sourceId}?limit=50`)
  ).json()) as {
    documents: { items: Array<{ revisionId: string; status: string }> };
  };
  const revision = detail.documents.items.find(
    (item) => item.status === "ready",
  );
  expect(revision).toBeDefined();
  preparePublishedFixture([revision!.revisionId]);

  await page.goto("/");

  // Step 1: create the Agent in Configuration.
  await openAgentsTab(page, "Configuration");
  await page.getByRole("button", { name: "New agent", exact: true }).click();
  const agentDialog = page.getByRole("dialog", { name: "New agent" });
  await expect(agentDialog).toBeVisible();
  const agentName = `Team journey reviewer ${Date.now()}`;
  await agentDialog.getByLabel("Name", { exact: true }).fill(agentName);
  await agentDialog
    .getByLabel("Description", { exact: true })
    .fill("Answers claim intake questions for the team journey.");
  await agentDialog
    .getByLabel("System instruction", { exact: true })
    .fill("Answer claim intake questions from the selected sources.");
  const createPending = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === `${aiRoot}/agents`,
  );
  await agentDialog.getByRole("button", { name: "Save", exact: true }).click();
  const createResponse = await createPending;
  expect(createResponse.status()).toBe(201);
  const agent = (await createResponse.json()) as { agentId: string };
  created.agentId = agent.agentId;

  // Step 2: the new Agent joins the roster; set its job title in the
  // profile's Settings tab.
  await page.getByRole("tab", { name: "Team", exact: true }).click();
  await expect(agentCard(page, agentName)).toBeVisible();
  await agentCard(page, agentName).locator(".tap-team-card-name").click();
  const profile = page.getByRole("dialog", { name: agentName });
  await expect(profile).toBeVisible();
  await profile.getByRole("tab", { name: "Settings", exact: true }).click();
  const jobTitle = "Claim intake reviewer";
  await profile.getByLabel("Job title", { exact: true }).fill(jobTitle);
  const titlePending = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
        `${aiRoot}/agents/${agent.agentId}/profile` &&
      response.request().method() !== "GET",
  );
  await profile.getByRole("button", { name: "Save", exact: true }).click();
  expect((await titlePending).ok()).toBe(true);
  await profile.getByRole("button", { name: "Close", exact: true }).click();
  await expect(profile).toHaveCount(0);
  await expect(agentCard(page, agentName)).toContainText(jobTitle);

  // Step 3: two chats with the Agent, the first answer rated "Not helpful".
  const firstPrompt = `What must a claim list ${Date.now()}?`;
  await askWithAgent(page, root, agentName, sourceFilename, firstPrompt);
  const firstTurn = page.locator(".tap-turn").filter({ hasText: firstPrompt });
  const notHelpful = firstTurn.getByRole("button", {
    name: /^(Not helpful|没帮助)$/u,
  });
  const ratingSaved = page.waitForResponse(
    (response) =>
      response.request().method() === "PUT" &&
      new URL(response.url()).pathname.includes("/feedback"),
  );
  await notHelpful.click();
  expect((await ratingSaved).ok()).toBe(true);
  await expect(notHelpful).toHaveAttribute("aria-pressed", "true");
  await askWithAgent(
    page,
    root,
    agentName,
    sourceFilename,
    `Who reviews the claim ${Date.now()}?`,
  );

  // Step 4: Team tab shows the Agent's card with two runs and the current
  // identity among the people it served (fresh page: no cached roster).
  await page.goto("/");
  await openAgentsTab(page, "Team");
  const card = agentCard(page, agentName);
  await expect(card).toContainText(/Runs in period\s*2\b/u);
  await expect(card).toContainText(runtime.actorId);

  // Step 5: the owner resolves the pending feedback from the profile; the
  // History tab then records it.
  await card.locator(".tap-team-card-name").click();
  const reopened = page.getByRole("dialog", { name: agentName });
  await reopened
    .getByRole("tab", { name: "Pending feedback", exact: true })
    .click();
  await expect(reopened).toContainText(/Pending feedback: 1|待处理反馈：1/u);
  await reopened
    .getByRole("textbox")
    .fill("Reviewed in the agent team journey.");
  await reopened
    .getByRole("button", { name: "Mark as resolved", exact: true })
    .click();
  await expect(reopened).toContainText(/Pending feedback: 0|待处理反馈：0/u);
  await reopened.getByRole("tab", { name: "History", exact: true }).click();
  await expect(
    reopened.getByText(/^(Feedback resolved|处理差评)$/u),
  ).toBeVisible();
  await reopened.getByRole("button", { name: "Close", exact: true }).click();

  // Step 6: Report tab exports a zip with the three CSV files.
  await page.getByRole("tab", { name: "Report", exact: true }).click();
  const exportLink = page.getByRole("link", {
    name: "Export CSV",
    exact: true,
  });
  await expect(exportLink).toBeVisible();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    exportLink.click(),
  ]);
  const path = await download.path();
  const entries = readZip(readFileSync(path));
  expect([...entries.keys()].sort()).toEqual([
    "people.csv",
    "roster.csv",
    "trend.csv",
  ]);
  for (const content of entries.values())
    expect(content.length).toBeGreaterThan(0);
});
