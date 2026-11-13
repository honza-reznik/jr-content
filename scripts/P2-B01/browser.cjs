// Jen Docker: timeout 180s node --max-old-space-size=2048 scripts/P2-B01/browser.cjs
const fs = require('node:fs');
const assert = require('node:assert/strict');
const { chromium } = require('/opt/jrc-tools/node_modules/playwright');
const fixture = JSON.parse(fs.readFileSync('ops/test-output/P2-B01/fixture.json'));
const origin = 'http://127.0.0.1:13901';
const base = origin + '/jrc';
(async () => {
  const browser = await chromium.launch({headless: true, args: ['--disable-dev-shm-usage']});
  try {
    for (const [label, width, height] of [['desktop',1440,900], ['mobile',390,844]]) {
      for (const who of ['owner', 'client']) {
        const context = await browser.newContext({viewport:{width,height}});
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', e => errors.push(e.message));
        await page.route('**/*', route => {
          const url = new URL(route.request().url());
          if (['http:', 'https:'].includes(url.protocol) && url.origin !== origin) return route.abort();
          return route.continue();
        });
        await page.goto(base + '/accounts/login/', {waitUntil:'networkidle'});
        await page.locator('[name=login]').fill(fixture[who].email);
        await page.locator('[name=password]').fill('incorrect-test-password');
        await page.locator('button[type=submit]').click();
        await page.waitForLoadState('networkidle');
        assert(page.url().includes('/accounts/login/'));
        await page.locator('[name=password]').focus();
        await page.locator('button[type=submit]').hover();
        assert(await page.locator('[name=password]').evaluate(input => document.activeElement === input));
        await page.screenshot({path:`ops/screenshoty/sprint-feedback/P2-B01/${label}-${who}-login-error.png`,fullPage:true});
        await page.locator('[name=password]').fill(fixture[who].password);
        await page.locator('button[type=submit]').click();
        await page.waitForURL(url => !url.pathname.includes('/accounts/login/'));
        await page.goto(`${base}/workspace/${fixture.workspace_id}/content/${fixture.post_id}/`, {waitUntil:'networkidle'});
        await page.getByText('Testovací obsah po bezpečné obnově', {exact:false}).first().waitFor();
        const layout = await page.evaluate(() => ({width:document.documentElement.clientWidth, scroll:document.documentElement.scrollWidth}));
        assert(layout.scroll <= layout.width + 1, JSON.stringify(layout));
        assert.deepEqual(errors, []);
        await page.screenshot({path:`ops/screenshoty/sprint-feedback/P2-B01/${label}-${who}-restored.png`,fullPage:true});
        console.log(`OK ${label} ${who}: chybné i správné přihlášení, obnovený obsah, bez přetékání a JS chyb`);
        await context.close();
      }
    }
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode=1; });
