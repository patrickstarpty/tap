import {
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

import { describe, expect, it } from "vitest";

const MAX_CHUNK_BYTES = 500_000;
const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");

function javascriptFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      return javascriptFiles(path);
    }
    return entry.isFile() && entry.name.endsWith(".js") ? [path] : [];
  });
}

function circularImport(chunks: readonly string[]): string[] {
  const chunkNames = new Set(chunks.map((path) => basename(path)));
  const graph = new Map<string, string[]>();
  for (const chunk of chunks) {
    const imports = new Set<string>();
    const staticImport = /(?:\bfrom|\bimport)["']\.\/([^"'?#]+\.js)["']/gu;
    for (const match of readFileSync(chunk, "utf8").matchAll(staticImport)) {
      const importedChunk = match[1];
      if (importedChunk && chunkNames.has(importedChunk)) {
        imports.add(importedChunk);
      }
    }
    graph.set(basename(chunk), [...imports]);
  }

  const visited = new Set<string>();
  const visiting = new Set<string>();
  const findCycle = (chunk: string, path: readonly string[]): string[] => {
    if (visiting.has(chunk)) {
      const cycleStart = path.indexOf(chunk);
      return [...path.slice(cycleStart), chunk];
    }
    if (visited.has(chunk)) {
      return [];
    }

    visiting.add(chunk);
    for (const importedChunk of graph.get(chunk) ?? []) {
      const cycle = findCycle(importedChunk, [...path, chunk]);
      if (cycle.length > 0) {
        return cycle;
      }
    }
    visiting.delete(chunk);
    visited.add(chunk);
    return [];
  };

  for (const chunk of chunkNames) {
    const cycle = findCycle(chunk, []);
    if (cycle.length > 0) {
      return cycle;
    }
  }
  return [];
}

describe("production build", () => {
  it("keeps every minified JavaScript chunk below the Vite warning boundary", async () => {
    const outputDirectory = mkdtempSync(
      join(tmpdir(), "tap-athena-web-build-"),
    );

    try {
      const result = spawnSync(
        process.execPath,
        [
          resolve(webRoot, "node_modules/vite/bin/vite.js"),
          "build",
          "--emptyOutDir",
          "--outDir",
          outputDirectory,
        ],
        {
          cwd: webRoot,
          encoding: "utf8",
          env: {
            ...process.env,
            NODE_ENV: "production",
          },
        },
      );
      const buildOutput = `${result.stdout}${result.stderr}`;

      const chunks = javascriptFiles(outputDirectory);
      const oversizedChunks = chunks
        .map((path) => ({
          file: path.slice(outputDirectory.length + 1),
          size: statSync(path).size,
        }))
        .filter(({ size }) => size >= MAX_CHUNK_BYTES);
      const chunkWarnings = buildOutput.includes(
        "Some chunks are larger than 500 kB",
      );

      expect(result.status, buildOutput).toBe(0);
      expect(chunks.length).toBeGreaterThan(0);
      expect({
        chunkWarnings,
        circularImport: circularImport(chunks),
        oversizedChunks,
      }).toEqual({
        chunkWarnings: false,
        circularImport: [],
        oversizedChunks: [],
      });
    } finally {
      rmSync(outputDirectory, { force: true, recursive: true });
    }
  }, 20_000);
});
