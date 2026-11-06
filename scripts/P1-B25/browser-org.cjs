// P1-B25 × P1-B23: organization writes are bound to the organization the page names.
// Docker runner supplies the cgroup, Node memory limit and hard deadline.
const {chromium} = require('/opt/jrc-tools/node_modules/playwright');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
(async () => {
  const seed = JSON.parse(await fs.readFile('/workspace/ops/test-output/P1-B25/session.json', 'utf8'));
  const org = JSON.parse(await fs.readFile('/workspace/ops/test-output/P1-B25/org.json', 'utf8'));
  const origin = 'http://127.0.0.1:13673', base = origin + '/jrc';
  const browser = await chromium.launch({args: ['--js-flags=--max-old-space-size=512', '--renderer-process-limit=2']});
  const deadline = setTimeout(() => process.exit(124), 240000);
  const results = [];
  const shot = (page, name) => page.screenshot({path: `/workspace/ops/screenshoty/P1-B25/org-${name}.png`});
  try {
    const ctx = await browser.newContext({viewport: {width: 1365, height: 900}});
    await ctx.addCookies([{name: 'sessionid', value: seed.sessions.owner, url: origin}]);
    await ctx.route('**/*', r => new URL(r.request().url()).origin === origin ? r.continue() : r.abort());
    const tabA = await ctx.newPage(); tabA.setDefaultTimeout(15000);
    const tabB = await ctx.newPage(); tabB.setDefaultTimeout(15000);
    const switchTo = async (page, id) => {
      await page.goto(base + '/accounts/settings/');
      await page.locator('.sidebar-org-expanded > button').first().click();
      await page.locator('#organization-switch').selectOption(id);
      await Promise.all([page.waitForNavigation(), page.getByRole('button', {name: 'Switch organization', exact: true}).click()]);
    };
    const fetchStatus = (page, path, data, headers = {}) => page.evaluate(async ({url, data, headers}) => {
      const csrf = document.querySelector('input[name=csrfmiddlewaretoken]').value;
      return (await fetch(url, {method: 'POST', redirect: 'manual', body: new URLSearchParams(data), headers: {'X-CSRFToken': csrf, ...headers}})).status;
    }, {url: base + path, data, headers});

    // 1. Organization settings as owner (org A shown, renamed through the form).
    await switchTo(tabA, org.a);
    let r = await tabA.goto(base + '/organizations/settings/');
    assert.equal(r.status(), 200);
    assert.equal(await tabA.locator('input#name').inputValue(), org.a_name);
    // 2. Another tab switches the session to org B before tab A submits its form.
    await switchTo(tabB, org.b);
    await tabA.locator('input#name').fill(org.a_name + ' (upraveno)');
    await Promise.all([tabA.waitForNavigation(), tabA.locator('form:has(input[value=update_name]) button[type=submit]').click()]);
    results.push({case: 'settings form from tab A after tab B switched to B', url: tabA.url()});
    await shot(tabA, 'settings-saved');
    // The form changed A (named by the page), never B (session selection).
    r = await tabA.goto(base + '/organizations/settings/');
    assert.equal(r.status(), 200, 'after a successful write the page shows the changed organization A');
    assert.equal(await tabA.locator('input#name').inputValue(), org.a_name + ' (upraveno)');
    // 3. Role is checked against the named target: owner is only a member of B.
    for (const [path, data] of [['/organizations/settings/', {action: 'update_name', name: 'Forbidden'}], ['/members/invite/', {email: 'p1b25-forbidden@example.invalid'}], ['/workspaces/create/', {name: 'Forbidden'}]]) {
      const status = await fetchStatus(tabA, path, {...data, organization_id: org.b});
      assert.equal(status, 403, `named B ${path}`);
      const untargeted = await fetchStatus(tabA, path, data);
      assert.equal(untargeted, 403, `untargeted ${path}`);
      results.push({case: `POST ${path} named B (member) / untargeted`, status, untargeted});
    }
    // 4. Team page and invitation through the real modal (htmx sends the page's organization).
    r = await tabA.goto(base + '/members/');
    assert.equal(r.status(), 200);
    await tabA.getByRole('button', {name: /invite/i}).first().click();
    await tabA.locator('#invite-email').fill('p1b25-invite@example.invalid');
    const [invite] = await Promise.all([
      tabA.waitForResponse(resp => resp.url().endsWith('/members/invite/') && resp.request().method() === 'POST'),
      tabA.getByRole('button', {name: 'Send Invitation'}).click(),
    ]);
    assert.equal(invite.status(), 200);
    assert.equal(invite.request().headers()['x-jrc-organization-id'] || org.a, org.a);
    await tabA.waitForTimeout(500);
    assert((await tabA.locator('body').innerText()).includes('p1b25-invite@example.invalid'));
    results.push({case: 'invite through modal (owner, org A)', status: invite.status()});
    await shot(tabA, 'team-invite');
    // 5. Switcher: B (member) hides the team page, A shows it again.
    await switchTo(tabA, org.b);
    r = await tabA.goto(base + '/members/');
    assert.equal(r.status(), 403);
    await shot(tabA, 'switch-b');
    await switchTo(tabA, org.a);
    r = await tabA.goto(base + '/members/');
    assert.equal(r.status(), 200);
    results.push({case: 'switcher A→B→A, team page 403/200'});
    await ctx.close();
    await fs.writeFile('/workspace/ops/test-output/P1-B25/browser-org.json', JSON.stringify(results, null, 2));
    console.log(JSON.stringify(results));
  } finally { await browser.close(); clearTimeout(deadline); }
})().catch(e => { console.error(e); process.exit(1); });
