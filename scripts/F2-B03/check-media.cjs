// Živý test oprávnění a náhledů; spouštět pouze v Dockeru projektu jrc-f2b03.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
assert.equal(process.env.JRC_PROJECT, 'jrc-f2b03');
const origin = `http://127.0.0.1:${process.env.JRC_APP_PORT || 13000}`;
const seed = JSON.parse(fs.readFileSync('/seed/f2b03-media-check.json', 'utf8'));

(async () => {
  for (const path of seed.paths) {
    for (const identity of ['anonymous', 'outsider', 'member']) {
      const headers = identity === 'anonymous' ? {} : { Cookie: `sessionid=${seed.sessions[identity]}` };
      const response = await fetch(origin + path, { redirect: 'manual', headers });
      assert.equal(response.status, { anonymous: 302, outsider: 404, member: 200 }[identity], `${identity}: ${path}`);
      if (identity === 'member') {
        assert.match(response.headers.get('cache-control'), /no-store/);
        assert((await response.arrayBuffer()).byteLength > 100);
      }
    }
  }
  console.log('OK: proxy odmítá anonymy (302) i členy cizího workspace stejné organizace (404); člen dostane 200, včetně verzí a náhledů.');
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [1440, 390]) {
      const context = await browser.newContext({ viewport: { width, height: 900 } });
      const foreign = [], failed = [], errors = [];
      await context.route('**/*', route => {
        if (new URL(route.request().url()).origin !== origin) {
          foreign.push(route.request().url());
          return route.abort();
        }
        return route.continue();
      });
      await context.addCookies([{ name: 'sessionid', value: seed.sessions.member, url: origin }]);
      const page = await context.newPage();
      page.on('response', response => { if (response.status() >= 400) failed.push([response.status(), response.url()]); });
      page.on('pageerror', error => errors.push(String(error)));
      const response = await page.goto(`${origin}/workspace/${seed.workspace}/media/`, { waitUntil: 'networkidle' });
      assert.equal(response.status(), 200);
      const previews = page.locator('.ml-asset-card img');
      assert.equal(await previews.count(), 2);
      for (const preview of await previews.all()) {
        await preview.scrollIntoViewIfNeeded();
        await preview.evaluate(img => img.decode());
        assert(await preview.evaluate(img => img.complete && img.naturalWidth > 0));
      }
      const out = '/jrc/ops/screenshoty/F2-B03';
      fs.mkdirSync(out, { recursive: true });
      await page.screenshot({ path: `${out}/media-${width}.png`, fullPage: true });
      assert.deepEqual(foreign, []);
      assert.deepEqual(failed, []);
      assert.deepEqual(errors, []);
      console.log(`OK: originál i náhled načteny v UI ${width} px, bez cizích adres, HTTP a JS chyb.`);
      await context.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
