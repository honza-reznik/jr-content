// Vizuální kontrola ruční publikace pouze proti projektu jrc-f8b02.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

assert.equal(process.env.JRC_PROJECT, 'jrc-f8b02');
assert.equal(process.env.JRC_APP_PORT, '13413');
const origin = process.env.F8_B02_APP_ORIGIN || 'http://127.0.0.1:8080';
const workspace = process.env.F8_B02_WORKSPACE_ID;
const password = process.env.F8_B02_VISUAL_PASSWORD;
assert(workspace, 'Chybí testovací ID klienta');
assert(password, 'Chybí testovací heslo');
const shots = path.resolve('ops/screenshoty/F8-B02');
fs.mkdirSync(shots, { recursive: true });

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ['--host-resolver-rules=MAP 127.0.0.1 jrc-proxy'],
  });
  try {
    for (const width of [1440, 390]) {
      const context = await browser.newContext({ viewport: { width, height: 2500 } });
      const foreign = [];
      const errors = [];
      await context.route('**/*', route => {
        if (new URL(route.request().url()).origin !== origin) {
          foreign.push(route.request().url());
          return route.abort();
        }
        return route.continue();
      });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(`${origin}/accounts/login/`);
      await page.locator('input[name="login"]').fill('f8-b02-visual@example.invalid');
      await page.locator('input[name="password"]').fill(password);
      await page.locator('button[type="submit"]').click();
      await page.waitForURL(url => !url.pathname.includes('/accounts/login/'));
      await page.goto(`${origin}/workspace/${workspace}/content/${process.env.F8_B02_POST_ID}/?tab=publish`);
      await page.getByRole('heading', { name: 'Publish channels' }).waitFor();
      await page.getByRole('button', { name: 'Copy caption' }).first().waitFor();
      await page.getByRole('link', { name: 'Download final video' }).waitFor();
      await page.getByRole('button', { name: 'Mark as published' }).first().waitFor();
      await page.screenshot({ path: path.join(shots, `publish-${width}.png`), fullPage: true });
      assert.deepEqual(foreign, [], 'Prohlížeč kontaktoval cizí doménu');
      assert.deepEqual(errors, [], 'Prohlížeč nahlásil chybu JavaScriptu');
      await context.close();
      console.log(`OK F8-B02 obrazovka Publish ${width}px`);
    }
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
