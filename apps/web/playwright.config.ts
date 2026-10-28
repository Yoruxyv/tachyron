import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  timeout: 120000,
  expect: { timeout: 15000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: 'http://127.0.0.1:4174',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command:
        'uv run --locked --project ../server python scripts/serve-test-api.py --with-test-dataset',
      url: 'http://127.0.0.1:8001/health',
      timeout: 30000,
      reuseExistingServer: false,
      gracefulShutdown: { signal: 'SIGTERM', timeout: 10000 },
    },
    {
      command:
        'node node_modules/vite/bin/vite.js preview --port 4174 --strictPort',
      env: { TACHYRON_API_URL: 'http://127.0.0.1:8001' },
      url: 'http://127.0.0.1:4174',
      timeout: 30000,
      reuseExistingServer: false,
    },
  ],
});
