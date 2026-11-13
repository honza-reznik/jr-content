// R2 (P2-B02): odhlášení musí projít i při zastaveném FF API. Hostitel před během zastaví
// jen vlastní jrc-ff-api (a po něm ho obnoví). Argument: ff-up | ff-down.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
assert.equal(process.env.JRC_PROJECT, 'jrc-p2b02');
const mode = process.argv[2];
assert(['ff-up', 'ff-down'].includes(mode));
const fixture = JSON.parse(fs.readFileSync('ops/test-output/P2-B02/fixture.json'));
const base = 'http://127.0.0.1:13902/jrc';
(async () => {
  const browser = await chromium.launch();
  try {
    for (const [device, viewport] of [['desktop', { width: 1440, height: 900 }], ['mobile', { width: 390, height: 844 }]]) {
      const context = await browser.newContext({ viewport });
      const page = await context.newPage();
      page.setDefaultTimeout(90000);
      await page.goto(`${base}/accounts/login/?${new URLSearchParams({ next: '/jrc/accounts/logout/' })}`);
      await page.locator('input[name="login"]').fill(fixture.users.owner.email);
      await page.locator('input[name="password"]').fill(fixture.users.owner.password);
      await page.locator('button[type="submit"]').click();
      await page.getByRole('button', { name: 'Sign out', exact: true }).waitFor();
      await page.screenshot({ path: `ops/screenshoty/sprint-feedback/P2-B02/${device}-12-logout-${mode}.png`, fullPage: true });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Vodorovné přetékání');
      const [response] = await Promise.all([
        page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname.endsWith('/accounts/logout/')),
        page.getByRole('button', { name: 'Sign out', exact: true }).click(),
      ]);
      assert.equal(response.status(), 302, 'odhlášení musí projít');
      await page.waitForURL(`${base}/accounts/login/`);
      assert.equal(await page.evaluate(() => localStorage.getItem('ff_access_token')), null);
      // Aplikace je odhlášená: chráněná stránka vede zpět na přihlášení.
      await page.goto(`${base}/`);
      assert(new URL(page.url()).pathname.endsWith('/accounts/login/'));
      await context.close();
      console.log(`OK ${device} (${mode}): odhlášení prošlo, relace aplikace ukončena.`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message.replace(/#grant=\S+/g, '#grant=[redacted]')); process.exitCode = 1; });
