// Regrese review Astra: opožděná odpověď mezi kartami, CSRF a nepřímé přesměrování.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
assert.equal(process.env.JRC_PROJECT, 'jrc-f5b03');
assert.equal(process.env.JRC_APP_PORT, '13213');
assert.equal(process.env.JRC_S3_PORT, '13933');
assert.equal(process.env.JRC_POSTGRES_PORT, '15463');
assert.equal(process.env.JRC_BB_PORT, '18033');
const origin = 'http://127.0.0.1:13213';
const fixture = JSON.parse(fs.readFileSync('ops/test-output/F5-B03/fixture.json', 'utf8'));
const shots = path.resolve('ops/screenshoty/F5-B03');
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
async function tokens(page) {
  return page.evaluate(() => [localStorage.getItem('ff_access_token'), localStorage.getItem('ff_refresh_token')]);
}
async function loginBB(page) {
  await page.goto(`${origin}/video-sso/?next=/projects/${fixture.project}`);
  if (new URL(page.url()).pathname === '/accounts/login/') {
    await page.locator('input[name="login"]').fill(fixture.email);
    await page.locator('input[name="password"]').fill(fixture.password);
    await page.locator('button[type="submit"]').click();
  }
  await page.getByRole('button', { name: 'Continue to video' }).waitFor();
}
async function enterVideo(page) {
  await loginBB(page);
  await page.getByRole('button', { name: 'Continue to video' }).click();
  await page.waitForURL(`${origin}/video/projects/${fixture.project}`);
  await page.getByText('F5-B03 video client', { exact: true }).first().waitFor();
  assert((await tokens(page)).every(Boolean));
}
async function logoutBB(page) {
  await page.goto(`${origin}/accounts/logout/`);
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await page.waitForURL(`${origin}/accounts/login/`);
  assert.deepEqual(await tokens(page), [null, null]);
}
async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const external = [];
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin !== origin) {
        external.push(route.request().url());
        return route.abort();
      }
      return route.continue();
    });
    const page = await context.newPage();
    await enterVideo(page);
    const original = await tokens(page);

    // Nevyžádaný SSO vstup ani veřejná stránka odhlášení nesmějí měnit relaci.
    for (const target of ['/video/jrc-sso', '/video/jrc-sso#ticket=forged']) {
      await page.goto('about:blank');
      await page.goto(origin + target);
      await page.getByRole('heading', { name: 'Video sign-in could not be completed' }).waitFor();
      assert.deepEqual(await tokens(page), original);
    }
    await page.goto(`${origin}/video/jrc-logout#grant=forged`);
    await page.getByRole('button', { name: 'Sign out of video', exact: true }).waitFor();
    assert.deepEqual(await tokens(page), original);
    await page.screenshot({ path: path.join(shots, '05-direct-logout-confirmation.png') });
    console.log('PASS: neověřené SSO a přímé odhlášení zachovaly přihlášení');

    // Skutečný API server vstupenku spotřebuje; zadržíme pouze doručení odpovědi.
    const received = deferred();
    const release = deferred();
    const delivered = deferred();
    await page.route('**/auth/jrc-exchange', async route => {
      const response = await route.fetch();
      assert.equal(response.status(), 200);
      received.resolve();
      await release.promise;
      await route.fulfill({ response });
      delivered.resolve();
    });
    await loginBB(page);
    await page.getByRole('button', { name: 'Continue to video' }).click();
    await received.promise;
    const otherTab = await context.newPage();
    await logoutBB(otherTab);
    release.resolve();
    await delivered.promise;
    await page.getByRole('heading', { name: 'Video sign-in could not be completed' }).waitFor();
    assert.deepEqual(await tokens(page), [null, null]);
    assert(!(await context.cookies()).some(c => ['ff_access_token', 'ff_refresh_token'].includes(c.name)));
    await page.screenshot({ path: path.join(shots, '06-delayed-exchange-after-logout.png') });
    await page.unroute('**/auth/jrc-exchange');
    await page.goto(`${origin}/video/projects/${fixture.project}`);
    await page.waitForURL(/\/video\/login\?/);
    console.log('PASS: odpověď výměny po odhlášení v druhé kartě neobnovila relaci ani cookies');

    await enterVideo(page);
    // Stejný souběh při obnově tokenu: zneplatníme pouze přístupový token.
    const refreshed = deferred();
    const releaseRefresh = deferred();
    const deliveredRefresh = deferred();
    await page.addInitScript(() => {
      const originalFetch = window.fetch;
      window.fetch = async (...args) => {
        const response = await originalFetch(...args);
        if (String(args[0]).endsWith('/auth/refresh')) {
          const originalJson = response.json.bind(response);
          response.json = async () => {
            const data = await originalJson();
            window.__refreshRead = true;
            return data;
          };
        }
        return response;
      };
    });
    await page.route('**/auth/refresh', async route => {
      const response = await route.fetch();
      assert.equal(response.status(), 200);
      refreshed.resolve();
      await releaseRefresh.promise;
      await route.fulfill({ response });
      deliveredRefresh.resolve();
    });
    await page.evaluate(() => localStorage.setItem('ff_access_token', 'expired-test-access'));
    await page.reload();
    await refreshed.promise;
    await logoutBB(otherTab);
    releaseRefresh.resolve();
    await deliveredRefresh.promise;
    // Wait for auth.ts to consume the JSON and run its continuation before
    // inspecting storage or navigating away (which could cancel the request).
    await page.waitForFunction(() => window.__refreshRead === true);
    assert.deepEqual(await tokens(page), [null, null]);
    assert(!(await context.cookies()).some(c => ['ff_access_token', 'ff_refresh_token'].includes(c.name)));
    await page.goto(`${origin}/video/projects/${fixture.project}`);
    await page.waitForURL(/\/video\/login\?/);
    assert.deepEqual(await tokens(page), [null, null]);
    await page.unroute('**/auth/refresh');
    console.log('PASS: opožděná obnova tokenů po odhlášení neobnovila relaci');

    await enterVideo(page);
    // Celý řetězec: BB odmítne login jako cíl; i podvržená odpověď API selže na klientu.
    const csrf = (await context.cookies()).find(c => c.name === 'csrftoken').value;
    const evil = '/login?from=https://foreign.invalid/';
    const refused = await context.request.post(`${origin}/video-sso/`, {
      form: { csrfmiddlewaretoken: csrf, next: evil }, headers: { Origin: origin },
    });
    assert.equal(refused.status(), 400);
    await page.route('**/auth/jrc-exchange', async route => {
      const response = await route.fetch();
      assert.equal(response.status(), 200);
      const json = await response.json();
      await route.fulfill({ response, json: { ...json, next: evil } });
    });
    await loginBB(page);
    await page.getByRole('button', { name: 'Continue to video' }).click();
    await page.getByRole('heading', { name: 'Video sign-in could not be completed' }).waitFor();
    assert((await tokens(page)).every(Boolean), 'Odmítnutá navigace nesmí mazat dosavadní relaci');
    await page.unroute('**/auth/jrc-exchange');
    for (const from of ['https://foreign.invalid/', '//foreign.invalid/', '/login?from=https://foreign.invalid/', '/%2f%2fforeign.invalid']) {
      await page.goto(`${origin}/video/login?from=${encodeURIComponent(from)}`);
      await page.waitForURL(`${origin}/video/projects`);
      await page.getByRole('heading', { name: 'Projects', exact: true }).waitFor();
    }
    await page.goto(`${origin}/video/login?from=${encodeURIComponent(`/projects/${fixture.project}`)}`);
    await page.waitForURL(`${origin}/video/projects/${fixture.project}`);
    await page.getByText('F5-B03 video client', { exact: true }).first().waitFor();
    await page.screenshot({ path: path.join(shots, '07-safe-login-destination.png') });
    assert.deepEqual(external, [], 'Žádný pokus o otevření cizí domény');
    console.log('PASS: BB → SSO → login/from bez otevřeného přesměrování; platná cesta funguje');
    await logoutBB(otherTab);
    await context.close();
  } finally { await browser.close(); }
}
main().catch(error => {
  console.error(error.message.replace(/#(?:ticket|grant)=[^\s]+/g, '#[redacted]').replace(/v1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, '[redacted]'));
  process.exitCode = 1;
});
