import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "line",
  use: {
    baseURL: process.env.TAP_INSIGHTS_E2E_BASE_URL ?? "http://127.0.0.1:15182",
    trace: "retain-on-failure",
  },
});
