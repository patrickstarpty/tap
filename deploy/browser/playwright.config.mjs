import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '/opt/browser/frozen', testMatch: 'flow.spec.ts', workers: 1,
  timeout: Number(process.env.TAP_BROWSER_TEST_TIMEOUT || 120_000),
  retries: 0, reporter: [['/opt/browser/reporter.mjs']],
  outputDir: '/opt/browser/output/test-results',
  use: { browserName: 'chromium', headless: true, screenshot: 'on', trace: 'on',
    proxy: { server: process.env.TAP_BROWSER_PROXY },
    launchOptions: { chromiumSandbox: true }, viewport: { width: 1280, height: 800 } },
});
