import { defineConfig } from '@playwright/test';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontend = dirname(fileURLToPath(import.meta.url));
const backend = resolve(frontend, '../backend');
const python = resolve(backend, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  timeout: 30000,
  expect: { timeout: 10000 },
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:5173',
    browserName: 'chromium',
    channel: process.env.PLAYWRIGHT_BROWSER_CHANNEL || 'chrome',
    viewport: { width: 1440, height: 1000 },
    timezoneId: 'America/New_York', // Displayed UK timestamps must not use the browser's timezone.
    trace: 'retain-on-failure',
  },
  webServer: [
    { command: `"${python}" -m uvicorn app.main:app --host localhost --port 8000`, cwd: backend,
      url: 'http://localhost:8000/api/health', reuseExistingServer: !process.env.CI, timeout: 30000 },
    { command: 'npm run dev -- --host localhost', cwd: frontend,
      url: 'http://localhost:5173', reuseExistingServer: !process.env.CI, timeout: 30000 },
  ],
});
