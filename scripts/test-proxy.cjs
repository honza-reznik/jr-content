// Živé ověření společné adresy; spouští se pouze v Dockeru se sítí hostitele.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const origin = process.env.JRC_PUBLIC_ORIGIN || `http://127.0.0.1:${process.env.JRC_APP_PORT || 13000}`;
const base = origin + (process.env.JRC_BASE_PATH ?? '/jrc');

(async () => {
  const health = await fetch(`${base}/health/`);
  assert.equal(health.status, 200);
  assert.deepEqual(await health.json(), { status: 'ok' });
  const root = await fetch(`${base}/`, { redirect: 'manual' });
  assert.equal(root.status, 302);
  const destination = new URL(root.headers.get('location'), origin);
  assert.equal(destination.origin, origin, 'Přesměrování opustilo společnou adresu');
  assert.equal(destination.pathname, (process.env.JRC_BASE_PATH ?? '/jrc') + '/accounts/login/');
  const css = await fetch(`${base}/static/css/dist/styles.css`);
  assert.equal(css.status, 200);
  assert((await css.text()).length > 1000, 'Chybí sestavené styly');

  fs.mkdirSync('/jrc/ops/screenshoty/F2-B03', { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [1440, 390]) {
      const context = await browser.newContext({ viewport: { width, height: 900 } });
      const foreign = [];
      const failed = [];
      // Cizí požadavky zachytíme před odesláním a test při nich selže.
      await context.route('**/*', route => {
        if (new URL(route.request().url()).origin !== origin) {
          foreign.push(route.request().url());
          return route.abort();
        }
        return route.continue();
      });
      const page = await context.newPage();
      page.on('response', response => {
        if (response.status() >= 400) failed.push([response.url(), response.status()]);
      });
      await page.goto(base + '/', { waitUntil: 'networkidle' });
      await page.locator('input[type="email"]').waitFor({ state: 'visible' });
      assert.equal(new URL(page.url()).origin, origin);
      assert.equal(await page.title(), 'Log in · JR Content');
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
        'Stránka vodorovně přetéká');
      await page.screenshot({ path: `/jrc/ops/screenshoty/F2-B03/login-${width}.png`, fullPage: true });

      // Odeslání skutečného formuláře ověří CSRF přes proxy (Host i Origin s portem).
      await page.locator('input[type="email"]').fill('f2-b03@example.invalid');
      await page.locator('input[type="password"]').fill('local-proxy-check');
      const posted = page.waitForResponse(response => response.request().method() === 'POST');
      await page.getByRole('button', { name: 'Log in', exact: true }).click();
      assert.equal((await posted).status(), 200, 'Formulář přes proxy odmítl CSRF');
      await page.waitForLoadState('networkidle');
      assert.deepEqual(foreign, [], 'Stránka zkusila cizí adresu');
      assert.deepEqual(failed, [], 'Stránka má chybové odpovědi');
      await context.close();
      console.log(`OK proxy: přihlášení, CSS, CSRF a screenshot ${width} px, bez cizích adres`);
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
