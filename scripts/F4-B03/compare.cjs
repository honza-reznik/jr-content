// Spouštět přes JRC_PROJECT=jrc-f4b03 make shell CMD='node scripts/F4-B03/compare.cjs'.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');

(async () => {
  const base = 'http://jrc-bb-web:8000';
  const root = '/workspace/ops';
  const seed = JSON.parse(await fs.readFile(`${root}/test-output/F4-B03/session.json`, 'utf8'));
  const browser = await chromium.launch({ headless: true });
  const errors = [], external = [], checks = [];
  try {
    for (const width of [1440, 390]) {
      const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1050 } });
      // Člen s rolí klienta má k porovnání stejný přístup jako ke čtení scénáře.
      await context.addCookies([{ name: 'sessionid', value: seed.sessions.client, url: base }]);
      await context.route('**/*', route => {
        if (new URL(route.request().url()).origin !== base) {
          external.push(route.request().url());
          return route.abort();
        }
        return route.continue();
      });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(String(error)));
      await page.goto(`${base}/workspace/${seed.workspace}/content/${seed.post}/?tab=script`, { waitUntil: 'networkidle' });
      await page.locator('#script-compare:not([hidden])').waitFor();
      const old = page.locator('#script-compare-old');
      const next = page.locator('#script-compare-new');
      assert.equal(await old.locator('option:checked').textContent(), 'Version 2');
      assert.equal(await next.locator('option:checked').textContent(), 'Version 3');
      assert.equal(await page.locator('#script-edit').isVisible(), false);
      await old.selectOption({ label: 'Version 1' });
      await page.waitForFunction(() => document.querySelector('#script-compare-meta').textContent.startsWith('Version 1:'));
      await page.locator('#script-compare-new:not([disabled])').waitFor();
      const diff = await page.locator('#script-diff').textContent();
      assert.match(diff, /number 1 → 5/);
      assert.match(diff, /Task unchecked → Task checked/);
      assert.match(diff, /Paragraph → Heading 2/);
      assert(await page.locator('#script-diff ins').count() > 0);
      assert(await page.locator('#script-diff del').count() > 0);
      assert.match(await page.locator('#script-compare-meta').textContent(), /Jamie/);
      await page.evaluate(() => document.fonts.ready);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Přetečení při ${width} px`);
      await page.screenshot({ path: `${root}/screenshoty/F4-B03/compare-${width}.png`, fullPage: true });
      if (width === 390) {
        // Skutečný patnáctisekundový obnovovací cyklus nesmí zahodit zvolenou verzi 1.
        await page.waitForResponse(response => response.url().includes('/script/?') && response.request().method() === 'GET', { timeout: 20000 });
        await page.waitForFunction(() => document.querySelector('#script-compare-old option:checked').textContent === 'Version 1');
        // Stejná verze nemá barevně označené změny.
        await next.selectOption({ label: 'Version 1' });
        await page.waitForFunction(() => document.querySelector('#script-compare-meta').textContent.includes('→ Version 1:'));
        assert.equal(await page.locator('#script-diff ins, #script-diff del').count(), 0);
        // Server vrací metadata i při překročení velikosti; chyba musí být viditelná.
        await context.route('**/script/**', async route => {
          const response = await route.fetch();
          const data = await response.json();
          data.comparison_error = 'These versions are too large to compare.';
          delete data.comparison.old.content;
          delete data.comparison.new.content;
          await route.fulfill({ json: data });
        });
        await page.locator('#script-reload').click();
        await page.locator('#script-compare-error:not([hidden])').waitFor();
        assert.match(await page.locator('#script-compare-error').textContent(), /too large/);
      }
      checks.push({ width, readOnly: true, versions: '1 vs 3', noOverflow: true });
      await context.close();
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    await fs.writeFile(`${root}/test-output/F4-B03/browser-report.json`, JSON.stringify({ checks, errors, external }, null, 2));
    console.log('OK: 1440/390 px, verze 1 vs 3, čtenář, metadata, číslování, formát, checklist, obnova volby, shodné verze a limit velikosti.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
