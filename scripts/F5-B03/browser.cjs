// F5-B03: jen Docker, Chromium přes skutečný port proxy, žádné cizí účty.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { chromium } = require('playwright');

assert.equal(process.env.JRC_PROJECT, 'jrc-f5b03');
const origin = `http://127.0.0.1:${process.env.JRC_APP_PORT}`;
const fixture = JSON.parse(fs.readFileSync('ops/test-output/F5-B03/fixture.json', 'utf8'));
const shots = path.resolve('ops/screenshoty/F5-B03');
fs.mkdirSync(shots, { recursive: true });

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let ticket;
    let exchanges = 0;
    const requestUrls = [];
    const referrers = [];
    page.on('request', request => {
      requestUrls.push(request.url());
      referrers.push(request.headers().referer || '');
      if (request.url().endsWith('/auth/jrc-exchange')) {
        ticket = request.postDataJSON().ticket;
        exchanges++;
      }
    });

    await page.goto(`${origin}/video-sso/?next=/projects/${fixture.project}`);
    console.log('SSO entry:', new URL(page.url()).pathname);
    await page.locator('input[name="login"]').fill(fixture.email);
    await page.locator('input[name="password"]').fill(fixture.password);
    await page.locator('button[type="submit"]').click();
    console.log('BB login submitted');
    await page.getByRole('button', { name: 'Continue to video' }).waitFor();
    await page.screenshot({ path: path.join(shots, '01-brightbean-open-video.png') });
    await page.getByRole('button', { name: 'Continue to video' }).click();
    await page.waitForURL(`${origin}/video/projects/${fixture.project}`);
    await page.getByText('F5-B03 video client', { exact: true }).first().waitFor();
    assert.equal(exchanges, 1, 'Vstupenka se vyměnila právě jednou');
    const stored = await page.evaluate(() => ({ access: localStorage.getItem('ff_access_token'), refresh: localStorage.getItem('ff_refresh_token') }));
    assert(stored.access && stored.refresh, 'Chybí přihlášení FreeFrame');
    const me = await page.evaluate(async () => {
      const r = await fetch('/video-api/auth/me', { headers: { Authorization: `Bearer ${localStorage.getItem('ff_access_token')}` } });
      return r.ok ? (await r.json()).email : null;
    });
    assert.equal(me, fixture.email);
    assert(requestUrls.every(url => !url.includes('ticket=') && !url.includes(ticket)), 'Vstupenka nesmí být v URL požadavku');
    assert(referrers.every(value => !value.includes('ticket=') && !value.includes(ticket)), 'Vstupenka nesmí být v Referer');
    const cdp = await context.newCDPSession(page);
    const history = await cdp.send('Page.getNavigationHistory');
    assert(history.entries.every(entry => !entry.url.includes('ticket=') && !entry.url.includes(ticket)), 'Vstupenka nesmí zůstat v historii');
    assert(!(await context.cookies()).some(cookie => cookie.name === 'jrc_sso_binding'), 'Kontrolní cookie se musí spotřebovat');
    await page.screenshot({ path: path.join(shots, '02-freeframe-signed-in.png') });
    console.log('OK: správný účet FF, historie a adresy požadavků bez vstupenky');

    // Jednorázovost prověříme i při znovuvytvoření browser bindingu.
    const replay = await context.request.post(`${origin}/video-api/auth/jrc-exchange`, { data: { ticket } });
    assert.equal(replay.status(), 401);
    assert(!(await replay.text()).includes(ticket), 'Odmítnutí nesmí vrátit vstupenku');

    await page.goto(`${origin}/accounts/logout/?next=https://foreign.invalid`);
    await page.getByRole('button', { name: 'Sign out', exact: true }).click();
    await page.waitForURL(`${origin}/accounts/login/`);
    assert.equal(await page.evaluate(() => localStorage.getItem('ff_access_token')), null);
    assert.equal(await page.evaluate(() => localStorage.getItem('ff_refresh_token')), null);
    assert(!(await context.cookies()).some(cookie => ['ff_access_token', 'ff_refresh_token', 'ff_setup_done', 'jrc_sso_binding'].includes(cookie.name)));
    await page.screenshot({ path: path.join(shots, '03-brightbean-signed-out.png') });
    await page.goto(`${origin}/video/projects/${fixture.project}`);
    await page.waitForURL(/\/video\/login\?/);
    assert.equal((await context.request.get(`${origin}/video-api/auth/me`)).status(), 401);

    await page.goto(`${origin}/video/jrc-sso#ticket=${ticket}`);
    await page.getByRole('heading', { name: 'Video sign-in could not be completed' }).waitFor();
    assert(!page.url().includes('#'), 'Fragment musí být odstraněn i při chybě');
    assert.equal(await page.evaluate(() => localStorage.getItem('ff_access_token')), null);
    assert.equal(exchanges, 1, 'Bez kontrolní cookie se vstupenka nesmí odeslat');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(shots, '04-replay-refused-mobile.png') });
    console.log('OK: společné odhlášení a odmítnuté opakování bez kontrolní cookie');

    // Platná, dosud nepoužitá vstupenka z jiného prohlížeče nepřihlásí oběť.
    const issuer = await browser.newContext();
    const issuerPage = await issuer.newPage();
    await issuerPage.goto(`${origin}/video-sso/`);
    await issuerPage.locator('input[name="login"]').fill(fixture.email);
    await issuerPage.locator('input[name="password"]').fill(fixture.password);
    await issuerPage.locator('button[type="submit"]').click();
    console.log('BB login in second browser:', new URL(issuerPage.url()).pathname);
    await issuerPage.getByRole('button', { name: 'Continue to video' }).waitFor();
    const csrf = await issuerPage.locator('form[action="/video-sso/"] input[name="csrfmiddlewaretoken"]').inputValue();
    const issued = await issuer.request.post(`${origin}/video-sso/`, {
      form: { csrfmiddlewaretoken: csrf, next: `/projects/${fixture.project}` },
      headers: { Origin: origin, Referer: `${origin}/video-sso/` }, maxRedirects: 0,
    });
    assert.equal(issued.status(), 302);
    const issuedLocation = issued.headers().location;
    const freshTicket = new URL(issuedLocation, origin).hash.slice('#ticket='.length);
    await page.goto('about:blank');
    await page.goto(origin + issuedLocation);
    await page.getByRole('heading', { name: 'Video sign-in could not be completed' }).waitFor();
    assert.equal(exchanges, 1, 'Cizí platná vstupenka nesmí přihlásit oběť');
    assert.equal(await page.evaluate(() => localStorage.getItem('ff_access_token')), null);
    console.log('OK: platná cizí vstupenka odmítnutá');
    // Vydávající prohlížeč ji naopak smí uplatnit.
    await issuerPage.goto(origin + issuedLocation);
    await issuerPage.waitForURL(`${origin}/video/projects/${fixture.project}`);
    assert(await issuerPage.evaluate(() => Boolean(localStorage.getItem('ff_access_token'))));
    await issuer.close();

    // Opakované použití se shodným otiskem musí dojít až k odmítnutí API.
    await context.addCookies([{ name: 'jrc_sso_binding', value: createHash('sha256').update(freshTicket).digest('hex'),
      domain: '127.0.0.1', path: '/video', sameSite: 'Strict' }]);
    await page.goto('about:blank');
    await page.goto(`${origin}/video/jrc-sso#ticket=${freshTicket}`);
    await page.getByRole('heading', { name: 'Video sign-in could not be completed' }).waitFor();
    assert.equal(exchanges, 2, 'Opakovaná vstupenka se shodným otiskem má být odmítnuta výměnou API');
    assert.equal(await page.evaluate(() => localStorage.getItem('ff_access_token')), null);
    assert(!(await context.cookies()).some(cookie => cookie.name === 'jrc_sso_binding'));
    assert.deepEqual(errors, [], 'Chyby JavaScriptu');
    console.log('OK: BB login → FF bez hesla; historie a HTTP URL bez vstupenky; CSRF/browser binding; replay odmítnutý; BB logout smaže FF přihlášení; 4 snímky.');
  } finally {
    await browser.close();
  }
}
main().catch(error => {
  // Playwright may include the navigation URL in an error; redact fragments
  // and signed tickets before writing even a local test log.
  console.error(error.message.replace(/#ticket=[^\s]+/g, '#ticket=[redacted]').replace(/v1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, '[redacted]').replace(/value="[^"]*"/g, 'value="[redacted]"'));
  process.exitCode = 1;
});
