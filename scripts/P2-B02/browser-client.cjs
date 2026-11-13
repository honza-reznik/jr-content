// Druhá role a živé odmítnutí místního API, jen vlastní Docker projekt.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
assert.equal(process.env.JRC_PROJECT, 'jrc-p2b02');
const fixture = JSON.parse(fs.readFileSync('ops/test-output/P2-B02/fixture.json'));
const video = JSON.parse(fs.readFileSync('ops/test-output/P2-B02/video.json'));
const base = 'http://127.0.0.1:13902/jrc';
const target = `/projects/${fixture.project}/assets/${video.asset}`;
(async () => {
  const browser = await chromium.launch();
  try {
    for (const [device, viewport] of [['desktop', { width: 1440, height: 900 }], ['mobile', { width: 390, height: 844 }]]) {
      const context = await browser.newContext({ viewport });
      for (const action of ['login', 'send-magic-code', 'verify-magic-code']) {
        for (const email of [fixture.users.client.email, 'unknown@example.com', 'jrc-sync@example.com']) {
          const response = await context.request.post(`${base}/video-api/auth/${action}`, {
            data: { email, password: 'wrong-test-password', code: '123456' }, headers: { 'X-Real-IP': '' },
          });
          assert.equal(response.status(), 403);
          assert.deepEqual(await response.json(), { detail: 'Sign in through JR Content.' });
        }
      }
      for (const path of ['/login', '/magic-code', '/set-password', '/accept-invite', '/forgot-password', '/invite/test', '/setup']) {
        const response = await context.request.get(`${base}/video${path}`, { maxRedirects: 0 });
        assert.equal(response.status(), 307);
        assert(new URL(response.headers().location).pathname.endsWith('/jrc/accounts/login/'));
      }
      const page = await context.newPage();
      page.setDefaultTimeout(90000);
      await page.goto(`${base}/video${target}`);
      await page.locator('input[name="login"]').fill(fixture.users.client.email);
      await page.locator('input[name="password"]').fill(fixture.users.client.password);
      await page.locator('button[type="submit"]').click();
      await page.getByRole('button', { name: 'Continue to video' }).click();
      await page.waitForURL(`${base}/video${target}`);
      await page.locator('video').first().waitFor();
      await page.waitForFunction(() => document.querySelector('video')?.readyState >= 2);
      await page.locator('video').first().evaluate(el => el.play());
      await page.waitForFunction(() => document.querySelector('video')?.currentTime > 0);
      await page.locator('video').first().evaluate(el => el.pause());
      await page.screenshot({ path: `ops/screenshoty/sprint-feedback/P2-B02/${device}-11-client-player.png`, fullPage: true });
      await page.goto(`${base}/accounts/logout/`);
      await page.getByRole('button', { name: 'Sign out', exact: true }).click();
      await page.waitForURL(`${base}/accounts/login/`);
      assert.equal(await page.evaluate(() => localStorage.getItem('ff_access_token')), null);
      await context.close();
      console.log(`OK ${device}: role client přehrála původní video a odhlásila se; místní API i auth stránky odmítnuté/přesměrované.`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message.replace(/#ticket=\S+/g, '#ticket=[redacted]')); process.exitCode = 1; });
