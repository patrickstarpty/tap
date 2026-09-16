import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
  symlinkSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

test("all product fixtures run with only the Tap AI dependency installation", () => {
  const repository = fileURLToPath(new URL("..", import.meta.url));
  const isolated = mkdtempSync(join(tmpdir(), "tap-ai-only-check-"));
  try {
    mkdirSync(join(isolated, "scripts"));
    mkdirSync(join(isolated, "apps/tap-ai-frontend"), { recursive: true });
    for (const name of [
      "check-frontend-boundary.mjs",
      "check-frontend-boundary.test.mjs",
    ]) {
      copyFileSync(
        join(repository, "scripts", name),
        join(isolated, "scripts", name),
      );
    }
    symlinkSync(
      join(repository, "apps/tap-ai-frontend/node_modules"),
      join(isolated, "apps/tap-ai-frontend/node_modules"),
    );
    assert.equal(existsSync(join(isolated, "apps/web/node_modules")), false);
    const env = { ...process.env };
    delete env.NODE_TEST_CONTEXT;
    const result = spawnSync(
      process.execPath,
      ["--test", join(isolated, "scripts/check-frontend-boundary.test.mjs")],
      { encoding: "utf8", env },
    );
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.match(
      result.stdout,
      /# pass 5\b/,
      "The isolated child must execute all five product fixtures.",
    );
  } finally {
    rmSync(isolated, { recursive: true, force: true });
  }
});
