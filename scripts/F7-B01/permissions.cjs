// Prohlížeč spouštět pouze v Dockeru projektu jrc-f7b01.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const { chromium } = require('playwright');

(async () => {
  const base = 'http://jrc-bb-web:8000';
  const root = '/workspace/ops';
  const seed = JSON.parse(await fs.readFile(`${root}/test-output/F7-B01/session.json`, 'utf8'));
  await fs.mkdir(`${root}/screenshoty/F7-B01`, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const checks = [], errors = [], external = [];
  try {
    for (const width of [1440, 390]) {
      const context = await browser.newContext({ viewport: { width, height: 1000 } });
      await context.addCookies([{ name: 'sessionid', value: seed.session, url: base }]);
      await context.route('**/*', route => {
        if (new URL(route.request().url()).origin !== base) {
          external.push(route.request().url());
          return route.abort();
        }
        return route.continue();
      });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(String(error)));
      const response = await page.goto(`${base}/workspace/${seed.workspace}/content/${seed.post}/?tab=caption`, { waitUntil: 'networkidle' });
      assert.equal(response.status(), 200);
      assert.equal(await page.getByRole('button', { name: 'Save version' }).count(), 0);
      assert(!await page.locator('body').textContent().then(text => text.includes('SECRET')));
      const denied = page.waitForResponse(response => response.request().method() === 'POST');
      await page.evaluate(() => document.querySelector('input[name="action"][value="save_caption"]').form.requestSubmit());
      assert.equal((await denied).status(), 403);
      await page.getByRole('heading', { name: 'You cannot perform this action' }).waitFor();
      await page.waitForLoadState('networkidle');
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.screenshot({ path: `${root}/screenshoty/F7-B01/403-${width}.png`, fullPage: true });
      await page.getByRole('link', { name: 'Back to content board' }).click();
      await page.waitForURL('**/board/');
      checks.push({ width, readOnlyCaption: true, forbiddenPage: true, backLink: true, noOverflow: true });
      await context.close();
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    await fs.writeFile(`${root}/test-output/F7-B01/browser-report.json`, JSON.stringify({ checks, errors, external }, null, 2));
    console.log('OK: klient, popisek pouze ke čtení, stránka 403 a návrat na nástěnku při 1440/390 px.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
