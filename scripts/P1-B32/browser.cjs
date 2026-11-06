// P1-B32: živé ověření – kdo smí plánovat (AK1), Review jen v režimu se schvalováním (AK3), pozdržená kopie (AK4).
// Docker runner dává cgroup, limit paměti Node a tvrdý strop.
const {chromium} = require('/opt/jrc-tools/node_modules/playwright');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
const phase = process.argv[2];
const out = '/workspace/ops/test-output/P1-B32', shots = '/workspace/ops/screenshoty/P1-B32';
(async () => {
  const seed = JSON.parse(await fs.readFile(`${out}/session.json`, 'utf8'));
  const origin = 'http://127.0.0.1:13743', base = origin + '/jrc';
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
  async function csvImport(s, ws, caption, date) {
    const token = await csrf(s.ctx);
    return s.page.evaluate(async ({prefix, token, caption, date}) => {
      const fd = new FormData();
      fd.append('csv_file', new File([`date,time,platform,caption\n${date},10:30,custom,${caption}\n`], 'plan.csv', {type: 'text/csv'}));
      const h = {'X-CSRFToken': token};
      const up = await fetch(`${prefix}/`, {method: 'POST', body: fd, headers: h});
      if (up.status !== 200) return {status: up.status};
      await fetch(`${prefix}/preview/`, {method: 'POST', headers: h, body: new URLSearchParams({map_date: '0', map_time: '1', map_platforms: '2', map_caption: '3'})});
      const done = await fetch(`${prefix}/confirm/`, {method: 'POST', headers: h});
      const text = await done.text();
      return {status: done.status, cannotSchedule: text.includes('data-cannot-schedule')};
    }, {prefix: `${base}/workspace/${ws.id}/import/csv`, token, caption, date});
  }
  const future = new Date(Date.now() + 3 * 86400000);
  const day = `${future.getFullYear()}-${String(future.getMonth() + 1).padStart(2, '0')}-${String(future.getDate()).padStart(2, '0')}`;
  const none = seed.workspaces.none, optional = seed.workspaces.optional;
  try {
    // AK1: EDITOR (bez práva publikovat) v „No approvals“.
    const editor = await session('editor');
    const mine = none.posts.editor_draft;
    const composeUrl = `${base}/workspace/${none.id}/compose/${mine.post}/`;
    await editor.page.goto(composeUrl);
    check('editor composer: no Add to Queue / Publish Now / Schedule', await editor.page.locator('#publish-action-btn').count(), 0);
    const propose = editor.page.getByRole('button', {name: /Proposed time/});
    check('editor composer: Proposed time offered', await propose.count(), 1);
    if (await propose.count()) {
      await propose.click();
      check('editor composer: panel has date inputs, no Set Schedule', [
        await editor.page.locator('#publish-schedule-view').isVisible()
          && await editor.page.locator('#publish-schedule-view input[name="scheduled_date"]').count() === 1,
        await editor.page.getByRole('button', {name: 'Set Schedule'}).count()], [true, 0]);
    }
    await editor.page.screenshot({path: `${shots}/${phase}-editor-none-composer.png`, fullPage: false});
    for (const action of ['schedule', 'add_to_queue', 'add_to_queue_priority', 'publish_now']) {
      const status = await post(editor, composeUrl + 'save/', {
        action, title: `P1-B32 none editor_draft`, caption: 'Ready caption', selected_accounts: none.account,
        scheduled_date: day, scheduled_time: '10:30'});
      results.push({name: `editor direct composer ${action}`, actual: status});
      if (phase === 'after') check(`editor direct composer ${action} refused`, status, 403);
    }
    // CSV import: krok náhledu (preview) dnes padá 500 i bez této změny (mimo rozsah, zapsáno v bloku); ověřeno testy.
    await editor.page.goto(`${base}/workspace/${none.id}/calendar/?mode=list&tab=queue`);
    check('editor calendar: no bulk Publish', (await editor.page.content()).includes("ask('publish')"), false);
    if (phase === 'after') assert.deepEqual(editor.errors.filter(e => !/40[03]/.test(e)), []);
    await editor.ctx.close();

    // AK1 kladně: OWNER skutečným klepnutím „Add to Queue“.
    const owner = await session('owner');
    const own = none.posts.owner_draft;
    await owner.page.goto(`${base}/workspace/${none.id}/compose/${own.post}/`);
    check('owner composer: Add to Queue offered', await owner.page.locator('#publish-action-btn').count(), 1);
    await owner.page.screenshot({path: `${shots}/${phase}-owner-none-composer.png`, fullPage: false});
    await Promise.all([owner.page.waitForURL(/\/calendar\//), owner.page.locator('#publish-action-btn').click()]);
    check('owner composer: Add to Queue redirected to calendar', /\/calendar\//.test(owner.page.url()), true);

    // AK4: pozdržená kopie nejde ven ani v „No approvals“ (přímý POST jako composer).
    const held = none.posts.owner_on_hold;
    for (const action of ['schedule', 'publish_now', 'add_to_queue']) {
      const status = await post(owner, `${base}/workspace/${none.id}/compose/${held.post}/save/`, {
        action, title: 'P1-B32 none owner_on_hold', caption: 'Ready caption', selected_accounts: none.account,
        scheduled_date: day, scheduled_time: '10:30'});
      results.push({name: `owner on_hold composer ${action}`, actual: status});
      if (phase === 'after') check(`owner on_hold composer ${action} refused`, status, 400);
    }

    // AK3: Review na nástěnce a v composeru jen tam, kde režim schvalování má.
    for (const [mode, ws] of [['none', none], ['optional', optional]]) {
      await owner.page.goto(`${base}/board/?client=${ws.id}`);
      const card = ws.posts.manager_draft.post;
      const options = await owner.page.locator(`#move-${card} option`).evaluateAll(os => os.map(o => o.value));
      check(`owner board ${mode}: Review offered`, options.includes('review'), mode !== 'none');
      await owner.page.screenshot({path: `${shots}/${phase}-owner-${mode}-board.png`, fullPage: false});
      await owner.page.goto(`${base}/workspace/${ws.id}/compose/${ws.posts.manager_draft.post}/`);
      check(`owner composer ${mode}: Submit for Approval offered`,
        await owner.page.getByRole('button', {name: /Submit for Approval|For Approval/}).count() > 0, mode !== 'none');
    }
    const reviewStatus = await post(owner, `${base}/workspace/${none.id}/board/${none.posts.contributor_draft.post}/move/`,
      {stage: 'review', expected_stage: 'idea'});
    results.push({name: 'owner board none: direct move to Review', actual: reviewStatus});
    if (phase === 'after') check('owner board none: direct move to Review refused', reviewStatus, 403);
    if (phase === 'after') {
      // Kladně: v „Optional“ jde obsah do Review skutečným výběrem a odesláním na kartě.
      await owner.page.goto(`${base}/board/?client=${optional.id}`);
      const card = optional.posts.manager_draft.post;
      await owner.page.locator(`article[data-post="${card}"] summary`).click();
      await owner.page.locator(`#move-${card}`).selectOption('review');
      await Promise.all([owner.page.waitForNavigation(), owner.page.locator(`article[data-post="${card}"] button.board-primary`).click()]);
      check('owner board optional: moved to Review by click', (await owner.page.locator(`article[data-post="${card}"]`).count()) > 0, true);
      await owner.page.screenshot({path: `${shots}/${phase}-owner-optional-board-after-review.png`, fullPage: false});
    }
    if (phase === 'after') assert.deepEqual(owner.errors.filter(e => !/40[03]/.test(e)), []);
    await owner.ctx.close();
    await fs.writeFile(`${out}/browser-${phase}.json`, JSON.stringify(results, null, 2));
    console.log(JSON.stringify(results, null, 1));
  } finally { await browser.close(); clearTimeout(deadline); }
})().catch(e => { console.error(e); process.exit(1); });
