// P1-B27: živé ověření režimů schvalování. Docker runner dává cgroup, limit paměti Node a tvrdý strop.
const {chromium} = require('/opt/jrc-tools/node_modules/playwright');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
const phase = process.argv[2];
const out = '/workspace/ops/test-output/P1-B27', shots = '/workspace/ops/screenshoty/P1-B27';
(async () => {
  const seed = JSON.parse(await fs.readFile(`${out}/session.json`, 'utf8'));
  const origin = 'http://127.0.0.1:13693', base = origin + '/jrc';
  const browser = await chromium.launch({args: ['--js-flags=--max-old-space-size=512']});
  const deadline = setTimeout(() => process.exit(124), 420000);
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
  async function post(page, ctx, url, data) {
    const token = await csrf(ctx);
    return page.evaluate(async ({url, data, token}) => {
      const r = await fetch(url, {method: 'POST', redirect: 'manual', body: new URLSearchParams(data), headers: {'X-CSRFToken': token}});
      return r.type === 'opaqueredirect' ? 302 : r.status;
    }, {url, data, token});
  }
  const future = new Date(Date.now() + 3 * 86400000);
  const local = `${future.getFullYear()}-${String(future.getMonth() + 1).padStart(2, '0')}-${String(future.getDate()).padStart(2, '0')}T10:30`;
  try {
    // OWNER: Draft s popiskem ve všech čtyřech režimech (E-08-ex6b-02).
    const owner = await session('owner');
    for (const mode of Object.keys(seed.workspaces)) {
      const ws = seed.workspaces[mode], item = ws.posts.draft_caption;
      const url = `${base}/workspace/${ws.id}/content/${item.post}/`;
      await owner.page.goto(url + '?tab=publish');
      const setDate = await owner.page.getByText(/^(Set date|Change date)$/).count();
      const mark = await owner.page.getByRole('button', {name: 'Mark as published'}).count();
      const hint = (await owner.page.locator('article').first().innerText()).replace(/\s+/g, ' ');
      await owner.page.screenshot({path: `${shots}/${phase}-owner-${mode}-publish.png`, fullPage: true});
      const goesOut = mode === 'none' || mode === 'optional';
      check(`owner ${mode}: Set date offered on Draft with caption`, setDate > 0, goesOut);
      check(`owner ${mode}: Mark as published offered`, mark > 0, goesOut);
      check(`owner ${mode}: hint mentions approval`, /approv/i.test(hint), !goesOut);
      if (phase === 'after' && goesOut) {
        // Skutečné klepnutí: otevřít „Set date“, vyplnit, uložit.
        await owner.page.getByText('Set date').click();
        await owner.page.locator(`#date-${item.platform_post}-scheduled_at`).fill(local);
        await owner.page.getByRole('button', {name: 'Save date'}).click();
        await owner.page.waitForLoadState('load');
        const body = await owner.page.locator('body').innerText();
        check(`owner ${mode}: date saved in browser`, /Publication date saved/.test(body), true);
        await owner.page.screenshot({path: `${shots}/${phase}-owner-${mode}-scheduled.png`, fullPage: true});
      } else {
        const status = await post(owner.page, owner.ctx, url + '?tab=publish', {action: 'set_date', platform_post: item.platform_post, scheduled_at: local});
        await owner.page.goto(url + '?tab=publish');
        const label = (await owner.page.locator('article').first().innerText()).includes('No date or time scheduled');
        results.push({name: `owner ${mode}: direct set_date POST`, actual: status, unchanged: label});
        if (phase === 'after') check(`owner ${mode}: direct set_date leaves Draft unscheduled`, label, true);
      }
    }
    owner.errors.length && results.push({name: 'owner console/network errors', actual: owner.errors});
    if (phase === 'after') assert.deepEqual(owner.errors, []);
    await owner.ctx.close();
    // CLIENT v „Internal review required“: nesmí rozhodnout (AK1 z P1-B25, ověření).
    const client = await session('client');
    const team = seed.workspaces.required_internal, review = team.posts.review;
    const turl = `${base}/workspace/${team.id}/content/${review.post}/`;
    await client.page.goto(turl + '?tab=approval');
    check('client required_internal: no Approve button', await client.page.getByRole('button', {name: /approve/i}).count(), 0);
    check('client required_internal: no Request changes button', await client.page.getByRole('button', {name: /request changes/i}).count(), 0);
    await client.page.screenshot({path: `${shots}/${phase}-client-required_internal-approval.png`, fullPage: true});
    for (const [path, data] of [[`content/${review.post}/approval/`, {decision: 'approve', expected_stage: 'review'}],
                                 [`content/${review.post}/approval/`, {decision: 'request_changes', expected_stage: 'review', comment: 'No'}],
                                 [`approvals/${review.post}/approve/`, {}]]) {
      const status = await post(client.page, client.ctx, `${base}/workspace/${team.id}/${path}`, data);
      check(`client required_internal: POST ${path} ${data.decision || 'approve'}`, status, 403);
    }
    await client.page.goto(turl + '?tab=approval');
    check('client required_internal: still in review', /team is reviewing/i.test(await client.page.locator('body').innerText()), true);
    if (phase === 'after') assert.deepEqual(client.errors.filter(e => !/403/.test(e)), []);
    await client.ctx.close();
    await fs.writeFile(`${out}/browser-${phase}.json`, JSON.stringify(results, null, 2));
    console.log(JSON.stringify(results, null, 1));
  } finally { await browser.close(); clearTimeout(deadline); }
})().catch(e => { console.error(e); process.exit(1); });
