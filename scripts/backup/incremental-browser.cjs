// P1-B12: po obnově druhé zálohy zobrazí obsah a ověří obrázek na počítači i telefonu.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const { chromium } = require('/opt/jrc-tools/node_modules/playwright');

const seed = JSON.parse(fs.readFileSync('ops/test-output/P1-B12/seed.json', 'utf8'));
const origin = process.env.JRC_PUBLIC_ORIGIN;
assert.equal(origin, 'http://127.0.0.1:13553');
const app = origin + '/jrc';

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (['http:', 'https:'].includes(url.protocol) && url.origin !== origin) {
          errors.push(`Externí požadavek: ${url.origin}`);
          return route.abort('blockedbyclient');
        }
        return route.continue();
      });
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(`${app}/accounts/login/`);
      await page.locator('input[name="login"]').fill(seed.email);
      await page.locator('input[name="password"]').fill(seed.password);
      await page.locator('button[type="submit"]').click();
      await page.waitForLoadState('networkidle');
      assert.ok(!page.url().includes('/accounts/login'), 'Přihlášení po obnově selhalo.');
      await page.goto(`${app}/workspace/${seed.workspace_id}/content/${seed.post_id}/`, { waitUntil: 'networkidle' });
      assert.ok((await page.content()).includes(seed.title));
      const script = await page.request.get(`${app}/workspace/${seed.workspace_id}/content/${seed.post_id}/script/`);
      assert.equal(script.status(), 200);
      assert.ok(JSON.stringify(await script.json()).includes('žluťoučký kůň'));
      await page.screenshot({ path: `ops/screenshoty/P1-B12/content-${width}.png`, fullPage: true });
      await page.goto(`${app}/workspace/${seed.workspace_id}/media/${seed.asset_id}/`, { waitUntil: 'networkidle' });
      const images = await page.evaluate(() => Array.from(document.images).map(i => ({ src: i.currentSrc || i.src, w: i.naturalWidth })));
      const image = images.find(i => i.src.startsWith(`${app}/files/`) && i.w === 320);
      assert.ok(image, 'Obrázek z obnovené zálohy se nezobrazil.');
      const response = await page.request.get(image.src);
      assert.equal(response.status(), 200);
      assert.equal(crypto.createHash('sha256').update(await response.body()).digest('hex'), seed.png_sha256);
      await page.screenshot({ path: `ops/screenshoty/P1-B12/media-${width}.png`, fullPage: true });
      assert.deepEqual(errors, []);
      await page.close();
      console.log(`OK Chromium ${width}: přihlášení, obsah, scénář, obrázek i SHA-256 po obnově.`);
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
