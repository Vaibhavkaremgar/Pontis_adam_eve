import { defineConfig, devices } from '@playwright/test';

const configuredBaseUrl = process.env.PLAYWRIGHT_BASE_URL?.trim() || 'http://127.0.0.1:3000';
const useLocalWebServer = !process.env.PLAYWRIGHT_BASE_URL?.trim();

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: true,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: configuredBaseUrl,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  webServer: useLocalWebServer
    ? {
        command: 'npm run dev',
        url: configuredBaseUrl,
        reuseExistingServer: true,
        timeout: 120_000,
      }
    : undefined,
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
