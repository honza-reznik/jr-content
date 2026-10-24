// Regrese review: upravitelný název u zamčeného popisku a chyba autosave i na mobilu.
// Spouštět po seed.py a composer.cjs přes JRC_PROJECT=jrc-f6b02 make shell.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');

(async () => {
  const base = 'http://jrc-bb-web:8000';
  const root = '/workspace/ops';
  const seed = JSON.parse(await fs.readFile(`${root}/test-output/F6-B02/session.json`, 'utf8'));
  const detailUrl = `${base}/workspace/${seed.workspace}/content/${seed.post}/?tab=caption`;
  const composeUrl = `${base}/workspace/${seed.workspace}/compose/${seed.post}/`;
  const browser = await chromium.launch({ headless: true });
  const checks = [], errors = [], external = [];
  try {
    for (const width of [1440, 390]) {
      const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1050 } });
      await context.addCookies([{ name: 'sessionid', value: seed.sessions.admin, url: base }]);
      await context.route('**/*', route => {
        if (new URL(route.request().url()).origin !== base) {
          external.push(route.request().url());
          return route.abort();
        }
        return route.continue();
      });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(String(error)));
      await page.goto(detailUrl, { waitUntil: 'networkidle' });
      if (width === 1440) {
        const video = page.locator('article').filter({ hasText: 'YouTube' });
        await video.locator('textarea').fill('Saved video caption 🌱\nSecond line');
        await Promise.all([
          page.waitForNavigation({ waitUntil: 'networkidle' }),
          video.getByRole('button', { name: 'Save version' }).click(),
        ]);
      }
      await page.goto(composeUrl, { waitUntil: 'networkidle' });
      const title = page.locator('[data-jrc-caption] input[name^="override_title_"]');
      await title.waitFor();
      if (width === 390) assert.equal(await title.inputValue(), 'Video title 1440 <b> & "quotes"');
      assert.equal(await title.isEditable(), true);
      assert.equal(await page.locator('[data-jrc-caption] textarea[readonly]').count(), 2);
      const changedTitle = `Video title ${width} <b> & "quotes"`;
      await title.fill(changedTitle);
      await title.scrollIntoViewIfNeeded();
      await page.screenshot({ path: `${root}/screenshoty/F6-B02/review-title-${width}.png` });
      await Promise.all([
        page.waitForNavigation({ waitUntil: 'networkidle' }),
        page.locator('#composer-form button[type="submit"][value="save_draft"]').first().click(),
      ]);
      await page.goto(composeUrl, { waitUntil: 'networkidle' });
      assert.equal(await page.locator('[data-jrc-caption] input[name^="override_title_"]').inputValue(), changedTitle);
      // Simulate a stale selection by omitting Instagram from the submitted form.
      await page.evaluate(() => {
        const root = document.querySelector('[x-data="composerApp()"]');
        const app = window.Alpine.$data(root);
        const limits = app.charLimits;
        app.selectedAccounts = app.selectedAccounts.filter(id => limits[id].platform !== 'instagram');
      });
      const autosave = page.waitForResponse(r => r.url().includes('/autosave/') && r.request().method() === 'POST');
      await page.evaluate(() => {
        const trigger = document.getElementById('autosave-trigger');
        window.htmx.ajax('POST', trigger.getAttribute('hx-post'), {
          source: trigger, target: '#autosave-status', swap: 'innerHTML',
          values: Object.fromEntries(new FormData(document.getElementById('composer-form'))),
        });
      });
      assert.equal((await autosave).status(), 200);
      const error = page.locator('[x-text="formError"]');
      await error.waitFor({ state: 'visible' });
      assert.match(await error.textContent(), /caption history cannot be removed/);
      assert.match(await page.locator('#autosave-status').textContent(), /Not saved/);
      await page.waitForTimeout(350); // dokončení animace hlášky
      const errorBox = await error.boundingBox();
      assert(errorBox.x >= 0 && errorBox.x + errorBox.width <= width);
      assert(errorBox.y + errorBox.height <= (width === 390 ? 844 : 1050) - 60);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.screenshot({ path: `${root}/screenshoty/F6-B02/review-autosave-error-${width}.png` });
      await page.goto(detailUrl, { waitUntil: 'networkidle' });
      const instagram = page.locator('article').filter({ hasText: 'Instagram' });
      assert.equal(await instagram.locator('textarea').inputValue(), seed.caption);
      const video = page.locator('article').filter({ hasText: 'YouTube' });
      assert.equal(await video.locator('textarea').inputValue(), 'Saved video caption 🌱\nSecond line');
      checks.push({ width, titleEditable: true, titlePreserved: true, autosaveErrorVisible: true, captionsPreserved: true });
      await context.close();
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    await fs.writeFile(`${root}/test-output/F6-B02/review-browser-report.json`, JSON.stringify({ checks, errors, external }, null, 2));
    console.log('OK: 1440/390 px, název upravitelný a zachovaný, chyba autosave viditelná, popisky zachované.');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
