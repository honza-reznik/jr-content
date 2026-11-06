// Only through run-browser.sh: Docker, memory cap and deadline.
const { chromium } = require('/opt/jrc-tools/node_modules/playwright');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
(async () => {
  const mode = process.argv[2];
  assert(['before', 'after'].includes(mode));
  const origin = 'http://127.0.0.1:13673';
  const base = origin + '/jrc';
  const seed = JSON.parse(await fs.readFile('/workspace/ops/test-output/P1-B25/session.json', 'utf8'));
  let ready = false;
  for (let attempt = 0; attempt < 40; attempt++) {
    try { ready = (await fetch(base + '/health/', {signal: AbortSignal.timeout(1000)})).ok; } catch {}
    if (ready) break;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  assert(ready, 'The application did not become ready.');
  const browser = await chromium.launch({args: ['--js-flags=--max-old-space-size=512']});
  const timer = setTimeout(() => process.exit(124), 240000);
  const results = [];
  try {
    for (const role of ['client', 'viewer']) {
      const context = await browser.newContext({viewport: role === 'viewer' ? {width: 390, height: 844} : {width: 1365, height: 900}});
      await context.addCookies([{name: 'sessionid', value: seed.sessions[role], url: origin}]);
      await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      const page = await context.newPage();
      page.setDefaultTimeout(15000); page.setDefaultNavigationTimeout(30000);
      for (const action of ['delete', 'channel']) {
        const post = seed.posts[role][action];
        const detail = `${base}/workspace/${seed.workspace}/content/${post}/`;
        const detailResponse = await page.goto(detail);
        assert.equal(detailResponse.status(), 200);
        const token = (await context.cookies()).find(cookie => cookie.name === 'csrftoken').value;
        const url = `${base}/workspace/${seed.workspace}/compose/${post}/delete/` + (action === 'channel' ? `?account=${seed.account}` : '');
        const status = await page.evaluate(async ({url, token}) => (await fetch(url, {method: 'POST', headers: {'X-CSRFToken': token}})).status, {url, token});
        assert(mode === 'before' ? status === 204 : [403, 404].includes(status), `${mode}/${role}/${action}: ${status}`);
        results.push({role, action, status});
        const afterResponse = await page.goto(detail);
        if (mode === 'after') {
          assert.equal(afterResponse.status(), 200);
          assert((await page.locator('body').innerText()).includes(`P1-B25 ${role} ${action}`));
        }
        await page.screenshot({path: `/workspace/ops/screenshoty/P1-B25/${mode}-${role}-${action}.png`});
      }
      await context.close();
    }
    if (mode === 'after') {
      const context = await browser.newContext();
      await context.addCookies([{name: 'sessionid', value: seed.sessions.nonmember, url: origin}]);
      await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      const page = await context.newPage();
      page.setDefaultTimeout(15000); page.setDefaultNavigationTimeout(30000);
      const response = await page.goto(`${base}/workspace/${seed.workspace}/content/${seed.posts.client.delete}/`);
      assert.equal(response.status(), 404);
      await page.getByRole('link', {name: 'Back to content board'}).waitFor();
      await page.screenshot({path: '/workspace/ops/screenshoty/P1-B25/after-nonmember.png'});
      results.push({role: 'nonmember', action: 'detail', status: response.status()});
      await context.close();
    }
    await fs.writeFile(`/workspace/ops/test-output/P1-B25/${mode}.json`, JSON.stringify(results, null, 2));
    console.log(JSON.stringify(results));
  } finally { await browser.close(); clearTimeout(timer); }
})().catch(error => { console.error(error); process.exit(1); });
