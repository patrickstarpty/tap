import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: "prototype-states.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "line",
  use: {
    baseURL: process.env.TAP_PROTOTYPE_BASE_URL ?? "http://127.0.0.1:15176",
    viewport: { width: 1280, height: 720 },
    deviceScaleFactor: 2,
    trace: "retain-on-failure",
  },
  webServer: {
    command: "corepack pnpm dev --port 15176",
    url: "http://127.0.0.1:15176/prototype",
    reuseExistingServer: true,
  },
});
