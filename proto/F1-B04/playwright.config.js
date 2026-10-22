// Playwright config for the F1-B04 prototype. Runs inside the jrc-proto-f1-b04 container
// (Chromium from the jrc-dev image, headless).
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:3100',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'PORT=3100 HOST=127.0.0.1 node server.mjs',
    url: 'http://127.0.0.1:3100/',
    reuseExistingServer: false,
    timeout: 10_000,
  },
  projects: [
    { name: 'desktop', use: { browserName: 'chromium', viewport: { width: 1280, height: 800 } } },
    {
      name: 'mobil-390',
      use: {
        browserName: 'chromium',
        viewport: { width: 390, height: 844 },
        deviceScaleFactor: 2,
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
});
