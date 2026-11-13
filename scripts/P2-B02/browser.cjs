// Pouze Docker, vlastní projekt; spuštění vždy timeout 600s a Node heap 2048 MB.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
assert.equal(process.env.JRC_PROJECT, 'jrc-p2b02');
const fixture = JSON.parse(fs.readFileSync('ops/test-output/P2-B02/fixture.json'));
const video = JSON.parse(fs.readFileSync('ops/test-output/P2-B02/video.json'));
const base = 'http://127.0.0.1:13902/jrc';
const target = `/projects/${fixture.project}/assets/${video.asset}`;
const shots = 'ops/screenshoty/sprint-feedback/P2-B02';
fs.mkdirSync(shots, { recursive: true });

async function login(page, user) {
  await page.locator('input[name="login"]').fill(user.email);
  await page.locator('input[name="password"]').fill(user.password);
  await page.locator('button[type="submit"]').click();
}
async function shot(page, name) {
  await page.screenshot({ path: `${shots}/${name}.png`, fullPage: true });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name}: vodorovné přetékání`);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [device, viewport] of [['desktop', { width: 1440, height: 900 }], ['mobile', { width: 390, height: 844 }]]) {
      const context = await browser.newContext({ viewport });
      const page = await context.newPage();
      page.setDefaultTimeout(90000);
      page.setDefaultNavigationTimeout(120000);
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('response', response => {
        if (response.status() >= 400) console.log(`${device}: HTTP ${response.status()} ${new URL(response.url()).pathname}`);
      });
      let hlsUrl;
      page.on('request', request => { if (/\/stream\/hls\/.*master.m3u8/.test(request.url())) hlsUrl = request.url(); });
      await page.goto(`${base}/video${target}`);
      await page.waitForURL(/\/jrc\/accounts\/login\//);
      assert.equal(await page.locator('input[name="next"]').inputValue(), `/jrc/video-sso/?next=${encodeURIComponent(target)}`);
      await page.locator('input[name="login"]').focus();
      await shot(page, `${device}-01-login`);
      await page.locator('input[name="login"]').fill('unknown-p2b02@example.invalid');
      await page.locator('input[name="password"]').fill('wrong-test-password');
      await Promise.all([page.waitForNavigation(), page.locator('button[type="submit"]').click()]);
      assert(new URL(page.url()).pathname.endsWith('/accounts/login/'));
      await shot(page, `${device}-02-login-error`);
      console.log(`${device}: přihlášení JR Content a odmítnuté heslo`);
      await login(page, fixture.users.owner);
      await page.getByRole('button', { name: 'Continue to video' }).waitFor();
      await shot(page, `${device}-03-sso`);
      console.log(`${device}: SSO potvrzení`);
      await page.getByRole('button', { name: 'Continue to video' }).click();
      await page.waitForURL(`${base}/video${target}`);
      const player = page.locator('video').first();
      await player.waitFor();
      console.log(`${device}: načten přehrávač`);
      await page.waitForFunction(() => document.querySelector('video')?.readyState >= 2);
      await player.evaluate(el => el.play());
      await page.waitForFunction(() => document.querySelector('video')?.currentTime > 0);
      await player.evaluate(el => el.pause());
      await shot(page, `${device}-04-player`);
      console.log(`${device}: video se přehrává`);
      assert(hlsUrl, 'Přehrávač nenačetl HLS');
      assert.equal((await context.request.get(hlsUrl)).status(), 200);
      await page.goto(`${base}/accounts/logout/`);
      await page.getByRole('button', { name: 'Sign out', exact: true }).click();
      await page.waitForURL(`${base}/accounts/login/`);
      assert.equal(await page.evaluate(() => localStorage.getItem('ff_access_token')), null);
      assert.equal(await page.evaluate(() => localStorage.getItem('ff_refresh_token')), null);
      assert(!(await context.cookies()).some(cookie => ['ff_access_token', 'ff_refresh_token'].includes(cookie.name)));
      const afterLogout = await context.request.get(hlsUrl);
      assert([401, 403].includes(afterLogout.status()), `HLS po odhlášení: ${afterLogout.status()}`);
      await page.goto(`${base}/video${target}`);
      await page.waitForURL(/\/jrc\/accounts\/login\//);
      await shot(page, `${device}-05-logged-out`);
      await page.goto(`${base}/video/jrc-sso`);
      await page.getByRole('heading', { name: 'Video sign-in could not be completed' }).waitFor();
      await shot(page, `${device}-06-sso-error`);
      assert.equal((await context.request.get(`${base}/video-api/share/${video.managed_share}`)).status(), 403);
      await page.goto(`${base}/video/share/${video.share.public}`);
      await page.locator('video').first().waitFor();
      await page.locator('video').first().evaluate(el => el.play());
      await page.waitForFunction(() => document.querySelector('video')?.currentTime > 0);
      await page.locator('video').first().evaluate(el => el.pause());
      await shot(page, `${device}-07-share-public`);
      await page.goto(`${base}/video/share/${video.share.protected}`);
      await page.getByPlaceholder('Enter password…').fill('wrong');
      await page.getByRole('button', { name: 'Access link', exact: true }).click();
      await page.getByText('Incorrect password').waitFor();
      await shot(page, `${device}-08-share-password`);
      await page.getByPlaceholder('Enter password…').fill('p2b02-share-test');
      await page.getByRole('button', { name: 'Access link', exact: true }).click();
      await page.locator('video').first().waitFor();
      assert(page.url().includes('/video/share/'));
      await page.goto(`${base}/video/share/invalid-p2b02-token`);
      await page.getByText(/not found|unavailable|invalid/i).first().waitFor();
      await shot(page, `${device}-09-share-invalid`);
      // An authenticated Brightbean visitor without workspace access gets one clear refusal.
      await page.goto(`${base}/video${target}`);
      await login(page, fixture.users.outsider);
      await page.getByRole('heading', { name: /access|permission/i }).waitFor();
      await shot(page, `${device}-10-no-access`);
      assert.equal(await page.evaluate(() => localStorage.getItem('ff_access_token')), null);
      assert.deepEqual(errors, []);
      await context.close();
      console.log(`OK ${device}: anonymní přehrávač → Brightbean → správné video, odhlášení včetně HLS, share, chyby a zákaz přístupu.`);
    }
  } finally { await browser.close(); }
})().catch(error => {
  console.error(error.message.replace(/#ticket=\S+/g, '#ticket=[redacted]'));
  process.exitCode = 1;
});
