// R1/R2 živě: odkaz se stahováním (starší data) nesmí stahovat; přehrávání ano. Jen Docker projekt jrc-p2b18.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
assert.equal(process.env.JRC_PROJECT, 'jrc-p2b18');
const out = 'ops/test-output/P2-B18';
const shots = 'ops/screenshoty/sprint-feedback/P2-B18';
const fixture = JSON.parse(fs.readFileSync(`${out}/fixture.json`));
const data = JSON.parse(fs.readFileSync(`${out}/versions.json`));
const share = JSON.parse(fs.readFileSync(`${out}/share.json`));
const root = 'http://127.0.0.1:13918/jrc';
const detail = `${root}/workspace/${fixture.workspace}/content/${fixture.post}/?tab=video`;
(async () => {
 const browser = await chromium.launch({ headless: true, args: ['--disable-dev-shm-usage'] });
 try {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }])
  for (const role of ['editor', 'client', 'viewer', 'manager', 'owner']) {
   const context = await browser.newContext({ viewport });
   await context.route(/^https?:\/\/(?!127\.0\.0\.1:13918(?:\/|$))/, r => r.abort());
   const page = await context.newPage(); page.setDefaultTimeout(30000);
   await page.goto(`${root}/accounts/login/`);
   await page.locator('[name=login]').fill(fixture[role].email);
   await page.locator('[name=password]').fill(fixture[role].password);
   await Promise.all([page.waitForURL(u => !u.pathname.includes('/accounts/login')), page.locator('button[type=submit]').click()]);
   await page.goto(detail);
   const frame = await (await page.locator('iframe').first().elementHandle()).contentFrame();
   await frame.locator('video').first().waitFor({ timeout: 90000 });
   const headers = { Authorization: 'Bearer ' + await frame.evaluate(() => localStorage.getItem('ff_access_token')) };
   const api = `${root}/video-api`;
   const base = `${api}/share/${share.token}`;
   // Přehrávání přes odkaz funguje (HLS playlist se skutečně načte).
   const play = await context.request.get(`${base}/stream/${data.asset}`, { headers });
   assert.equal(play.status(), 200, `${role} play`);
   const url = (await play.json()).url;
   assert(url.includes('/stream/hls/'), `${role} hls url`);
   const playlist = await context.request.get(url.startsWith('http') ? url : `${api}${url}`);
   assert.equal(playlist.status(), 200, `${role} playlist`);
   assert.equal((await context.request.get(`${base}`, { headers })).status(), 200);
   assert.equal((await (await context.request.get(`${base}`, { headers })).json()).allow_download, false);
   // Stažení přes odkaz: všechny varianty 403.
   for (const q of ['', '&rendition=original', '&rendition=transcode', '&prefer_original=true'])
     assert.equal((await context.request.get(`${base}/stream/${data.asset}?download=true${q}`, { headers })).status(), 403, `${role} dl ${q}`);
   assert.equal((await context.request.get(`${base}/downloads/${data.asset}`, { headers })).status(), 403);
   // Nový odkaz a úprava: stahování se vynutí vypnuté (jen editor a výše smí odkaz tvořit).
   const created = await context.request.post(`${api}/assets/${data.asset}/share`, { headers, data: { allow_download: true } });
   if (['editor', 'manager', 'owner'].includes(role)) {
    assert.equal(created.status(), 201, `${role} create`);
    const body = await created.json();
    assert.equal(body.allow_download, false);
    const patched = await context.request.patch(`${api}/share/${body.token}`, { headers, data: { allow_download: true } });
    assert.equal(patched.status(), 200);
    assert.equal((await patched.json()).allow_download, false);
   } else assert.equal(created.status(), 403, `${role} create denied`);
   await page.screenshot({ path: `${shots}/r1-share-${role}-${viewport.width}.png` });
   await context.close();
   console.log(`OK ${role} ${viewport.width}: přehrávání přes odkaz 200, stažení 403, nový/upravený odkaz bez stahování`);
  }
 } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
