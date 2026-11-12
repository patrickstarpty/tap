import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: "./test-results",
  preserveOutput:
    process.env.TAPPER_BROWSER_CORE_LCA_BRIDGE === "1" ||
    process.env.TAPPER_BROWSER_CORE_LCA_INLINE_REAL === "1"
      ? "always"
      : "failures-only",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  timeout: 240_000,
  expect: { timeout: 10_000 },
  use: {
    ...devices["Desktop Chrome"],
    baseURL: process.env.TAPPER_WEB_BASE_URL ?? "http://127.0.0.1:15173",
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
    trace:
      process.env.TAPPER_BROWSER_CORE_LCA_BRIDGE === "1" ||
      process.env.TAPPER_BROWSER_CORE_LCA_INLINE_REAL === "1"
        ? "on"
        : "retain-on-failure",
    video:
      process.env.TAPPER_BROWSER_CORE_LCA_BRIDGE === "1" ||
      process.env.TAPPER_BROWSER_CORE_LCA_INLINE_REAL === "1"
        ? "on"
        : "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
