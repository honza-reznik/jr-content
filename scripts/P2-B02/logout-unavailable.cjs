// Hostitel před fází error zastaví jen vlastní FF API a před retry je obnoví.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
assert.equal(process.env.JRC_PROJECT, 'jrc-p2b02');
const phase = process.argv[2];
assert(['error', 'retry'].includes(phase));
const fixture = JSON.parse(fs.readFileSync('ops/test-output/P2-B02/fixture.json'));
const base = 'http://127.0.0.1:13902/jrc';
(async () => {
  const browser = await chromium.launch();
  try {
    for (const [device, viewport] of [['desktop', { width: 1440, height: 900 }], ['mobile', { width: 390, height: 844 }]]) {
      const statePath = `ops/test-output/P2-B02/logout-error-${device}.json`;
      const context = await browser.newContext({ viewport, ...(phase === 'retry' ? { storageState: statePath } : {}) });
      const page = await context.newPage();
      page.setDefaultTimeout(90000);
      if (phase === 'error') {
        await page.goto(`${base}/accounts/login/?${new URLSearchParams({ next: '/jrc/accounts/logout/' })}`);
        await page.locator('input[name="login"]').fill(fixture.users.owner.email);
        await page.locator('input[name="password"]').fill(fixture.users.owner.password);
        await page.locator('button[type="submit"]').click();
      } else {
        await page.goto(`${base}/accounts/logout/`);
      }
      const [response] = await Promise.all([
        page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname.endsWith('/accounts/logout/')),
        page.getByRole('button', { name: 'Sign out', exact: true }).click(),
      ]);
      if (phase === 'error') {
        assert.equal(response.status(), 503);
        await page.getByRole('alert').filter({ hasText: 'You are still signed in' }).waitFor();
        assert(new URL(page.url()).pathname.endsWith('/accounts/logout/'));
        await page.screenshot({ path: `ops/screenshoty/sprint-feedback/P2-B02/${device}-12-logout-unavailable.png`, fullPage: true });
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await context.storageState({ path: statePath });
        fs.chmodSync(statePath, 0o600);
      } else {
        assert.equal(response.status(), 302);
        await page.waitForURL(`${base}/accounts/login/`);
        assert.equal(await page.evaluate(() => localStorage.getItem('ff_access_token')), null);
      }
      await context.close();
      console.log(`OK ${device}: odhlášení ${phase === 'error' ? 'odmítnuté při výpadku, relace zachovaná' : 'po obnovení FF úspěšné'}.`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message.replace(/#grant=\S+/g, '#grant=[redacted]')); process.exitCode = 1; });
