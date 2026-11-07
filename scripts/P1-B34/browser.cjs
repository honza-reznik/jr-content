// P1-B34: živé ověření – CSV import celým tokem (AK1), úprava schváleného obsahu podle režimu (AK2),
// přetažení v kalendáři podle práva plánovat (AK3). Docker runner dává cgroup, limit paměti Node a tvrdý strop.
const {chromium} = require('/opt/jrc-tools/node_modules/playwright');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
const phase = process.argv[2];
const out = '/workspace/ops/test-output/P1-B34', shots = '/workspace/ops/screenshoty/P1-B34';
(async () => {
  await fs.mkdir(shots, {recursive: true});
  const seed = JSON.parse(await fs.readFile(`${out}/session.json`, 'utf8'));
  const origin = 'http://127.0.0.1:13763', base = origin + '/jrc';
  const browser = await chromium.launch({args: ['--js-flags=--max-old-space-size=512']});
  const deadline = setTimeout(() => process.exit(124), 480000);
  const results = [];
  const check = (name, actual, expected) => {
    results.push({name, actual, expected, ok: JSON.stringify(actual) === JSON.stringify(expected)});
    if (phase === 'after') assert.deepEqual(actual, expected, name);
  };
  const note = (name, actual) => results.push({name, actual});
  async function session(role) {
    const ctx = await browser.newContext({viewport: {width: 1365, height: 900}});
    await ctx.addCookies([{name: 'sessionid', value: seed.sessions[role], url: origin}]);
    await ctx.route('**/*', r => new URL(r.request().url()).origin === origin ? r.continue() : r.abort());
    const page = await ctx.newPage();
    page.setDefaultTimeout(15000); page.setDefaultNavigationTimeout(30000);
    const errors = [];
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    page.on('response', r => { if (r.status() >= 500) errors.push(`${r.status()} ${r.url()}`); });
    return {ctx, page, errors};
  }
  const csrf = async ctx => (await ctx.cookies()).find(c => c.name === 'csrftoken').value;
  async function post(s, url, data) {
    const token = await csrf(s.ctx);
    return s.page.evaluate(async ({url, data, token}) => {
      const r = await fetch(url, {method: 'POST', redirect: 'manual', body: new URLSearchParams(data), headers: {'X-CSRFToken': token}});
      return r.type === 'opaqueredirect' ? 302 : r.status;
    }, {url, data, token});
  }
  const ws = seed.workspaces, day = seed.day;
  // CSV na jiný týden, aby nezměnil počty čipů v kalendáři.
  const csvDay = new Date(Date.parse(day) + 21 * 86400000).toISOString().slice(0, 10);

  // AK1: celý tok CSV importu skutečnými klepnutími (nahrání → mapování → náhled → potvrzení).
  async function csvFlow(s, mode, label, lines) {
    const page = s.page;
    await page.goto(`${base}/workspace/${ws[mode].id}/import/csv/`);
    const csv = ['date,time,platform,caption,tags', ...lines].join('\n') + '\n';
    await page.locator('#csv-file').setInputFiles({name: `b34-${label}.csv`, mimeType: 'text/csv', buffer: Buffer.from(csv)});
    await page.getByRole('button', {name: /Upload & Map Columns/}).click();
    await page.getByText('Map Columns', {exact: true}).waitFor();
    await page.waitForFunction(() => (document.querySelector('select[name="map_caption"]') || {}).value);
    const mapping = {};
    for (const f of ['date', 'time', 'platforms', 'caption', 'tags']) mapping[f] = await page.locator(`select[name="map_${f}"]`).inputValue();
    check(`${label}: columns mapped automatically`, mapping, {date: '0', time: '1', platforms: '2', caption: '3', tags: '4'});
    await page.screenshot({path: `${shots}/${phase}-${label}-1-mapping.png`});
    const [preview] = await Promise.all([
      page.waitForResponse(r => r.url().includes('/import/csv/preview/')),
      page.getByRole('button', {name: /Validate & Preview/}).click(),
    ]);
    check(`${label}: preview status`, preview.status(), 200);
    if (preview.status() !== 200) { await page.screenshot({path: `${shots}/${phase}-${label}-2-preview-failed.png`}); return null; }
    await page.getByText('Validation Results').waitFor();
    await page.screenshot({path: `${shots}/${phase}-${label}-2-preview.png`});
    const previewText = await page.locator('#csv-import-container').innerText();
    const [confirm] = await Promise.all([
      page.waitForResponse(r => r.url().includes('/import/csv/confirm/')),
      page.getByRole('button', {name: /Confirm Import/}).click(),
    ]);
    check(`${label}: confirm status`, confirm.status(), 200);
    await page.getByText('Import Complete').waitFor();
    await page.screenshot({path: `${shots}/${phase}-${label}-3-done.png`});
    const doneText = await page.locator('#csv-import-container').innerText();
    return {previewText, doneText};
  }
  const rows = (who) => [
    `${csvDay},10:30,custom,B34 CSV ${who} one,"x,y"`,
    `${csvDay},11:00,custom,B34 CSV ${who} two,`,
    `${csvDay},25:99,custom,B34 CSV ${who} bad time,`,
    `${csvDay},12:00,snapchat,B34 CSV ${who} bad platform,`,
  ];
  try {
    const owner = await session('owner');
    const r1 = await csvFlow(owner, 'none', 'csv-owner-none', rows('owner'));
    if (r1) {
      note('csv-owner-none preview text', r1.previewText);
      note('csv-owner-none result text', r1.doneText);
      check('csv-owner-none: preview counts', [/2 valid/.test(r1.previewText), /2 errors/.test(r1.previewText)], [true, true]);
      check('csv-owner-none: preview names both bad rows', [
        /Row 4[\s\S]*Invalid time format '25:99'/.test(r1.previewText), /Row 5[\s\S]*Unknown platform 'snapchat'/.test(r1.previewText)], [true, true]);
      check('csv-owner-none: result 2 created, 2 skipped', [/2\s*posts created/.test(r1.doneText), /2\s*rows skipped/.test(r1.doneText)], [true, true]);
    }
    const r2 = await csvFlow(owner, 'required_internal', 'csv-owner-required', rows('owner'));
    if (r2) check('csv-owner-required: approval hint', /needs approval by the team/.test(r2.doneText), true);
    if (phase === 'after') assert.deepEqual(owner.errors, [], 'owner console/network errors');

    const editor = await session('editor');
    const r3 = await csvFlow(editor, 'none', 'csv-editor-none', rows('editor'));
    if (r3) check('csv-editor-none: cannot-schedule hint', /scheduling needs publishing rights/.test(r3.doneText), true);

    // AK2: úprava schváleného obsahu v composeru (skutečné Save Draft) ve všech režimech.
    for (const mode of Object.keys(ws)) {
      const item = ws[mode].posts.owner_approved_edit;
      await owner.page.goto(`${base}/workspace/${ws[mode].id}/compose/${item.post}/`);
      const banner = await owner.page.getByText(/^Approved/).first().innerText();
      check(`AK2 ${mode}: approved banner`, banner,
        mode.startsWith('required') ? 'Approved - saving changes sends it back for re-approval' : 'Approved');
      const caption = owner.page.locator('textarea[name="caption"]').first();
      await caption.fill(`Edited caption in ${mode}`);
      await Promise.all([owner.page.waitForNavigation(), owner.page.locator('button[name="action"][value="save_draft"]').first().click()]);
      await owner.page.waitForTimeout(500);
      note(`AK2 ${mode}: url after save`, owner.page.url());
    }
    await owner.page.screenshot({path: `${shots}/${phase}-ak2-after-save.png`});

    // AK3: nabídka přetažení v kalendáři podle can_schedule; přímé POSTy změny data.
    const weekUrl = mode => `${base}/workspace/${ws[mode].id}/calendar/?mode=calendar&view=week&date=${day}`;
    await editor.page.goto(weekUrl('none'));
    await editor.page.locator('.cal-drop .post-chip').first().waitFor();
    check('editor calendar none: chips shown', await editor.page.locator('.cal-drop .post-chip').count(), 6);
    check('editor calendar none: draggable chips', await editor.page.locator('.cal-drop .post-chip.is-draggable, .cal-drop .post-chip[draggable="true"]').count(), 0);
    await editor.page.screenshot({path: `${shots}/${phase}-editor-none-calendar.png`});
    for (const name of ['editor_approved', 'editor_pending', 'editor_draft']) {
      const status = await post(editor, `${base}/workspace/${ws.none.id}/calendar/reschedule/`, {
        platform_post_id: ws.none.posts[name].platform_post, new_datetime: `${day}T16:00:00`});
      note(`editor reschedule ${name}`, status);
      if (phase === 'after') check(`editor reschedule ${name} refused`, status, 403);
    }
    if (phase === 'after') assert.deepEqual(editor.errors.filter(e => !/40[03]/.test(e)), [], 'editor errors');
    await editor.ctx.close();

    for (const [mode, expected] of [['none', 6], ['required_internal', 4]]) {
      await owner.page.goto(weekUrl(mode));
      await owner.page.locator('.cal-drop .post-chip').first().waitFor();
      check(`owner calendar ${mode}: draggable chips`, await owner.page.locator('.cal-drop .post-chip.is-draggable').count(), expected);
      await owner.page.screenshot({path: `${shots}/${phase}-owner-${mode}-calendar.png`});
    }
    // Kladně: skutečné přetažení schválené kopie na jiný den (SortableJS, nativní drag).
    await owner.page.goto(weekUrl('none'));
    const chip = owner.page.locator(`.cal-drop .post-chip[data-platform-post-id="${ws.none.posts.owner_approved_cal.platform_post}"]`);
    await chip.scrollIntoViewIfNeeded();
    const target = owner.page.locator(`.cal-drop[data-time="10:00"]:not([data-date="${day}"])`).first();
    const targetDate = await target.getAttribute('data-date');
    note('owner drag target date', targetDate);
    const [resp] = await Promise.all([
      owner.page.waitForResponse(r => r.url().includes('/calendar/reschedule/'), {timeout: 15000}),
      chip.dragTo(target),
    ]);
    check('owner drag approved chip: server accepted', resp.status(), 204);
    await owner.page.waitForTimeout(800);
    await owner.page.screenshot({path: `${shots}/${phase}-owner-none-after-drag.png`});
    results.push({name: 'owner drag target', targetDate});
    if (phase === 'after') assert.deepEqual(owner.errors.filter(e => !/40[03]/.test(e)), [], 'owner errors');
    await owner.ctx.close();
    await fs.writeFile(`${out}/browser-${phase}.json`, JSON.stringify(results, null, 2));
    console.log(JSON.stringify(results.map(r => ({...r, actual: typeof r.actual === 'string' ? r.actual.slice(0, 300) : r.actual})), null, 1));
  } catch (e) {
    await fs.writeFile(`${out}/browser-${phase}.json`, JSON.stringify(results, null, 2));
    throw e;
  } finally { await browser.close(); clearTimeout(deadline); }
})().catch(e => { console.error(e); process.exit(1); });
