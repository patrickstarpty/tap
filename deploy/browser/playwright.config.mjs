import { defineConfig } from '@playwright/test';

const runtime = process.env.TAP_BROWSER_RUNTIME_DIR || '/opt/browser';
const frozen = process.env.TAP_BROWSER_FROZEN_DIR || '/opt/browser/frozen';
const output = process.env.TAP_BROWSER_OUTPUT_DIR || '/opt/browser/output';
export default defineConfig({
  testDir: frozen, testMatch: 'flow.spec.ts', workers: 1,
  timeout: Number(process.env.TAP_BROWSER_TEST_TIMEOUT || 120_000),
  retries: 0, reporter: [[`${runtime}/reporter.mjs`]],
  outputDir: `${output}/test-results`,
  use: { browserName: 'chromium', headless: !process.env.TAP_BROWSER_LOCAL_DIR || process.env.TAP_BROWSER_LOCAL_HEADLESS !== 'false', screenshot: 'on', trace: 'on', video: 'on',
    proxy: { server: process.env.TAP_BROWSER_PROXY },
    ignoreHTTPSErrors: Boolean(process.env.TAP_BROWSER_LOCAL_DIR),
    launchOptions: { chromiumSandbox: true,
      ...(process.env.TAP_BROWSER_LOCAL_EXECUTABLE_PATH ? {executablePath:process.env.TAP_BROWSER_LOCAL_EXECUTABLE_PATH} : {}) }, viewport: { width: 1280, height: 800 } },
});
