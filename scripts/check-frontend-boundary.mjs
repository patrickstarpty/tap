import { existsSync, readFileSync, readdirSync, realpathSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, relative, resolve } from "node:path";

const appRoot = realpathSync(process.cwd());
const ts = createRequire(join(appRoot, "package.json"))("typescript");
const sourceRoot = join(appRoot, "src");
const product = appRoot.endsWith("/tap-ai-frontend") ? "ai" : "tap";
const config = ts.readConfigFile(
  join(appRoot, "tsconfig.json"),
  ts.sys.readFile,
);
const options = ts.parseJsonConfigFileContent(
  config.config ?? {},
  ts.sys,
  appRoot,
).options;
const violations = [];

function scan(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const file = join(directory, entry.name);
    if (entry.isDirectory()) {
      scan(file);
      continue;
    }
    if (!/\.[cm]?[jt]sx?$/.test(file) || /\.(?:test|spec)\.[jt]sx?$/.test(file))
      continue;
    for (const imported of ts.preProcessFile(
      readFileSync(file, "utf8"),
      true,
      true,
    ).importedFiles) {
      const specifier = imported.fileName;
      const resolved = ts.resolveModuleName(specifier, file, options, ts.sys)
        .resolvedModule?.resolvedFileName;
      const target =
        resolved ??
        (specifier.startsWith(".")
          ? resolve(dirname(file), specifier)
          : specifier);
      const canonical = existsSync(target) ? realpathSync(target) : target;
      const foreignSource =
        /\/apps\/(?:web|tap-ai-frontend)\/src\//.test(canonical) &&
        !canonical.startsWith(`${sourceRoot}/`);
      const tapImplementation =
        product === "ai" &&
        /\/(?:legacy|automation)\/|\/prototype\/(?:testManagement\/|TestAnalytics|testAnalytics|TestQuality|TapperFloatingAssistant|floatingAssistantModel|artifacts\/(?:fixtures|state|model))/.test(
          canonical,
        );
      if (foreignSource || tapImplementation) {
        violations.push(
          `${relative(appRoot, file)} -> ${specifier}: ${foreignSource ? "no-cross-product-source" : "no-tap-implementation-in-ai"}`,
        );
      }
    }
  }
}

scan(sourceRoot);
if (violations.length) {
  process.stderr.write(`${violations.join("\n")}\n`);
  process.exitCode = 1;
} else {
  process.stdout.write(
    `${product === "ai" ? "TAP AI" : "TAP"} frontend product boundary passed.\n`,
  );
}
