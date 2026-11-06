// Druhá část: prošlý klíč jde odstranit. Jen přes run-browser.sh.
const { chromium } = require('/opt/jrc-tools/node_modules/playwright');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
(async () => {
  const origin = 'http://127.0.0.1:13713';
  const seed = JSON.parse(await fs.readFile('/workspace/ops/test-output/P1-B29/seed.json', 'utf8'));
  const browser = await chromium.launch({args: ['--js-flags=--max-old-space-size=512']});
  const timer = setTimeout(() => process.exit(124), 200000);
  try {
    const context = await browser.newContext({viewport: {width: 1365, height: 900}});
    await context.addCookies([{name: 'sessionid', value: seed.session, url: origin}]);
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    await page.goto(origin + '/jrc/organizations/api-keys/');
    const row = page.locator('div.px-6.py-4', {hasText: 'B29 platny'});
    assert.equal(await row.getByRole('button', {name: 'Delete'}).count(), 0, 'active key has no Delete');
    await page.screenshot({path: '/workspace/ops/screenshoty/P1-B29/05-prosly-klic-seznam.png', fullPage: true});
    const expired = page.locator('div.px-6.py-4', {hasText: 'B29 prosly'});
    await expired.getByRole('button', {name: 'Delete'}).click();
    await page.getByRole('heading', {name: 'Delete API key'}).waitFor();
    await page.screenshot({path: '/workspace/ops/screenshoty/P1-B29/06-smazat-potvrzeni.png'});
    await page.getByRole('button', {name: 'Delete key'}).click();
    await page.getByText('Deleted key').first().waitFor();
    assert.equal(await expired.count(), 0, 'expired key removed from the list');
    await page.screenshot({path: '/workspace/ops/screenshoty/P1-B29/07-po-smazani.png', fullPage: true});
    console.log('OK delete');
  } finally { clearTimeout(timer); await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
