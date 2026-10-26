// Review 6: skutečná stránka composeru po události prvního automatického uložení.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const origin = process.env.JRC_PUBLIC_ORIGIN;
const base = origin + (process.env.JRC_BASE_PATH ?? '/jrc');
const fixture = JSON.parse(fs.readFileSync('ops/test-output/P1-B01/fixture.json', 'utf8'));
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ ignoreHTTPSErrors: true });
    const errors = [];
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base + '/accounts/login/');
    await page.locator('[name=login]').fill(fixture.admin.email);
    await page.locator('[name=password]').fill(fixture.admin.password);
    await page.locator('button[type=submit]').click();
    await page.waitForURL(url => !url.pathname.includes('/accounts/login/'));
    await page.goto(`${base}/workspace/${fixture.workspace}/compose/`);
    await page.waitForFunction(() => window.Alpine && document.querySelector('[x-data="composerApp()"]')?._x_dataStack);
    const post = '11111111-1111-4111-8111-111111111111';
    const urls = await page.evaluate(post => {
      document.body.dispatchEvent(new CustomEvent('autosaved', { detail: { postId: post, isNew: true } }));
      return {
        autosave: document.querySelector('#autosave-trigger').getAttribute('hx-post'),
        save: document.querySelector('#composer-form').getAttribute('hx-post'),
        upload: window.Alpine.$data(document.querySelector('#composer-form')).uploadUrl,
      };
    }, post);
    const path = new URL(`${base}/workspace/${fixture.workspace}/compose/${post}/`).pathname;
    assert.deepEqual(urls, { autosave: path + 'autosave/', save: path + 'save/', upload: path + 'upload-media/' });
    assert.deepEqual(errors, []);
    await context.close();
    console.log('OK review 6: živý composer po prvním autosave zachovává předponu u uložení i uploadu.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
