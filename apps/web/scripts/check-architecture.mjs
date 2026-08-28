import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const webRoot = fileURLToPath(new URL("..", import.meta.url));
const commonArguments = ["--config", "dependency-cruiser.cjs"];

function cruise(target) {
  return spawnSync("depcruise", [target, ...commonArguments], {
    cwd: webRoot,
    encoding: "utf8",
  });
}

function output(result) {
  return `${result.stdout ?? ""}${result.stderr ?? ""}`;
}

const sourceResult = cruise("src");
if (sourceResult.status !== 0) {
  process.stderr.write(output(sourceResult));
  process.exit(sourceResult.status ?? 1);
}

const negativeResult = cruise("architecture-fixtures/src");
const negativeOutput = output(negativeResult);
if (
  negativeResult.status === 0 ||
  !negativeOutput.includes("no-feature-to-feature")
) {
  process.stderr.write(negativeOutput);
  process.stderr.write(
    "Expected the cross-feature architecture fixture to fail no-feature-to-feature.\n",
  );
  process.exit(1);
}

process.stdout.write(
  "Dependency boundaries passed, including the negative fixture.\n",
);
