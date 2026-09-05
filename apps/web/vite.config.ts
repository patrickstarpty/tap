import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const MAX_VENDOR_GROUP_BYTES = 1_300_000;
const reactPackages = new Set(["react", "react-dom", "scheduler"]);
const dataClientPackages = new Set([
  "@tanstack/query-core",
  "@tanstack/react-query",
  "openapi-fetch",
]);
const markdownPackages = new Set(["react-markdown", "rehype-sanitize"]);

function dependencyPackage(moduleId: string): string | undefined {
  const normalizedId = moduleId.replaceAll("\\", "/");
  const marker = "/node_modules/";
  const packageStart = normalizedId.lastIndexOf(marker);
  if (packageStart === -1) {
    return undefined;
  }

  const packagePath = normalizedId.slice(packageStart + marker.length);
  const [firstSegment, secondSegment] = packagePath.split("/");
  if (firstSegment?.startsWith("@") && secondSegment) {
    return `${firstSegment}/${secondSegment}`;
  }
  return firstSegment;
}

function packageIsIn(moduleId: string, packages: ReadonlySet<string>): boolean {
  const packageName = dependencyPackage(moduleId);
  return packageName !== undefined && packages.has(packageName);
}

function isDesignSystemPackage(moduleId: string): boolean {
  const packageName = dependencyPackage(moduleId);
  return (
    packageName === "antd" ||
    packageName?.startsWith("@ant-design/") === true ||
    packageName?.startsWith("@rc-component/") === true
  );
}

function loopback(name: string, fallback: string): string {
  const value = process.env[name] ?? fallback;
  if (value !== "127.0.0.1") {
    throw new Error(`${name} must be the fixed loopback host`);
  }
  return value;
}

function port(name: string, fallback: number): number {
  const raw = process.env[name] ?? String(fallback);
  if (!/^[1-9][0-9]*$/u.test(raw)) {
    throw new Error(`${name} must be a canonical port`);
  }
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value > 65_535) {
    throw new Error(`${name} must be a canonical port`);
  }
  return value;
}

const apiHost = loopback("TAPPER_API_HOST", "127.0.0.1");
const apiPort = port("TAPPER_API_PORT", 8000);
const webHost = loopback("TAPPER_WEB_HOST", "127.0.0.1");
const webPort = port("TAPPER_WEB_PORT", 5173);
const apiProxy = {
  target: `http://${apiHost}:${String(apiPort)}`,
  changeOrigin: false,
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: "react-runtime",
              test: (moduleId) => packageIsIn(moduleId, reactPackages),
              priority: 40,
              includeDependenciesRecursively: true,
            },
            {
              name: "data-client",
              test: (moduleId) => packageIsIn(moduleId, dataClientPackages),
              priority: 30,
              includeDependenciesRecursively: true,
            },
            {
              name: "markdown",
              test: (moduleId) => packageIsIn(moduleId, markdownPackages),
              priority: 30,
              maxSize: MAX_VENDOR_GROUP_BYTES,
              includeDependenciesRecursively: true,
            },
            {
              name: "design-system",
              test: isDesignSystemPackage,
              priority: 20,
              maxSize: MAX_VENDOR_GROUP_BYTES,
              includeDependenciesRecursively: false,
            },
            {
              name: "vendor-shared",
              test: (moduleId) => dependencyPackage(moduleId) !== undefined,
              priority: 10,
              includeDependenciesRecursively: true,
            },
          ],
        },
      },
    },
  },
  server: {
    host: webHost,
    port: webPort,
    strictPort: true,
    proxy: {
      "/health": apiProxy,
      "/v1": apiProxy,
    },
  },
});
