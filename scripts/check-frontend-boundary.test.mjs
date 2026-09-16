import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  symlinkSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const repository = fileURLToPath(new URL("..", import.meta.url));
const checker = join(repository, "scripts/check-frontend-boundary.mjs");

for (const [product, source, expected] of [
  [
    "tap-ai-frontend",
    'import "../../web/src/foreign";',
    "no-cross-product-source",
  ],
  [
    "web",
    'export * from "../../tap-ai-frontend/src/foreign";',
    "no-cross-product-source",
  ],
  [
    "tap-ai-frontend",
    'import("./prototype/automation/AutomationWorkspace");',
    "no-tap-implementation-in-ai",
  ],
  ["tap-ai-frontend", 'import "./owned";', null],
  ["web", 'import "./owned";', null],
]) {
  test(`${product}: ${source}`, () => {
    const fixture = mkdtempSync(join(tmpdir(), "tap-product-boundary-"));
    try {
      const app = join(fixture, "apps", product);
      for (const [name, content] of [
        [`apps/${product}/src/main.ts`, source],
        [`apps/${product}/src/owned.ts`, "export const owned = true;"],
        [
          `apps/${product}/src/prototype/automation/AutomationWorkspace.ts`,
          "export const automation = true;",
        ],
        ["apps/web/src/foreign.ts", "export const foreign = true;"],
        ["apps/tap-ai-frontend/src/foreign.ts", "export const foreign = true;"],
        [
          `apps/${product}/tsconfig.json`,
          JSON.stringify({
            compilerOptions: { moduleResolution: "bundler", module: "esnext" },
          }),
        ],
      ]) {
        const path = join(fixture, name);
        mkdirSync(dirname(path), { recursive: true });
        writeFileSync(path, content);
      }
      symlinkSync(
        join(repository, "apps", product, "node_modules"),
        join(app, "node_modules"),
      );
      const result = spawnSync(process.execPath, [checker], {
        cwd: app,
        encoding: "utf8",
      });
      assert.equal(result.status, expected === null ? 0 : 1, result.stderr);
      if (expected !== null) assert.match(result.stderr, new RegExp(expected));
    } finally {
      rmSync(fixture, { recursive: true, force: true });
    }
  });
}
