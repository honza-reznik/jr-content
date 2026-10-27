// Skutečné cookies, tři přihlášení a čtvrtý čistý prohlížeč; pouze lokální projekt.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
assert.equal(process.env.JRC_PROJECT, 'jrc-p1b03');
const fixture = JSON.parse(fs.readFileSync('ops/test-output/P1-B03/files.json'));
const origin = process.env.JRC_PUBLIC_ORIGIN;
const base = origin + (process.env.JRC_BASE_PATH ?? '/jrc');
const url = value => new URL(value, origin).href;
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const contexts = {};
    for (const role of ['owner', 'client', 'stranger', 'anonymous']) {
      const context = await browser.newContext({ ignoreHTTPSErrors: true });
      contexts[role] = context;
      if (role === 'anonymous') continue;
      const page = await context.newPage();
      await page.goto(base + '/accounts/login/');
      await page.locator('[name=login]').fill(fixture.people[role].email);
      await page.locator('[name=password]').fill(fixture.people[role].password);
      await page.locator('button[type=submit]').click();
      await page.waitForURL(u => !u.pathname.includes('/accounts/login/'));
    }
    for (const file of fixture.files) {
      const address = url(file.url);
      assert(!/X-Amz|token=|\/storage\//i.test(address));
      const response = await contexts.owner.request.get(address, { maxRedirects: 0 });
      assert.equal(response.status(), 200, new URL(address).pathname);
      assert.equal((await response.body()).length, 300000);
      assert.equal(response.headers()['cache-control'], 'private, no-store');
      assert.equal(response.headers().location, undefined);
      assert.equal((await contexts.anonymous.request.get(address)).status(), 403);
      assert.equal((await contexts.stranger.request.get(address)).status(), 404);
      assert.equal((await contexts.client.request.get(address)).status(), file.internal ? 404 : 200);
    }
    for (const address of [fixture.range, fixture.video_range]) {
      const response = await contexts.owner.request.get(url(address), { headers: { Range: 'bytes=2-5' } });
      assert.equal(response.status(), 206);
      assert.equal(response.headers()['content-range'], 'bytes 2-5/300000');
      assert.equal((await response.body()).toString(), '2345');
    }
    for (const address of [fixture.download, fixture.video_download]) {
      const response = await contexts.owner.request.get(url(address), { maxRedirects: 0 });
      assert.equal(response.status(), 200);
      assert.match(response.headers()['content-disposition'], /attachment;.*filename/);
    }
    const longAttachment = await contexts.owner.request.get(url(fixture.long_attachment));
    assert.equal(longAttachment.status(), 200, 'Příloha s názvem dlouhým 255 znaků');
    assert.match(longAttachment.headers()['content-disposition'], /attachment; filename="a{200}"/);
    console.log(`OK P1-B03: ${fixture.files.length} typů/variant souborů; owner 200, bez přihlášení 403, nečlen 404, interní přílohy klientovi 404; Range a názvy stažení.`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
