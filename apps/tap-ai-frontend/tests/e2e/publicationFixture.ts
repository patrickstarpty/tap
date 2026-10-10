import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

import { expect, type Page } from "@playwright/test";

interface PublicationFixture {
  publicationId: string;
  sourceRevisionIds: string[];
  approvedItemIds: string[];
  generation: string;
  editorActorIds: string[];
  reviewerActorId: string;
  publishedBy: string;
  status: string;
}

export function preparePublishedFixture(
  revisionIds: string[],
): PublicationFixture {
  expect(revisionIds.length).toBeGreaterThan(0);
  const root = resolve(process.cwd(), "../..");
  const output = execFileSync(
    "uv",
    [
      "run",
      "--project",
      "apps/tap-ai-backend",
      "python",
      "scripts/prepare-tapper-e2e-publication.py",
      ...revisionIds,
    ],
    { cwd: root, encoding: "utf8", timeout: 30_000 },
  );
  const publication = JSON.parse(output) as PublicationFixture;
  expect(publication.publicationId).toBeTruthy();
  expect(publication.status).toBe("published");
  expect(publication.sourceRevisionIds).toEqual(
    expect.arrayContaining(revisionIds),
  );
  expect(publication.approvedItemIds.length).toBeGreaterThan(0);
  expect(publication.generation).toBeTruthy();
  expect(publication.editorActorIds).toEqual(["tapper-e2e-fixture-editor"]);
  expect(publication.reviewerActorId).toBe("tapper-e2e-fixture-reviewer");
  expect(publication.publishedBy).toBe("tapper-e2e-fixture-publisher");
  return publication;
}

interface ProjectGraphView {
  graphVersion?: number | null;
  status: "EMPTY" | "MERGING" | "READY" | "FAILED";
  extractingRevisionIds?: string[];
  partialRevisionIds?: string[];
}

/**
 * Polls `GET {root}/knowledge/graph/project` until the project graph is
 * `READY` and none of `revisionIds` are still `EXTRACTING` or `PARTIAL`
 * (the response's `extractingRevisionIds` / `partialRevisionIds`), then
 * returns the resulting `graphVersion` as a string. Replaces polling the
 * retired fragment-scoped `GET /knowledge/graph/snapshots` route.
 */
export async function waitForProjectGraph(
  page: Page,
  root: string,
  revisionIds: string[],
  timeout = 60_000,
): Promise<string> {
  let graphVersion: string = "";
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${root}/knowledge/graph/project`,
        );
        if (response.status() !== 200) return false;
        const body = (await response.json()) as ProjectGraphView;
        const pending = new Set([
          ...(body.extractingRevisionIds ?? []),
          ...(body.partialRevisionIds ?? []),
        ]);
        const ready =
          body.status === "READY" &&
          revisionIds.every((revisionId) => !pending.has(revisionId));
        if (ready) graphVersion = String(body.graphVersion ?? "");
        return ready;
      },
      { timeout },
    )
    .toBe(true);
  return graphVersion;
}

export function approveExistingReviewFixture(
  reviewId: string,
  revisionId: string,
): void {
  const root = resolve(process.cwd(), "../..");
  const output = execFileSync(
    "uv",
    [
      "run",
      "--project",
      "apps/tap-ai-backend",
      "python",
      "scripts/prepare-tapper-e2e-publication.py",
      "--approve-existing",
      reviewId,
      revisionId,
    ],
    { cwd: root, encoding: "utf8", timeout: 30_000 },
  );
  const approved = JSON.parse(output) as {
    reviewId: string;
    reviewerActorId: string;
    status: string;
  };
  expect(approved).toMatchObject({
    reviewId,
    reviewerActorId: "tapper-e2e-fixture-reviewer",
    status: "approved",
  });
}
