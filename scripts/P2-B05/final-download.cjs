// Chromium needs a UTF-8 locale to retain non-ASCII download names.
process.env.LANG = 'C.UTF-8';
process.env.LC_ALL = 'C.UTF-8';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const crypto = require('node:crypto');
const { chromium } = require('playwright');
assert.equal(process.env.JRC_PROJECT, 'jrc-p2b05');
const output = 'ops/test-output/P2-B05';
const fixture = JSON.parse(fs.readFileSync(`${output}/fixture.json`));
const final = JSON.parse(fs.readFileSync(`${output}/final.json`));
const fallback = process.env.P2B05_FALLBACK === '1';
const root = 'http://127.0.0.1:13905/jrc';
const url = `${root}/workspace/${fixture.workspace}/content/${fixture.post}/video/channels/${final.variant}/final/download/`;
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
(async () => {
 const browser = await chromium.launch({ headless: true });
 try {
  for (const viewport of [{ width:1440,height:900 }, { width:390,height:844 }]) {
   const context = await browser.newContext({ viewport, acceptDownloads: true });
   await context.route(/^https?:\/\/(?!127\.0\.0\.1:13905(?:\/|$))/, route => route.abort());
   const page = await context.newPage();
   page.setDefaultTimeout(30000);
   await page.goto(`${root}/accounts/login/`);
   await page.locator('[name=login]').fill(fixture.editor.email);
   await page.locator('[name=password]').fill(fixture.editor.password);
   await Promise.all([page.waitForURL(u => !u.pathname.includes('/accounts/login')), page.locator('button[type=submit]').click()]);
   if (fallback) {
    await page.goto(url);
    await page.getByRole('heading', { name: 'Original video unavailable' }).waitFor();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: `ops/screenshoty/sprint-feedback/P2-B05/fallback-${viewport.width}.png` });
    const event = page.waitForEvent('download');
    await page.getByRole('link', { name:'Download converted MP4' }).click();
    const download = await event;
    assert(download.suggestedFilename().endsWith('.mp4'));
    assert.notEqual(hash(fs.readFileSync(await download.path())), hash(fs.readFileSync(`${output}/Žlutý klip – original.MOV`)));
   } else {
    const event = page.waitForEvent('download');
    // A file navigation emits a download instead of a document navigation.
    const navigation = page.goto(url).catch(error => { if (!/Download is starting|ERR_ABORTED/.test(error.message)) throw error; });
    const download = await event;
    await navigation;
    assert.equal(download.suggestedFilename(), 'Žlutý klip – original.MOV');
    assert.equal(hash(fs.readFileSync(await download.path())), hash(fs.readFileSync(`${output}/Žlutý klip – original.MOV`)));
   }
   await context.close();
  }
  for (const role of ['client', 'viewer']) {
   const context = await browser.newContext(); const page = await context.newPage();
   await page.goto(`${root}/accounts/login/`);
   await page.locator('[name=login]').fill(fixture[role].email);
   await page.locator('[name=password]').fill(fixture[role].password);
   await Promise.all([page.waitForURL(u => !u.pathname.includes('/accounts/login')), page.locator('button[type=submit]').click()]);
   assert.equal((await context.request.get(url)).status(), role === "client" ? 200 : 403, `${role}: existing final download permission`);
   await context.close();
  }
  console.log(`OK final download ${fallback ? 'explained MP4 fallback' : 'original filename + SHA256'}, desktop and mobile`);
 } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
