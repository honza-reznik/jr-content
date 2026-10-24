// F8-B01: ověření kalendáře jen proti místnímu projektu v Dockeru.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

assert.equal(process.env.JRC_PROJECT, 'jrc-f8b01');
assert.equal(process.env.JRC_APP_PORT, '13273');
// The browser runs in jrc-dev, so resolve the host-local origin to the proxy's
// Docker service address while preserving the host name accepted by Django.
const origin = process.env.F8_APP_ORIGIN || 'http://127.0.0.1:8080';
const workspace = process.env.F8_WORKSPACE_ID;
const email = process.env.F8_VISUAL_EMAIL;
const password = process.env.F8_VISUAL_PASSWORD;
assert(workspace && email && password, 'Chybí místní testovací přístup');
const screenshots = path.resolve('ops/screenshoty/F8-B01');
fs.mkdirSync(screenshots, { recursive: true });

(async () => {
  const browser = await chromium.launch({
    headless: true,
    args: ['--host-resolver-rules=MAP 127.0.0.1 jrc-proxy'],
  });
  try {
    for (const width of [1440, 390]) {
      const context = await browser.newContext({ viewport: { width, height: 980 } });
      const foreign = [];
      const pageErrors = [];
      await context.route('**/*', route => {
        if (new URL(route.request().url()).origin !== origin) {
          foreign.push(route.request().url());
          return route.abort();
        }
        return route.continue();
      });
      const page = await context.newPage();
      page.on('pageerror', error => pageErrors.push(error.message));
      await page.goto(`${origin}/accounts/login/`);
      await page.locator('input[name="login"]').fill(email);
      await page.locator('input[name="password"]').fill(password);
      await page.locator('button[type="submit"]').click();
      await page.waitForURL(url => !url.pathname.includes('/accounts/login/'));
      await page.goto(`${origin}/workspace/${workspace}/calendar/?mode=calendar&view=month`);
      await page.getByRole('heading', { name: /Waiting for me/ }).waitFor();
      await page.getByText('Visual: launch story').first().waitFor();
      await page.getByText('Review', { exact: true }).first().waitFor();
      const html = await page.locator('body').innerText();
      assert(!html.includes('INTERNAL F8 TEST COMMENT MUST STAY PRIVATE'), 'Kalendář odhalil interní komentář');
      await page.screenshot({ path: path.join(screenshots, `month-${width}.png`), fullPage: true });
      assert.deepEqual(foreign, [], 'Prohlížeč kontaktoval cizí doménu');
      assert.deepEqual(pageErrors, [], 'Prohlížeč nahlásil chybu JavaScriptu');
      await context.close();
      console.log(`OK F8-B01 kalendář ${width}px; bez interního komentáře a cizích požadavků`);
    }
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
