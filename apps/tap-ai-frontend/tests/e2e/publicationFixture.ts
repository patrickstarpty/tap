import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

import { expect } from "@playwright/test";

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

export function preparePublishedFixture(revisionIds: string[]): PublicationFixture {
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
