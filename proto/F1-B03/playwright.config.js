// Automatické testy prototypu. Spouštět v kontejneru: ./proto.sh test
// Testy mají vlastní databázi proto_test (před během se vyprázdní) a vlastní server na portu 3100.
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  workers: 1,
  timeout: 30_000,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:3100',
    browserName: 'chromium',
    viewport: { width: 1280, height: 900 },
    locale: 'cs-CZ',
    timezoneId: 'Europe/Prague',
  },
  webServer: {
    command: 'npm run build && node tests/prepare-db.js && node server/index.js',
    url: 'http://127.0.0.1:3100/api/health',
    reuseExistingServer: false,
    timeout: 60_000,
    env: { PORT: '3100', PGDATABASE: 'proto_test', PROTO_SEED: '0' },
  },
})
