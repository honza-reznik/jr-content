// P1-B36: živé ověření – úprava popisku po schválení v detailu JR (AK1, režim Internal review + client
// approval a pro srovnání Optional), import TSV a hláška u jiného kódování (AK2), stažení z plánu (AK3).
// Docker runner dává cgroup, limit paměti Node a tvrdý strop.
const {chromium} = require('/opt/jrc-tools/node_modules/playwright');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
const phase = process.argv[2];
const out = '/workspace/ops/test-output/P1-B36', shots = '/workspace/ops/screenshoty/P1-B36';
(async () => {
  await fs.mkdir(shots, {recursive: true});
  const seed = JSON.parse(await fs.readFile(`${out}/session.json`, 'utf8'));
  const origin = 'http://127.0.0.1:13783', base = origin + '/jrc';
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
  const owner = await session('owner'), editor = await session('editor');

  // AK1: úprava popisku schváleného obsahu v záložce Caption skutečnými klepnutími.
  for (const mode of ['required_internal_and_client', 'optional']) {
    const ws = seed.workspaces[mode], page = owner.page;
    const detail = `${base}/workspace/${ws.id}/content/${ws.post}/`;
    await page.goto(`${detail}?tab=caption`);
    const card = page.locator('article').filter({hasText: 'P1-B36 Instagram'});
    const hint = await card.locator('[data-reopens-review]').count();
    check(`${mode}: hint before saving`, hint, mode === 'optional' ? 0 : 1);
    await page.screenshot({path: `${shots}/${phase}-${mode}-1-caption-before.png`, fullPage: true});
    await card.locator('textarea[name="caption"]').fill('Schválený popisek – opravený překlep');
    await Promise.all([page.waitForNavigation(), card.getByRole('button', {name: 'Save version'}).click()]);
    const body = await page.locator('body').innerText();
    check(`${mode}: success message`, body.includes('It goes back to review for approval.'), mode !== 'optional');
    check(`${mode}: caption saved`, body.includes('Saved version 2'), true);
    await page.screenshot({path: `${shots}/${phase}-${mode}-2-caption-after.png`, fullPage: true});
    await page.goto(detail);
    const stageText = await page.locator('body').innerText();
    results.push({name: `${mode}: detail after save mentions Review`, actual: /\bReview\b/.test(stageText)});
    await page.screenshot({path: `${shots}/${phase}-${mode}-3-detail-after.png`, fullPage: true});
  }

  // AK2: TSV skutečným nahráním, pak soubor v cp1250.
  {
    const ws = seed.workspaces.required_internal_and_client, page = owner.page;
    await page.goto(`${base}/workspace/${ws.id}/import/csv/`);
    check('import page text', await page.getByText('Supports .csv (comma or semicolon) and .tsv files saved as UTF-8').count(), 1);
    const day = new Date(Date.now() + 9 * 86400000).toISOString().slice(0, 10);
    const tsv = `date\ttime\tplatform\tcaption\n${day}\t10:00\tcustom\tB36 TSV, s čárkou\n`;
    await page.locator('#csv-file').setInputFiles({name: 'b36.tsv', mimeType: 'text/tab-separated-values', buffer: Buffer.from(tsv)});
    await page.getByRole('button', {name: /Upload & Map Columns/}).click();
    await page.getByText('Map Columns', {exact: true}).waitFor();
    const mapping = {};
    for (const f of ['date', 'time', 'platforms', 'caption']) mapping[f] = await page.locator(`select[name="map_${f}"]`).inputValue();
    check('tsv: four columns mapped', mapping, {date: '0', time: '1', platforms: '2', caption: '3'});
    await page.screenshot({path: `${shots}/${phase}-tsv-1-mapping.png`, fullPage: true});
    await page.getByRole('button', {name: /Validate & Preview/}).click();
    await page.getByRole('button', {name: /Confirm Import/}).click();
    await page.getByText(/posts? created/).waitFor();
    await page.screenshot({path: `${shots}/${phase}-tsv-2-done.png`, fullPage: true});

    await page.goto(`${base}/workspace/${ws.id}/import/csv/`);
    const cp1250 = Buffer.from([0x64, 0x61, 0x74, 0x65, 0x2c, 0x63, 0x61, 0x70, 0x74, 0x69, 0x6f, 0x6e, 0x0a, 0x9e, 0x6c, 0x9d, 0x6f, 0x75, 0xe8, 0x6b, 0xfd, 0x0a]);
    await page.locator('#csv-file').setInputFiles({name: 'b36-cp1250.csv', mimeType: 'text/csv', buffer: cp1250});
    const [upload] = await Promise.all([
      page.waitForResponse(r => r.url().includes('/import/csv/') && r.request().method() === 'POST'),
      page.getByRole('button', {name: /Upload & Map Columns/}).click(),
    ]);
    check('cp1250: upload status', upload.status(), 200);
    await page.getByText('not saved as UTF-8 text').waitFor();
    check('cp1250: message shown once', await page.getByText('not saved as UTF-8 text').count(), 1);
    check('cp1250: page not nested (one sidebar, one heading)', [await page.getByRole('link', {name: 'Content board'}).count(), await page.getByRole('heading', {name: 'Import from CSV'}).count()], [1, 1]);
    await page.screenshot({path: `${shots}/${phase}-cp1250-message.png`, fullPage: true});
  }

  // AK3: hromadné Draft naplánovaného obsahu – editor ne, owner ano.
  {
    const ws = seed.workspaces.optional;
    await editor.page.goto(`${base}/workspace/${ws.id}/calendar/`);
    const refused = await post(editor, `${base}/workspace/${ws.id}/calendar/bulk-action/`, {action: 'draft', platform_post_ids: ws.planned});
    check('editor bulk draft of planned content', refused.status, 403);
    results.push({name: 'editor refusal message', actual: refused.body});
    await owner.page.goto(`${base}/workspace/${ws.id}/calendar/`);
    const done = await post(owner, `${base}/workspace/${ws.id}/calendar/bulk-action/`, {action: 'draft', platform_post_ids: ws.planned});
    check('owner bulk draft of planned content', [done.status, JSON.parse(done.body).count], [200, 1]);
  }
  check('owner: no console errors or 5xx', owner.errors, []);
  // The one 403 is the deliberately refused bulk Draft above (the browser logs every 4xx fetch).
  check('editor: no console errors or 5xx', editor.errors.filter(e => !e.endsWith('status of 403 (Forbidden)')), []);
  check('editor: exactly the refused request logged', editor.errors.length, 1);
  await fs.writeFile(`${out}/browser-${phase}.json`, JSON.stringify(results, null, 2));
  clearTimeout(deadline);
  await browser.close();
  console.log(JSON.stringify(results.map(r => [r.ok === false ? 'FAIL' : 'ok', r.name])));
})().catch(async e => { console.error(e); process.exit(1); });
