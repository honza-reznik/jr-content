// Skutečný web, API a Garage; jen vlastní projekt a vymyšlená data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
assert.equal(process.env.JRC_PROJECT, 'jrc-f5b04');
assert.equal(process.env.JRC_APP_PORT, '13243');
assert.equal(process.env.JRC_S3_PORT, '13963');
const fixture = JSON.parse(fs.readFileSync(process.env.F5B04_FIXTURE, 'utf8'));
const origin = 'http://127.0.0.1:13243';
const api = origin + '/video-api';
const s3 = 'http://127.0.0.1:13963';

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext();
    await context.route('**/*', route => {
      assert([origin, s3].includes(new URL(route.request().url()).origin));
      return route.continue();
    });
    await context.addInitScript(({ expired, refresh }) => {
      localStorage.setItem('ff_access_token', expired);
      localStorage.setItem('ff_refresh_token', refresh);
      localStorage.setItem('ff_session_version', 'expiry-regression');
    }, fixture);
    const page = await context.newPage();
    page.setDefaultTimeout(60000);
    const responses = [], errors = [];
    page.on('response', r => responses.push([new URL(r.url()).pathname, r.status()]));
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(`${origin}/video/share/${fixture.share}`);
    await page.getByText('Photo 01.png', { exact: true }).first().waitFor();
    assert(responses.some(([p, s]) => p.startsWith('/video-api/share/') && s === 401));
    assert(responses.some(([p, s]) => p === '/video-api/auth/refresh' && s === 200));
    assert(responses.some(([p, s]) => p.startsWith('/video-api/share/') && s === 200));
    const access = await page.evaluate(() => localStorage.getItem('ff_access_token'));
    assert(access && access !== fixture.expired);
    assert.deepEqual(errors, []);
    console.log('OK Chromium: skutečný vypršený JWT → 401 → obnova relace → sdílená složka 200.');
    const headers = { Authorization: `Bearer ${access}` };
    const root = `${api}/share/${fixture.share}`;
    const thumbnailPath = `${root}/thumbnail/${fixture.images[0]}`;
    const thumbnail = await context.request.get(thumbnailPath, { headers });
    assert.equal(thumbnail.status(), 200);
    const thumbUrl = (await thumbnail.json()).url;
    const commentsPath = `${root}/comments?asset_id=${fixture.images[0]}`;
    const comments = await context.request.get(commentsPath, { headers });
    assert.equal(comments.status(), 200);
    const attachmentUrl = (await comments.json())[0].attachments[0].url;
    const urls = [thumbUrl, attachmentUrl];
    const issuedAt = Date.now();
    for (const url of urls) {
      assert.equal(new URL(url).origin, s3);
      assert.equal(new URL(url).searchParams.get('X-Amz-Expires'), '180');
      assert.equal((await context.request.get(url)).status(), 200);
    }
    const removed = await context.request.delete(`${api}/projects/${fixture.project}/members/${fixture.user}`, {
      headers: { Authorization: `Bearer ${fixture.admin}` },
    });
    assert.equal(removed.status(), 204);
    for (const path of [thumbnailPath, commentsPath]) {
      assert.equal((await context.request.get(path, { headers })).status(), 403);
    }
    for (const url of urls) assert.equal((await context.request.get(url)).status(), 200);
    console.log('OK: odebrání během platnosti podpisů odmítne obnovu; vydané adresy jsou dosud platné.');
    // Bez změny hodin serveru či náhrady odpovědí ověříme skutečné vypršení S3.
    while (Date.now() - issuedAt < 182000) {
      await new Promise(resolve => setTimeout(resolve, Math.min(30000, 182000 - (Date.now() - issuedAt))));
      console.log(`Čekání na vypršení skutečných podpisů: ${Math.floor((Date.now() - issuedAt) / 1000)} s.`);
    }
    for (const url of urls) {
      const expired = await context.request.get(url);
      // Garage uses InvalidRequest/400 for an expired SigV4 date.
      assert.equal(expired.status(), 400);
      const body = await expired.text();
      assert(body.includes('<Code>InvalidRequest</Code>'));
      assert(body.includes('Date is too old'));
    }
    assert.equal((await context.request.get(`${api}/auth/me`, { headers })).status(), 200);
    console.log('OK Garage: původní náhled i příloha po 180 s odmítnuty (400 InvalidRequest: Date is too old), přihlašovací token zůstává platný.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
