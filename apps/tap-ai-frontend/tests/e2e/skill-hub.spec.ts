import { Buffer } from "node:buffer";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { inflateRawSync } from "node:zlib";

import { expect, test } from "@playwright/test";

import { preparePublishedFixture } from "./publicationFixture";

const ORIGIN = "http://127.0.0.1:15173";
const FIXTURE_NAME = "claim-intake-checklist";
const FIXTURE_ROOT = resolve(
  process.cwd(),
  "tests/e2e/fixtures/skills/claim-intake-checklist",
);

// Created by the journey; module-level (not `test.step`-scoped) so the
// `afterEach` cleanup still runs when the test fails partway through.
// Every journey spec removes what it created so the exact document-set
// assertion in `persistence.spec.ts`'s app-restart phase isn't perturbed;
// Skills cannot be deleted, so the imported one is disabled instead.
const created: {
  skillId: string | null;
  conversationIds: string[];
  sourceId: string | null;
} = { skillId: null, conversationIds: [], sourceId: null };

test.afterEach(async ({ page }) => {
  const request = page.request;
  // Each step runs independently; failures are collected and thrown at the
  // end so one broken step never leaks the Skill or the source -- including
  // the runtime-mode lookup itself, which every later step depends on.
  const errors: string[] = [];
  let root = "";
  const step = async (name: string, run: () => Promise<void>) => {
    if (name !== "runtime-mode" && root === "") {
      errors.push(`${name}: skipped, no project id`);
      return;
    }
    try {
      await run();
    } catch (error) {
      errors.push(`${name}: ${String(error)}`);
    }
  };
  const headers = (key: string) => ({
    Origin: ORIGIN,
    "Idempotency-Key": `cleanup-skill-hub-${key}`,
  });
  await step("runtime-mode", async () => {
    const runtime = (await (
      await request.get("/api/v1/runtime-mode")
    ).json()) as { projectId: string };
    root = `/api/v1/projects/${encodeURIComponent(runtime.projectId)}`;
  });
  const { skillId, conversationIds, sourceId } = created;
  created.skillId = null;
  created.conversationIds = [];
  created.sourceId = null;
  if (sourceId !== null)
    await step("source", async () => {
      const deleted = await request.delete(
        `${root}/knowledge/sources/${sourceId}`,
        { headers: headers(sourceId) },
      );
      expect(deleted.status()).toBe(204);
      await expect
        .poll(
          async () =>
            (
              await request.get(`${root}/knowledge/sources/${sourceId}`)
            ).status(),
          { timeout: 45_000 },
        )
        .toBe(404);
    });
  for (const conversationId of conversationIds)
    await step(`conversation ${conversationId}`, async () => {
      const deleted = await request.delete(
        `${root}/conversations/${conversationId}`,
        { headers: headers(conversationId) },
      );
      expect(deleted.ok()).toBe(true);
    });
  if (skillId !== null)
    await step("skill", async () => {
      const disabled = await request.post(
        `${root}/ai/skills/${encodeURIComponent(skillId)}/disable`,
        { headers: headers(skillId) },
      );
      expect(disabled.ok()).toBe(true);
    });
  if (errors.length > 0) throw new Error(errors.join("\n"));
});

// -- ZIP helpers (Node built-ins only; no zip library is reachable from this
// package, see `skills-agents.spec.ts`) -------------------------------------

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

/** A stored-entry (uncompressed) ZIP, which the backend's `scan_zip`
 * accepts alongside deflated entries. */
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
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBuffer.length, 26);
    localParts.push(local, nameBuffer, data);

    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(nameBuffer.length, 28);
    central.writeUInt32LE(offset, 42);
    centralParts.push(central, nameBuffer);

    offset += local.length + nameBuffer.length + data.length;
  }
  const centralSize = centralParts.reduce((sum, part) => sum + part.length, 0);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(centralSize, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...localParts, ...centralParts, end]);
}

/**
 * The fixture Skill package, zipped under one top-level folder (as a person
 * zipping the Skill folder would), with its `SKILL.md` `name` made unique to
 * this run: the library then holds exactly one Skill of that name, which
 * no earlier journey (`skills-agents.spec.ts` imports the same package) or
 * earlier run has used, so its usage starts at zero.
 */
function buildFixtureZip(skillName: string): Buffer {
  const relativePaths = [
    "SKILL.md",
    "references/glossary.md",
    "scripts/check_fields.py",
    "assets/template.txt",
  ];
  const entries = relativePaths.map((relativePath) => {
    let data = readFileSync(resolve(FIXTURE_ROOT, relativePath));
    if (relativePath === "SKILL.md") {
      const original = data.toString("utf8");
      const renamed = original.replace(
        new RegExp(`^name: ${FIXTURE_NAME}$`, "mu"),
        `name: ${skillName}`,
      );
      expect(renamed).not.toBe(original);
      data = Buffer.from(renamed, "utf8");
    }
    return { path: `${skillName}/${relativePath}`, data };
  });
  return buildStoredZip(entries);
}

/** Every entry of a ZIP held in memory: walk the central directory, then
 * inflate (method 8) or copy (method 0) each entry's data. */
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

/** Rows of an exported CSV: BOM and `#` comment lines dropped, quoted
 * fields (with `""` escapes, commas and newlines) unwrapped. */
function parseCsv(content: Buffer): string[][] {
  const text = content
    .toString("utf8")
    .replace(/^﻿/u, "")
    .split("\n")
    .filter((line) => !line.startsWith("#"))
    .join("\n");
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index]!;
    if (quoted) {
      if (char === '"' && text[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        field += char;
      }
    } else if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      row.push(field);
      field = "";
    } else if (char === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else if (char !== "\r") {
      field += char;
    }
  }
  if (field !== "" || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

/** The CSV's rows as header-keyed records. */
function csvRecords(content: Buffer): Record<string, string>[] {
  const [header, ...rows] = parseCsv(content);
  expect(header).toBeDefined();
  return rows.map((cells) =>
    Object.fromEntries(header!.map((key, index) => [key, cells[index] ?? ""])),
  );
}

test("imports a Skill, activates it automatically in a chat, and counts it in usage and the CSV export", async ({
  page,
}) => {
  const runtimeResponse = await page.request.get("/api/v1/runtime-mode");
  expect(runtimeResponse.status()).toBe(200);
  const runtime = (await runtimeResponse.json()) as { projectId: string };
  const root = `/api/v1/projects/${encodeURIComponent(runtime.projectId)}`;
  const aiRoot = `${root}/ai`;
  const skillName = `${FIXTURE_NAME}-${Date.now()}`;

  // Step 0: publish a Knowledge source -- the deterministic gateway runs
  // the tool loop (and so the `[e2e-tool:skill.activate:...]` marker) on
  // the inline answer path, which this question takes by having a
  // published source selected.
  const sourceFilename = `skill-hub-evidence-${Date.now()}.md`;
  const upload = await page.request.post(`${root}/knowledge/sources`, {
    headers: {
      Origin: ORIGIN,
      "Idempotency-Key": `skill-hub-source-${Date.now()}`,
    },
    multipart: {
      upload: {
        name: sourceFilename,
        mimeType: "text/markdown",
        buffer: Buffer.from(
          `# Skill hub evidence ${Date.now()}\n\nClaims must list a claimant name, policy number and incident date.`,
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
  const sourceDetail = (await (
    await page.request.get(`${root}/knowledge/sources/${sourceId}?limit=50`)
  ).json()) as {
    documents: { items: Array<{ revisionId: string; status: string }> };
  };
  const revision = sourceDetail.documents.items.find(
    (item) => item.status === "ready",
  );
  expect(revision).toBeDefined();
  preparePublishedFixture([revision!.revisionId]);

  await page.goto("/");

  // Step 1: import the fixture Skill package through the two-step dialog.
  await page.getByRole("button", { name: "Skills", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Skills", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Import skill", exact: true })
    .first()
    .click();
  const stageDialog = page.getByRole("dialog", { name: "Import a skill" });
  await expect(stageDialog).toBeVisible();
  await stageDialog.getByLabel("Browse files", { exact: true }).setInputFiles({
    name: `${skillName}.zip`,
    mimeType: "application/zip",
    buffer: buildFixtureZip(skillName),
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
    skills: Array<{ name: string }>;
  };
  expect(preview.skills.map((item) => item.name)).toEqual([skillName]);
  const reviewDialog = page.getByRole("dialog", {
    name: "Review before import",
  });
  await expect(reviewDialog).toBeVisible();
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
    revisions: Array<{ skillId: string; displayName: string }>;
  };
  const importedSkill = commitResult.revisions.find(
    (item) => item.displayName === skillName,
  );
  expect(importedSkill).toBeDefined();
  const skillId = importedSkill!.skillId;
  created.skillId = skillId;

  // Step 2: the Skill's card appears in the library.
  const library = page.getByRole("list", { name: "Skill library" });
  await expect(
    library.getByRole("listitem", { name: skillName, exact: true }),
  ).toBeVisible();

  // Step 3: a new chat with the published source selected and no Skill or
  // Agent picked; the deterministic gateway turns the marker into one
  // `skill.activate` call for the imported Skill.
  await page.getByRole("button", { name: "Tapper", exact: true }).click();
  await page.getByRole("button", { name: "New chat", exact: true }).click();
  await page
    .getByRole("checkbox", { name: new RegExp(sourceFilename, "u") })
    .check();
  const prompt = `What fields must a claim list ${Date.now()}? [e2e-tool:skill.activate:${skillName}]`;
  await page.getByRole("textbox", { name: "Message Tapper" }).fill(prompt);
  const acceptedPending = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === `${root}/conversations`,
  );
  await page.getByRole("button", { name: "Send" }).click();
  const acceptedResponse = await acceptedPending;
  expect(acceptedResponse.status()).toBe(202);
  const { conversationId } = (await acceptedResponse.json()) as {
    conversationId: string;
  };
  created.conversationIds.push(conversationId);
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${root}/conversations/${conversationId}`,
        );
        const body = (await response.json()) as { turns: { state: string }[] };
        return body.turns[0]?.state;
      },
      { timeout: 45_000 },
    )
    .toBe("completed");
  const conversation = (await (
    await page.request.get(`${root}/conversations/${conversationId}`)
  ).json()) as {
    turns: Array<{
      input: { skillsUsed?: Array<{ skillId: string; mode: string }> };
    }>;
  };
  // No manual or pinned Skill: the only one used is the automatic one.
  expect(conversation.turns[0]!.input.skillsUsed).toEqual([
    expect.objectContaining({ skillId, mode: "auto" }),
  ]);

  // Step 4: "Sources and settings" lists the Skill, tagged Auto.
  const turn = page.locator(".tap-turn").filter({ hasText: prompt });
  const answerContext = turn.locator(".tap-answer-context");
  await answerContext
    .getByText("Sources and settings used for this answer", { exact: true })
    .click();
  const usedSkill = answerContext
    .getByRole("list", { name: "Skills used" })
    .getByRole("listitem")
    .filter({ hasText: skillName });
  await expect(usedSkill).toHaveCount(1);
  await expect(usedSkill.getByText("Auto", { exact: true })).toBeVisible();

  // Step 5: the Skill's detail counts one automatic activation in the
  // library's current period (a fresh page: no cached library data).
  await page.goto("/");
  await page.getByRole("button", { name: "Skills", exact: true }).click();
  await library
    .getByRole("listitem", { name: skillName, exact: true })
    .getByRole("button", { name: skillName, exact: true })
    .click();
  const detail = page.getByRole("dialog", { name: skillName });
  await expect(detail).toBeVisible();
  await detail.getByRole("tab", { name: "Usage", exact: true }).click();
  await expect(
    detail.getByRole("tab", { name: "Usage", selected: true }),
  ).toBeVisible();
  // The per-mode tiles sit with the note on how activations are counted.
  const modeTiles = detail
    .getByRole("tabpanel")
    .locator("section")
    .filter({ hasText: "counts once for each skill" });
  await expect(modeTiles).toContainText(/Automatic\s*1(?!\d)/u);
  await expect(modeTiles).toContainText(/Manual\s*0(?!\d)/u);
  await detail.getByRole("button", { name: "Close", exact: true }).click();
  await expect(detail).toHaveCount(0);

  // Step 6: the CSV export is a zip of library.csv and usage.csv; the
  // Skill's library row counts the automatic activation.
  const exportLink = page.getByRole("link", {
    name: "Export CSV",
    exact: true,
  });
  await expect(exportLink).toBeVisible();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    exportLink.click(),
  ]);
  const entries = readZip(readFileSync(await download.path()));
  expect([...entries.keys()].sort()).toEqual(["library.csv", "usage.csv"]);
  const libraryRows = csvRecords(entries.get("library.csv")!);
  const skillRow = libraryRows.find((row) => row.skill_id === skillId);
  expect(skillRow).toBeDefined();
  expect(skillRow!.name).toBe(skillName);
  expect(skillRow!.activations_auto).toBe("1");
  const usageRows = csvRecords(entries.get("usage.csv")!);
  expect(
    usageRows.filter((row) => row.skill_id === skillId && row.mode === "auto"),
  ).toEqual([expect.objectContaining({ runs: "1" })]);
});
