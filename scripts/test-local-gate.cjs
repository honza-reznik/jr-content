// Ověření skutečných veřejných adres; pouze Docker se sítí hostitele.
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
const port = (name, fallback) => process.env[name] || fallback;
const url = (name, fallback) => `http://127.0.0.1:${port(name, fallback)}`;

(async () => {
  const api = url('JRC_FF_API_PORT', 18022);
  const web = url('JRC_FF_WEB_PORT', 13022);
  const bb = url('JRC_APP_PORT', 13000);
  const s3 = url('JRC_S3_PORT', 13900);
  assert.equal((await fetch(`${api}/health`)).status, 200);
  assert.equal((await fetch(`${bb}/health/`)).status, 200);
  assert.equal((await fetch(`${s3}/`)).status, 403);
  assert.equal(execFileSync('psql', ['-Atc', 'SELECT 1'], { encoding: 'utf8',
    env: { ...process.env, PGHOST: '127.0.0.1', PGPORT: port('JRC_POSTGRES_PORT', 15432) }
  }).trim(), '1');
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext();
    const foreign = [];
    const fonts = [];
    const responses = [];
    const allowed = new Set([api, web, s3]);
    await context.route('**/*', route => {
      const request = route.request();
      if (!allowed.has(new URL(request.url()).origin)) {
        foreign.push(request.url());
        return route.abort();
      }
      if (request.resourceType() === 'font') fonts.push(request.url());
      return route.continue();
    });
    const page = await context.newPage();
    page.on('response', response => responses.push([response.url(), response.status()]));
    // Formulář je viditelný už ze serverového HTML; API se zavolá až po spuštění
    // JavaScriptu. Při zatíženém stroji samotné networkidle tento krok nepokrývá.
    const apiReady = page.waitForResponse(response => response.url().startsWith(`${api}/`)
      && response.status() === 200, { timeout: 60000 });
    await page.goto(`${web}/video/login`, { waitUntil: 'networkidle' });
    await page.locator('input[type="email"]').waitFor({ state: 'visible' });
    await apiReady;
    await page.evaluate(() => document.fonts.ready);
    assert(fonts.length > 0 && fonts.every(font => font.startsWith(`${web}/video/_next/`)));
    assert(responses.some(([address, status]) => address.startsWith(api) && status === 200), 'Prohlížeč nedosáhne na API');
    assert.deepEqual(foreign, [], 'Skutečný web zkusil cizí doménu');
    console.log('OK brána: API, Brightbean, S3, PostgreSQL; Chromium přes skutečný web načetl místní písmo/API bez cizí domény.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
