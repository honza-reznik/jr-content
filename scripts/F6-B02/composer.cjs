// Spouštět přes JRC_PROJECT=jrc-f6b02 make shell CMD='node scripts/F6-B02/composer.cjs'
// (po scripts/F6-B02/seed.py, který zapíše ops/test-output/F6-B02/session.json).
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');

(async () => {
  const base = 'http://jrc-bb-web:8000';
  const root = '/workspace/ops';
  const seed = JSON.parse(await fs.readFile(`${root}/test-output/F6-B02/session.json`, 'utf8'));
  await fs.mkdir(`${root}/screenshoty/F6-B02`, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const errors = [], external = [], checks = [], knownCsp = [];
  const detailUrl = `${base}/workspace/${seed.workspace}/content/${seed.post}/?tab=caption`;
  try {
    for (const width of [1440, 390]) {
      const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1050 } });
      await context.addCookies([{ name: 'sessionid', value: seed.sessions.admin, url: base }]);
      await context.route('**/*', (route) => {
        if (new URL(route.request().url()).origin !== base) {
          external.push(route.request().url());
          return route.abort();
        }
        return route.continue();
      });
      const page = await context.newPage();
      page.on('pageerror', (error) => errors.push(String(error)));
      page.on('console', (message) => {
        if (message.type() !== 'error') return;
        const text = message.text();
        // Hlavička COOP je u http://jrc-bb-web:8000 jen šum prohlížeče (bez HTTPS).
        if (text.includes('Cross-Origin-Opener-Policy')) return;
        const where = new URL(page.url()).pathname + new URL(page.url()).search;
        // Známý nález F6-B01 mimo rozsah: čítač znaků na kartě Caption je vložený skript bez nonce.
        if (text.includes('Executing inline script violates') && where.endsWith('?tab=caption') && where.includes('/content/')) {
          knownCsp.push(where);
          return;
        }
        errors.push(`console [${where}]: ${text}`);
      });
      const noOverflow = () => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);

      // 1. Detail obsahu, karta Caption: tři zapnuté kanály, TikTok skrytý, náhled IG = poslední verze.
      await page.goto(detailUrl, { waitUntil: 'networkidle' });
      await page.getByRole('heading', { name: /Channel captions/ }).waitFor();
      const cards = page.locator('article');
      assert.equal(await cards.count(), 3, 'Instagram, YouTube a LinkedIn; TikTok je vypnutý');
      assert.equal(await cards.filter({ hasText: 'TikTok' }).count(), 0);
      await cards.filter({ hasText: 'LinkedIn' }).getByText('New channel').waitFor();
      const detailText = page.locator('[data-caption-preview] span.whitespace-pre-wrap');
      await detailText.waitFor();
      assert.equal(await detailText.evaluate((el) => el.textContent), seed.caption, 'Náhled IG na detailu = poslední verze');
      assert.equal(await detailText.evaluate((el) => getComputedStyle(el).whiteSpace), 'pre-wrap');
      assert.equal(await page.locator('[data-caption-preview]').count(), 1, 'Náhled jen u Instagramu');
      assert(await noOverflow(), `Přetečení detailu při ${width} px`);
      // Obsah se posouvá ve vnitřním kontejneru, proto pro snímek celé karty dočasně zvýšíme okno.
      await page.setViewportSize({ width, height: 2400 });
      await page.waitForTimeout(300);
      await page.screenshot({ path: `${root}/screenshoty/F6-B02/caption-card-${width}.png` });
      await page.setViewportSize({ width, height: width === 390 ? 844 : 1050 });

      // 2. Composer: pole popisku jen ke čtení s poslední verzí a odkazem na záložku Caption.
      await page.goto(`${base}/workspace/${seed.workspace}/compose/${seed.post}/`, { waitUntil: 'networkidle' });
      const box = page.locator('[data-jrc-caption]');
      await box.waitFor();
      assert.equal(await box.count(), 1, 'Jen Instagram má uloženou verzi');
      const field = box.locator('textarea[readonly]');
      assert.equal(await field.inputValue(), seed.caption);
      assert.equal(await field.evaluate((el) => el.hasAttribute('name')), false, 'pole se neodesílá');
      const link = box.getByRole('link', { name: 'Edit in Caption tab' });
      assert.match(await link.getAttribute('href'), /\?tab=caption$/);
      const overridePlatforms = await page.evaluate(() => {
        const limits = JSON.parse(document.getElementById('composer-char-limits').textContent);
        return [...document.querySelectorAll('textarea[name^="override_caption_"]')].map((el) => limits[el.name.replace('override_caption_', '')].platform);
      });
      assert.deepEqual(overridePlatforms, ['youtube'], 'Instagram nemá upravitelné pole popisku, YouTube beze změny');
      assert(await page.getByRole('button', { name: /Customize YouTube/ }).count() > 0, 'YouTube bez verze zůstává beze změny');
      assert.equal(await page.getByRole('button', { name: /Customize Instagram/ }).count(), 0);
      assert(await noOverflow(), `Přetečení composeru při ${width} px`);
      await box.scrollIntoViewIfNeeded();
      await page.screenshot({ path: `${root}/screenshoty/F6-B02/composer-readonly-${width}.png` });

      // 3. Náhled Instagramu v composeru ukazuje stejný text jako poslední verze.
      if (width === 390) await page.getByRole('button', { name: /Preview/ }).first().click();
      const previewText = page.locator('#preview-panel .preview-card span.whitespace-pre-wrap').first();
      await previewText.waitFor();
      assert.equal(await previewText.evaluate((el) => el.textContent), seed.caption, 'Náhled IG v composeru = poslední verze');
      await page.locator('.panel-right').screenshot({ path: `${root}/screenshoty/F6-B02/composer-preview-${width}.png` });
      if (width === 390) await page.locator('.panel-right-backdrop').click({ position: { x: 5, y: 300 }, force: true }).catch(() => {});

      // 4. Uložení composeru popisek nepřepíše.
      const [saved] = await Promise.all([
        page.waitForResponse((response) => response.url().includes('/save/') && response.request().method() === 'POST'),
        page.locator('#composer-form button[type="submit"][value="save_draft"]').first().click(),
      ]);
      assert.ok(saved.status() < 400, `Uložení composeru vrátilo ${saved.status()}`);
      await page.waitForTimeout(2000); // stránka se po uložení sama obnoví
      await page.waitForLoadState('networkidle');
      await page.goto(detailUrl, { waitUntil: 'networkidle' });
      assert.equal(await page.locator('[data-caption-preview] span.whitespace-pre-wrap').evaluate((el) => el.textContent), seed.caption);
      assert.equal(await page.locator('textarea#caption-1').inputValue(), seed.caption, 'Uložení composeru popisek nezměnilo');

      // 5. Odkaz „Edit in Caption tab“ vede na detail s kartou Caption.
      await page.goto(`${base}/workspace/${seed.workspace}/compose/${seed.post}/`, { waitUntil: 'networkidle' });
      await page.locator('[data-jrc-caption]').getByRole('link', { name: 'Edit in Caption tab' }).click();
      await page.waitForURL(/\/content\/.*\?tab=caption/);
      checks.push({ width, readOnly: true, previewMatches: true, savedUnchanged: true, noOverflow: true });
      await context.close();
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    await fs.writeFile(`${root}/test-output/F6-B02/browser-report.json`, JSON.stringify({ checks, errors, external, knownCsp }, null, 2));
    console.log('OK: 1440/390 px, karta Caption (3 kanály, TikTok skrytý), náhled IG, composer jen ke čtení, uložení bez přepsání.');
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
