// Chromium ověření F6-B01a: psaní do pole popisku mění počítadlo, žádná chyba CSP.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('/opt/jrc-tools/node_modules/playwright');

const origin = 'http://127.0.0.1:13293';
const cred = JSON.parse(fs.readFileSync('ops/test-output/F6-B01a/cred.txt', 'utf8').trim().split('\n').pop());
const errors = [];

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.on('console', (m) => {
      if (m.type() === 'error' && /content security policy|refused to execute inline script/i.test(m.text())) errors.push(m.text());
    });
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(`${origin}/accounts/login/`);
    await page.locator('input[name="login"]').fill(cred.email);
    await page.locator('input[name="password"]').fill(cred.password);
    await page.locator('button[type="submit"]').click();
    await page.waitForLoadState('networkidle');
    await page.goto(`${origin}/workspace/${cred.workspace_id}/content/${cred.post_id}/?tab=caption`, { waitUntil: 'networkidle' });
    const field = page.locator('[data-caption-counter]').first();
    const count = page.locator('[data-caption-count]').first();
    assert.equal(await count.textContent(), '0');
    await field.fill('Hi \u{1F331}');
    assert.equal(await count.textContent(), '4');
    await field.fill('Řádek 1\nŘádek 2');
    const expected = String(await page.evaluate(() => {
      const f = document.querySelector('[data-caption-counter]');
      const esc = new Set(Array.from(f.dataset.escapedChars || ''));
      return Array.from(f.value).reduce((t, c) => t + 1 + Number(esc.has(c)), 0);
    }));
    assert.equal(await count.textContent(), expected);
    assert.equal(expected, String(Array.from('Řádek 1\nŘádek 2').length)); // nový řádek = 1 znak
    await field.fill('x'.repeat(2300));
    assert.equal(await count.textContent(), '2300');
    assert.ok((await count.getAttribute('class')).includes('text-red-700'));
    await field.fill('Hi \u{1F331}');
    assert.equal(await count.textContent(), '4');
    fs.mkdirSync('ops/screenshoty/F6-B01a', { recursive: true });
    await page.screenshot({ path: 'ops/screenshoty/F6-B01a/pocitadlo.png', fullPage: true });
    assert.deepEqual(errors, []);
    console.log('OK: počítadlo reaguje na psaní, bez chyby CSP');
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exitCode = 1; });
