// Chromium po obnově (F2-B09): přihlášení, zobrazení obsahu, textu a obrázku z obnoveného úložiště.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const { chromium } = require('/opt/jrc-tools/node_modules/playwright');

const seed = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const origin = process.env.JRC_PUBLIC_ORIGIN;
const basePath = process.env.JRC_BASE_PATH ?? "/jrc";
const app = origin + basePath;
const shot = process.argv[3];
const errors = [];

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (['http:', 'https:'].includes(url.protocol) && url.origin !== origin) {
        errors.push(`Externí požadavek: ${url.href}`);
        return route.abort('blockedbyclient');
      }
      return route.continue();
    });
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(`${app}/accounts/login/`);
    await page.locator('input[name="login"]').fill(seed.email);
    await page.locator('input[name="password"]').fill(seed.password);
    await page.locator('button[type="submit"]').click();
    await page.waitForLoadState('networkidle');
    assert.ok(!page.url().includes('/accounts/login'), `přihlášení selhalo: ${page.url()}`);
    // obsah
    await page.goto(`${app}/workspace/${seed.workspace_id}/content/${seed.post_id}/`, { waitUntil: 'networkidle' });
    assert.ok((await page.content()).includes(seed.title), 'název obsahu není na stránce');
    // text (scénář) přes stejnou relaci prohlížeče
    const response = await page.request.get(`${app}/workspace/${seed.workspace_id}/content/${seed.post_id}/script/`);
    assert.equal(response.status(), 200);
    assert.ok(JSON.stringify(await response.json()).includes('žluťoučký kůň'), 'text scénáře po obnově chybí');
    // obrázek: podepsaná adresa míří na obnovené úložiště a vrací původní bajty
    await page.goto(`${app}/workspace/${seed.workspace_id}/media/${seed.asset_id}/`, { waitUntil: 'networkidle' });
    const images = await page.evaluate(() => Array.from(document.images).map((i) => ({ src: i.currentSrc || i.src, w: i.naturalWidth })));
    const shown = images.find((i) => i.src.startsWith(`${app}/storage/jrc-app/`) && i.w === 320);
    assert.ok(shown, `obrázek z úložiště na cílovém původu ${origin} se nezobrazil: ${JSON.stringify(images)}`);
    const bytes = await (await page.request.get(shown.src)).body();
    assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), seed.png_sha256, 'obrázek má jiné bajty než původní');
    if (shot) { fs.mkdirSync(require('node:path').dirname(shot), { recursive: true }); await page.screenshot({ path: shot, fullPage: true }); }
    assert.deepEqual(errors, []);
    console.log('OK Chromium po obnově: přihlášení, obsah, text scénáře a obrázek z obnoveného úložiště');
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exitCode = 1; });
