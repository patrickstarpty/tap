import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createRequire } from "node:module";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const openapiPath = resolve(
  scriptDirectory,
  "../../../contracts/openapi/tap-api.json",
);
const outputPath = resolve(
  scriptDirectory,
  "../src/shared/api/generated/schema.ts",
);
const check = process.argv.slice(2).join(" ") === "--check";

function resolveGenerator() {
  try {
    return createRequire(import.meta.url).resolve("openapi-typescript");
  } catch {
    // Existing worktrees may predate TAP Web's declared generator dependency.
    // Bootstrap links the local dependency; this only reuses the same pinned
    // workspace tool until that non-source cache is refreshed.
    return createRequire(
      resolve(scriptDirectory, "../../tap-ai-frontend/package.json"),
    ).resolve("openapi-typescript");
  }
}

async function generatedSchema() {
  const generator = await import(
    pathToFileURL(resolveGenerator())
  );
  const openapiTS =
    typeof generator.default === "function"
      ? generator.default
      : generator.default.default;
  const astToString = generator.astToString ?? generator.default.astToString;
  return astToString(
    await openapiTS(pathToFileURL(openapiPath), {
      defaultNonNullable: false,
    }),
  );
}

async function main() {
  const schema = await generatedSchema();
  if (!check) {
    await mkdir(dirname(outputPath), { recursive: true });
    await writeFile(outputPath, schema, "utf8");
    return;
  }

  const temporaryDirectory = await mkdtemp(join(tmpdir(), "tap-openapi-"));
  const temporaryPath = join(temporaryDirectory, "schema.ts");
  try {
    await writeFile(temporaryPath, schema, "utf8");
    const [generated, committed] = await Promise.all([
      readFile(temporaryPath),
      readFile(outputPath).catch(() => null),
    ]);
    if (committed === null || !generated.equals(committed)) {
      throw new Error("Generated TAP OpenAPI TypeScript schema is out of date.");
    }
  } finally {
    await rm(temporaryDirectory, { recursive: true, force: true });
  }
}

await main();
