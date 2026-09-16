import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

test("the public capture command lists only AI-owned journeys", () => {
  const result = spawnSync(
    "corepack",
    ["pnpm", "run", "prototype:capture", "--list"],
    {
      cwd: fileURLToPath(new URL("..", import.meta.url)),
      encoding: "utf8",
    },
  );
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /captures the fresh Tapper conversation/);
  assert.doesNotMatch(
    result.stdout,
    /Low Code Automation|Test Management|floating context|linked generation|automation channel/,
  );
});
