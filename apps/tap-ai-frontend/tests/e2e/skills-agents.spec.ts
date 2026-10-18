import { Buffer } from "node:buffer";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

import { preparePublishedFixture } from "./publicationFixture";

const ORIGIN = "http://127.0.0.1:15173";
const SKILL_NAME = "claim-intake-checklist";
const SKILL_MARKER = "[e2e-skill:claim-intake]";
const FIXTURE_ROOT = resolve(
  process.cwd(),
  "tests/e2e/fixtures/skills/claim-intake-checklist",
);

// -- Minimal STORED-only ZIP writer ------------------------------------------
//
// No zip library is reachable from `apps/tap-ai-frontend`'s own dependency
// tree: `jszip` exists only as `docx`'s private transitive dependency (see
// `pnpm-lock.yaml`), nested under `docx`'s own `node_modules` rather than
// hoisted anywhere this package can `import` it. Rather than add a new
// dependency for four small fixture files, this builds the handful of bytes
// a stored-entry (uncompressed) ZIP needs with Node built-ins only --
// `scan_zip` (`apps/tap-ai-backend/.../ai/application/skill_package.py`)
// explicitly accepts `zipfile.ZIP_STORED` alongside `ZIP_DEFLATED`.
const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let value = n;
    for (let bit = 0; bit < 8; bit += 1) {
      value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    }
    table[n] = value;
  }
  return table;
})();

function crc32(data: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of data) {
    crc = CRC_TABLE[(crc ^ byte) & 0xff]! ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function buildStoredZip(entries: { path: string; data: Buffer }[]): Buffer {
  const localParts: Buffer[] = [];
  const centralParts: Buffer[] = [];
  let offset = 0;
  for (const { path, data } of entries) {
    const nameBuffer = Buffer.from(path, "utf8");
    const crc = crc32(data);

    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0, 6);
    local.writeUInt16LE(0, 8);
    local.writeUInt16LE(0, 10);
    local.writeUInt16LE(0, 12);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBuffer.length, 26);
    local.writeUInt16LE(0, 28);
    localParts.push(local, nameBuffer, data);

    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0, 8);
    central.writeUInt16LE(0, 10);
    central.writeUInt16LE(0, 12);
    central.writeUInt16LE(0, 14);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(nameBuffer.length, 28);
    central.writeUInt16LE(0, 30);
    central.writeUInt16LE(0, 32);
    central.writeUInt16LE(0, 34);
    central.writeUInt16LE(0, 36);
    central.writeUInt32LE(0, 38);
    central.writeUInt32LE(offset, 42);
    centralParts.push(central, nameBuffer);

    offset += local.length + nameBuffer.length + data.length;
  }
  const centralSize = centralParts.reduce((sum, part) => sum + part.length, 0);
  const centralOffset = offset;
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(0, 4);
  end.writeUInt16LE(0, 6);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(centralSize, 12);
  end.writeUInt32LE(centralOffset, 16);
  end.writeUInt16LE(0, 20);
  return Buffer.concat([...localParts, ...centralParts, end]);
}

function buildFixtureZip(): Buffer {
  const relativePaths = [
    "SKILL.md",
    "references/glossary.md",
    "scripts/check_fields.py",
    "assets/template.txt",
  ];
  // A single shared top-level directory so the backend's
  // `_strip_common_prefix` reduces every entry back to the bare
  // `SKILL.md`-relative layout, exactly like a user zipping the skill
  // folder itself (not its contents) before uploading it.
  const entries = relativePaths.map((relativePath) => ({
    path: `${SKILL_NAME}/${relativePath}`,
    data: readFileSync(resolve(FIXTURE_ROOT, relativePath)),
  }));
  return buildStoredZip(entries);
}

test("imports a Skill package, binds it to a new Agent, and uses it end to end", async ({
  page,
}) => {
  const runtimeResponse = await page.request.get("/api/v1/runtime-mode");
  expect(runtimeResponse.status()).toBe(200);
  const runtime = (await runtimeResponse.json()) as { projectId: string };
  const root = `/api/v1/projects/${encodeURIComponent(runtime.projectId)}`;
  const aiRoot = `${root}/ai`;

  // Step 0: publish a Knowledge source -- the deterministic gateway only
  // echoes the `[e2e-skill:...]` marker on the inline (non-STRUCTURED)
  // answer path, which this question must take by having a published
  // source selected.
  const sourceFilename = `skill-agent-evidence-${Date.now()}.md`;
  const upload = await page.request.post(`${root}/knowledge/sources`, {
    headers: {
      Origin: ORIGIN,
      "Idempotency-Key": `skill-agent-source-${Date.now()}`,
    },
    multipart: {
      upload: {
        name: sourceFilename,
        mimeType: "text/markdown",
        buffer: Buffer.from(
          `# Claim intake evidence ${Date.now()}\n\nClaims must list a claimant name, policy number and incident date.`,
        ),
      },
    },
  });
  expect(upload.status()).toBe(202);
  const uploaded = (await upload.json()) as {
    source: { sourceId: string };
  };
  const createdSourceId = uploaded.source.sourceId;
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${root}/knowledge/sources/${createdSourceId}?limit=50`,
        );
        const detail = (await response.json()) as { readyCount: number };
        return detail.readyCount;
      },
      { timeout: 45_000 },
    )
    .toBeGreaterThan(0);
  const detailResponse = await page.request.get(
    `${root}/knowledge/sources/${createdSourceId}?limit=50`,
  );
  const sourceDetail = (await detailResponse.json()) as {
    documents: { items: Array<{ revisionId: string; status: string }> };
  };
  const revision = sourceDetail.documents.items.find(
    (item) => item.status === "ready",
  );
  expect(revision).toBeDefined();
  preparePublishedFixture([revision!.revisionId]);

  await page.goto("/");

  // Step 1: stage the zipped Skill package through the two-step import
  // dialog, upload via the file input (not the folder/paste inputs), and
  // confirm the preview labels a bundled script "saved, not executed" and
  // the referenced glossary "attached" before committing the import.
  await page.getByRole("button", { name: "Skills", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Skills", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Import skill", exact: true }).click();
  const stageDialog = page.getByRole("dialog", { name: "Import a skill" });
  await expect(stageDialog).toBeVisible();
  await stageDialog.getByLabel("Browse files", { exact: true }).setInputFiles({
    name: `${SKILL_NAME}.zip`,
    mimeType: "application/zip",
    buffer: buildFixtureZip(),
  });
  const stagePending = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === `${aiRoot}/skills/imports`,
  );
  await stageDialog
    .getByRole("button", { name: "Preview", exact: true })
    .click();
  const staged = await stagePending;
  expect(staged.status()).toBe(201);
  const preview = (await staged.json()) as {
    importId: string;
    skills: Array<{ root: string; name: string }>;
  };
  expect(preview.skills.some((item) => item.name === SKILL_NAME)).toBe(true);

  const reviewDialog = page.getByRole("dialog", {
    name: "Review before import",
  });
  await expect(reviewDialog).toBeVisible();
  await expect(
    reviewDialog.getByText("Script · saved, not executed", { exact: true }),
  ).toBeVisible();
  await expect(
    reviewDialog.getByText("Reference · attached", { exact: true }),
  ).toBeVisible();

  const commitPending = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname ===
        `${aiRoot}/skills/imports/${preview.importId}/commit`,
  );
  await reviewDialog
    .getByRole("button", { name: "Import selected", exact: true })
    .click();
  const committed = await commitPending;
  expect(committed.status()).toBe(200);
  const commitResult = (await committed.json()) as {
    revisions: Array<{
      revisionId: string;
      skillId: string;
      displayName: string;
    }>;
  };
  const importedSkill = commitResult.revisions.find(
    (item) => item.displayName === SKILL_NAME,
  );
  expect(importedSkill).toBeDefined();

  // Step 2: build a new Agent bound to the imported Skill.
  await page.getByRole("button", { name: "Agents", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Agents", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "New agent", exact: true }).click();
  const agentDialog = page.getByRole("dialog", { name: "New agent" });
  await expect(agentDialog).toBeVisible();
  const agentName = `Claim intake reviewer ${Date.now()}`;
  await agentDialog.getByLabel("Name", { exact: true }).fill(agentName);
  await agentDialog
    .getByLabel("Description", { exact: true })
    .fill("Reviews claim intake submissions against the required fields.");
  await agentDialog
    .getByLabel("System instruction", { exact: true })
    .fill(
      "Use the claim intake checklist Skill to confirm every required field is present before answering.",
    );
  await agentDialog
    .getByRole("checkbox", { name: new RegExp(SKILL_NAME, "u") })
    .check();
  const agentCreatePending = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === `${aiRoot}/agents`,
  );
  await agentDialog.getByRole("button", { name: "Save", exact: true }).click();
  const agentCreated = await agentCreatePending;
  expect(agentCreated.status()).toBe(201);
  const agent = (await agentCreated.json()) as {
    revisionId: string;
    agentId: string;
    displayName: string;
  };
  expect(agent.displayName).toBe(agentName);

  // Step 3: start a new conversation, select the published source and the
  // new Agent, and ask a question.
  await page.getByRole("button", { name: "Tapper", exact: true }).click();
  await page.getByRole("button", { name: "New chat", exact: true }).click();
  await page
    .getByRole("checkbox", { name: new RegExp(sourceFilename, "u") })
    .check();
  await page.getByRole("button", { name: "Add to message" }).click();
  await page.getByRole("menuitem", { name: "Use Agents" }).click();
  await page.getByRole("option", { name: agentName }).click();

  const prompt = `What fields must a claim list ${Date.now()}?`;
  await page.getByRole("textbox", { name: "Message Tapper" }).fill(prompt);
  const acceptedResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === `${root}/conversations`,
  );
  await page.getByRole("button", { name: "Send" }).click();
  const acceptedResponse = await acceptedResponsePromise;
  expect(acceptedResponse.status()).toBe(202);
  const accepted = (await acceptedResponse.json()) as {
    conversationId: string;
    turnId: string;
  };

  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${root}/conversations/${accepted.conversationId}`,
        );
        const body = (await response.json()) as {
          turns: Array<{ state: string }>;
        };
        return body.turns[0]?.state;
      },
      { timeout: 45_000 },
    )
    .toBe("completed");

  // Step 4: the answer echoes the Skill marker (proving the Skill's
  // instructions reached the model prompt), and "Sources and settings"
  // names the Agent and the Skill revision used.
  const turn = page.locator(".tap-turn").filter({ hasText: prompt });
  await expect(turn).toContainText(SKILL_MARKER);

  const answerContext = turn.locator(".tap-answer-context");
  await answerContext
    .getByText(
      /Sources and settings used for this answer|本次回答使用的资料与配置/u,
    )
    .click();
  await expect(
    answerContext.getByText(new RegExp(agentName, "u")),
  ).toBeVisible();
  const skillLink = answerContext.getByRole("button", {
    name: new RegExp(SKILL_NAME, "u"),
  });
  await expect(skillLink).toBeVisible();
  await skillLink.click();
  const skillDetailDialog = page.getByRole("dialog", { name: SKILL_NAME });
  await expect(skillDetailDialog).toBeVisible();
  await skillDetailDialog.getByRole("tab", { name: "Metadata" }).click();
  await expect(
    skillDetailDialog.getByText(importedSkill!.revisionId, { exact: true }),
  ).toBeVisible();

  // Step 5: the bundled script downloads as an attachment, never executes.
  const downloadResponse = await page.request.get(
    `${aiRoot}/skills/${importedSkill!.revisionId}/files/scripts/check_fields.py`,
  );
  expect(downloadResponse.status()).toBe(200);
  expect(downloadResponse.headers()["content-disposition"]).toMatch(
    /^attachment;/u,
  );
});
