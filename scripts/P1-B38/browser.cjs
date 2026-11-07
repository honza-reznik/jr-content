// P1-B38: živé ověření – přejmenování obsahu (AK1), zpráva po smazání (AK2), odmítnuté stránky v rozvržení (AK3).
// Docker runner dává cgroup, limit paměti Node a tvrdý strop.
const {chromium} = require('/opt/jrc-tools/node_modules/playwright');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
const phase = process.argv[2];
const out = '/workspace/ops/test-output/P1-B38', shots = '/workspace/ops/screenshoty/P1-B38';
(async () => {
  await fs.mkdir(shots, {recursive: true});
  const seed = JSON.parse(await fs.readFile(`${out}/session.json`, 'utf8'));
  const origin = 'http://127.0.0.1:13803', base = origin + '/jrc';
  const browser = await chromium.launch({args: ['--js-flags=--max-old-space-size=512']});
  const deadline = setTimeout(() => process.exit(124), 480000);
  const results = [];
  const check = (name, actual, expected) => {
    results.push({name, actual, expected, ok: JSON.stringify(actual) === JSON.stringify(expected)});
    if (phase === 'after') assert.deepEqual(actual, expected, name);
  };
  async function session(role) {
    const ctx = await browser.newContext({viewport: {width: 1365, height: 900}});
    await ctx.addCookies([{name: 'sessionid', value: seed.sessions[role], url: origin}]);
    await ctx.route('**/*', r => new URL(r.request().url()).origin === origin ? r.continue() : r.abort());
    const page = await ctx.newPage();
    page.setDefaultTimeout(15000); page.setDefaultNavigationTimeout(30000);
    const errors = [];
    page.on('console', m => { if (m.type() === 'error') errors.push(page.url() + ' :: ' + m.text().slice(0, 160)); });
    page.on('response', r => { if (r.status() >= 500) errors.push(`${r.status()} ${r.url()}`); });
    return {ctx, page, errors};
  }
  async function post(s, url, data) {
    const token = (await s.ctx.cookies()).find(c => c.name === 'csrftoken').value;
    return s.page.evaluate(async ({url, data, token}) => {
      const r = await fetch(url, {method: 'POST', redirect: 'manual', body: new URLSearchParams(data), headers: {'X-CSRFToken': token}});
      let body = ''; try { body = await r.text(); } catch (e) {}
      return {status: r.type === 'opaqueredirect' ? 302 : r.status, body};
    }, {url, data, token});
  }
  const owner = await session('owner'), client = await session('client');
  const REQ = 'required_internal_and_client';

  // AK1: přejmenování skutečnými klepnutími v detailu.
  for (const mode of [REQ, 'optional']) {
    const ws = seed.workspaces[mode], page = owner.page;
    const detail = `${base}/workspace/${ws.id}/content/${ws.post}/`;
    await page.goto(detail);
    check(`${mode}: heading before`, (await page.locator('h1').first().innerText()).trim(), `B38 ${mode} rename`);
    await page.locator('#content-rename summary').click();
    await page.locator('#content-title').fill('  Nový název obsahu ' + mode.slice(0, 3) + '  ');
    await page.screenshot({path: `${shots}/${phase}-${mode}-1-rename-form.png`, fullPage: true});
    await Promise.all([page.waitForNavigation(), page.getByRole('button', {name: 'Save title'}).click()]);
    check(`${mode}: heading after`, (await page.locator('h1').first().innerText()).trim(), 'Nový název obsahu ' + mode.slice(0, 3));
    const body = await page.locator('body').innerText();
    check(`${mode}: message`, body.includes(mode === REQ ? 'go back to review for approval' : 'Content renamed.'), true);
    await page.screenshot({path: `${shots}/${phase}-${mode}-2-renamed.png`, fullPage: true});
    await page.goto(`${detail}?tab=history`);
    check(`${mode}: history`, (await page.locator('body').innerText()).includes('Renamed from “B38 ' + mode + ' rename” to “Nový název obsahu '), true);
    await page.screenshot({path: `${shots}/${phase}-${mode}-3-history.png`, fullPage: true});
  }
  {
    const ws = seed.workspaces[REQ];
    await client.page.goto(`${base}/workspace/${ws.id}/content/${ws.post}/`);
    check('client: no rename control', await client.page.locator('#content-rename').count(), 0);
    const denied = await post(client, `${base}/workspace/${ws.id}/content/${ws.post}/rename/`, {title: 'Klient přejmenoval'});
    check('client: rename refused', denied.status, 403);
  }

  // AK2: smazání v composeru -> nástěnka klienta se zprávou.
  for (const mode of [REQ, 'optional']) {
    const ws = seed.workspaces[mode], page = owner.page;
    await page.goto(`${base}/workspace/${ws.id}/compose/${ws.doomed}/`);
    await page.getByRole('button', {name: /^Delete$|Delete/}).first().click();
    await page.screenshot({path: `${shots}/${phase}-${mode}-4-delete-modal.png`});
    await Promise.all([page.waitForNavigation(), page.locator('div[x-show="showDeleteModal"] button.bg-red-500').click()]);
    check(`${mode}: landed on board of the client`, page.url().replace(origin, ''), `/jrc/board/?client=${ws.id}`);
    check(`${mode}: delete message`, (await page.locator('body').innerText()).includes('Content deleted.'), true);
    await page.screenshot({path: `${shots}/${phase}-${mode}-5-after-delete.png`, fullPage: true});
    await page.reload();
    check(`${mode}: message shown once`, (await page.locator('body').innerText()).includes('Content deleted.'), false);
  }

  // AK3: odmítnuté stránky v rozvržení aplikace.
  {
    const page = owner.page;
    const probes = [
      ['400 foreign client', `${base}/board/?client=${seed.foreign}`, 400, 'Choose a client you have access to.'],
      ['404 missing content', `${base}/workspace/${seed.workspaces[REQ].id}/content/00000000-0000-4000-8000-000000000000/`, 404, null],
      ['405 GET on a write', `${base}/workspace/${seed.workspaces[REQ].id}/content/${seed.workspaces[REQ].post}/rename/`, 405, null],
    ];
    for (const [name, url, status, text] of probes) {
      const resp = await page.goto(url);
      check(`${name}: status`, resp.status(), status);
      check(`${name}: app layout (sidebar link + way back)`, [await page.getByRole('link', {name: 'Content board'}).count() >= 1, await page.locator('a[href$="/board/"]').count() >= 1], [true, true]);
      if (text) check(`${name}: reason shown`, (await page.locator('body').innerText()).includes(text), true);
      check(`${name}: styled (not default text page)`, await page.evaluate(() => getComputedStyle(document.querySelector('h1')).fontSize !== '32px' || document.querySelectorAll('nav,aside').length > 0), true);
      await page.screenshot({path: `${shots}/${phase}-reject-${status}.png`, fullPage: true});
    }
    const forbidden = await client.page.goto(`${base}/workspace/${seed.workspaces[REQ].id}/content/${seed.workspaces[REQ].post}/rename/`);
    check('client GET write: layout', [forbidden.status(), await client.page.locator('[data-rejected-page]').count()], [405, 1]);
    const back = await page.goto(`${base}/board/?client=${seed.foreign}`);
    await page.getByRole('link', {name: 'Back to content board'}).click();
    check('way back works', page.url().replace(origin, ''), '/jrc/board/');
  }
  // fetch/HTMX dostane dál prostý text: stejný požadavek s Accept: */*
  {
    const r = await owner.page.evaluate(async url => { const x = await fetch(url, {headers: {Accept: '*/*'}}); return [x.status, (await x.text()).slice(0, 80)]; }, `${base}/board/?client=${seed.foreign}`);
    check('script request keeps plain text', r, [400, 'Choose a client you have access to.']);
  }
  const noise = e => e.filter(m => !/status of (400|403|404|405)\b/.test(m));
  check('owner: no console errors or 5xx besides deliberate rejections', noise(owner.errors), []);
  check('client: no console errors or 5xx besides deliberate rejections', noise(client.errors), []);
  await fs.writeFile(`${out}/browser-${phase}.json`, JSON.stringify(results, null, 2));
  clearTimeout(deadline);
  await browser.close();
  console.log(JSON.stringify(results.map(r => [r.ok === false ? 'FAIL' : 'ok', r.name])));
})().catch(async e => { console.error(e); process.exit(1); });
