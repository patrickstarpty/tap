import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: "prototype-demo-capture.spec.ts",
  outputDir: "./test-results/prototype-capture",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  use: {
    ...devices["Desktop Chrome"],
    baseURL: "http://127.0.0.1:15174",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "corepack pnpm exec vite --host 127.0.0.1 --port 15174",
    url: "http://127.0.0.1:15174",
    reuseExistingServer: false,
    env: {
      TAPPER_API_HOST: "127.0.0.1",
      TAPPER_API_PORT: "18001",
      TAPPER_WEB_HOST: "127.0.0.1",
      TAPPER_WEB_PORT: "15174",
    },
  },
});
