import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import openapiTS, { astToString } from "openapi-typescript";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const openapiPath = resolve(scriptDirectory, "../../../contracts/openapi/api.json");
const outputPath = resolve(scriptDirectory, "../src/shared/api/generated/schema.ts");
const check = process.argv.slice(2).join(" ") === "--check";

async function generatedSchema() {
  return astToString(await openapiTS(pathToFileURL(openapiPath)));
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
      throw new Error("Generated OpenAPI TypeScript schema is out of date.");
    }
  } finally {
    await rm(temporaryDirectory, { recursive: true, force: true });
  }
}

await main();
