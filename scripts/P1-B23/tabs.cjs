const { chromium } = require('/opt/jrc-tools/node_modules/playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');

(async () => {
  const mode = process.argv[2] || 'after';
  const seeds = JSON.parse(fs.readFileSync('/workspace/brightbean/.p1b23-tabs.env.local'));
  const base = 'http://127.0.0.1:13653/jrc';
  const browser = await chromium.launch({ headless: true, args: ['--js-flags=--max-old-space-size=512', '--renderer-process-limit=2'] });
  try {
    for (const width of mode === 'before' ? [1440] : [1440, 390]) {
      for (const action of mode === 'before' ? ['update_name'] : ['update_name', 'update_timezone', 'delete_organization', 'cancel_deletion', 'delete_organization_now']) {
        const seed = seeds[`${mode}-${width}-${action}`];
        const context = await browser.newContext({ viewport: { width, height: 900 } });
        try {
          await context.addCookies([{ name: 'sessionid', value: seed.session, url: base }]);
          await context.route('**/*', route => new URL(route.request().url()).origin === new URL(base).origin ? route.continue() : route.abort());
          context.setDefaultTimeout(15000);
          context.setDefaultNavigationTimeout(20000);
          const tabA = await context.newPage();
          const tabB = await context.newPage();
          await tabA.goto(`${base}/workspaces/${seed.a.workspace}/settings/`);
          assert.equal((await tabA.goto(`${base}/organizations/settings/`)).status(), 200);
          const form = tabA.locator('form').filter({ has: tabA.locator(`input[name="action"][value="${action}"]`) }).last();
          if (mode === 'after') assert.equal(await form.locator('[name="organization_id"]').inputValue(), seed.a.org);
          assert.equal((await tabB.goto(`${base}/workspaces/${seed.b.workspace}/settings/`)).status(), 200);
          const renamed = `${seed.a.name} saved`;
          if (action === 'update_name') await form.locator('[name="name"]').fill(renamed);
          if (action === 'update_timezone') await form.locator('[name="timezone"]').selectOption('Europe/Prague');
          await tabA.screenshot({ path: `/workspace/ops/screenshoty/P1-B23/tabs-${mode}-${width}-${action}.png` });
          // Submit the original DOM form, including teleported confirmation forms.
          await Promise.all([tabA.waitForNavigation(), form.evaluate(el => el.requestSubmit())]);
          assert.equal((await tabB.goto(`${base}/workspaces/${seed.b.workspace}/settings/`)).status(), 200);
          await tabB.goto(`${base}/organizations/settings/`);
          assert.equal(await tabB.locator('input#name').inputValue(), mode === 'before' ? renamed : seed.b.name);
          if (mode === 'after') {
            assert.equal(await tabB.locator('select#timezone').inputValue(), 'UTC');
            const bPending = await tabB.locator('input[value="cancel_deletion"]').count();
            assert.equal(bPending, ['cancel_deletion', 'delete_organization_now'].includes(action) ? 1 : 0);
            const aResponse = await tabA.goto(`${base}/workspaces/${seed.a.workspace}/settings/`);
            if (action === 'delete_organization_now') {
              assert.equal(aResponse.status(), 403);
            } else {
              assert.equal(aResponse.status(), 200);
              await tabA.goto(`${base}/organizations/settings/`);
              assert.equal(await tabA.locator('input#name').inputValue(), action === 'update_name' ? renamed : seed.a.name);
              assert.equal(await tabA.locator('select#timezone').inputValue(), action === 'update_timezone' ? 'Europe/Prague' : 'UTC');
              assert.equal(await tabA.locator('input[value="cancel_deletion"]').count(), action === 'delete_organization' ? 1 : 0);
            }
          }
          console.log(mode, width, action, 'PASS: original form A; shared session switched to B; verified both targets');
        } finally { await context.close(); }
      }
    }
    if (mode === 'after') {
      // htmx write (team invite): the page sends the organization it shows as a header.
      for (const width of [1440, 390]) {
        const seed = seeds[`after-${width}-update_timezone`];
        const email = `tabs-invite-${width}-${Date.now()}@example.invalid`;
        const context = await browser.newContext({ viewport: { width, height: 900 } });
        try {
          await context.addCookies([{ name: 'sessionid', value: seed.session, url: base }]);
          await context.route('**/*', route => new URL(route.request().url()).origin === new URL(base).origin ? route.continue() : route.abort());
          context.setDefaultTimeout(15000);
          const tabA = await context.newPage();
          const tabB = await context.newPage();
          await tabA.goto(`${base}/workspaces/${seed.a.workspace}/settings/`);
          assert.equal((await tabA.goto(`${base}/members/`)).status(), 200);
          assert.equal(await tabA.locator('body').getAttribute('data-jrc-organization-id'), seed.a.org);
          await tabB.goto(`${base}/workspaces/${seed.b.workspace}/settings/`);
          const status = await tabA.evaluate(async email => new Promise(resolve => {
            document.body.addEventListener('htmx:afterRequest', e => resolve(e.detail.xhr.status), { once: true });
            htmx.ajax('POST', document.querySelector('form[hx-post$="/members/invite/"]').getAttribute('hx-post'), { values: { email }, target: 'body', swap: 'none' });
          }), email);
          assert.equal(status, 200);
          await tabB.goto(`${base}/workspaces/${seed.b.workspace}/settings/`);
          assert.equal(await (await tabB.goto(`${base}/members/`)).text().then(t => t.includes(email)), false);
          await tabA.goto(`${base}/workspaces/${seed.a.workspace}/settings/`);
          assert.equal(await (await tabA.goto(`${base}/members/`)).text().then(t => t.includes(email)), true);
          await tabA.screenshot({ path: `/workspace/ops/screenshoty/P1-B23/tabs-after-${width}-invite.png` });
          console.log(mode, width, 'invite', 'PASS: htmx invite from page A landed in A while session was on B');
        } finally { await context.close(); }
      }
    }
    if (mode === 'after') {
      // Refused or invalid writes must not move the shared session; a successful one does.
      for (const width of [1440, 390]) {
        const seed = seeds[`after-${width}-refused`];
        const context = await browser.newContext({ viewport: { width, height: 900 } });
        try {
          await context.addCookies([{ name: 'sessionid', value: seed.session, url: base }]);
          await context.route('**/*', route => new URL(route.request().url()).origin === new URL(base).origin ? route.continue() : route.abort());
          context.setDefaultTimeout(15000);
          const tabA = await context.newPage();
          const tabB = await context.newPage();
          const orgNameInB = async () => {
            await tabB.goto(`${base}/organizations/settings/`);
            return tabB.locator('input#name').inputValue();
          };
          assert.equal((await tabA.goto(`${base}/workspace/${seed.a.workspace}/calendar/`)).status(), 200);
          assert.equal((await tabA.goto(`${base}/organizations/settings/`)).status(), 200);
          assert.equal(await tabB.goto(`${base}/workspace/${seed.b.workspace}/calendar/`).then(r => r.status()), 200);
          // 1) Invalid name in A's form: A's page again with the error, session stays on B.
          const nameForm = tabA.locator('form').filter({ has: tabA.locator('input[name="action"][value="update_name"]') });
          await nameForm.locator('[name="name"]').fill('   ');
          const [invalid] = await Promise.all([tabA.waitForNavigation(), nameForm.evaluate(el => el.requestSubmit())]);
          assert.equal(invalid.status(), 400);
          assert.equal(await tabA.locator('input#name').inputValue(), seed.a.name);
          assert.ok((await tabA.locator('body').innerText()).includes('Organization name cannot be empty.'));
          await tabA.screenshot({ path: `/workspace/ops/screenshoty/P1-B23/session-${width}-invalid-name.png` });
          assert.equal(await orgNameInB(), seed.b.name);
          // 2) Refused workspace write in A (viewer): 403, session stays on B.
          const refused = await tabA.evaluate(async url => {
            const body = new URLSearchParams({ csrfmiddlewaretoken: document.querySelector('[name=csrfmiddlewaretoken]').value, name: 'Refused' });
            return (await fetch(url, { method: 'POST', body, redirect: 'manual' })).status;
          }, `${base}/workspaces/${seed.a.workspace}/settings/`);
          assert.equal(refused, 403);
          assert.equal((await tabB.goto(`${base}/workspaces/${seed.a.workspace}/settings/`)).status(), 403);
          assert.equal(await orgNameInB(), seed.b.name);
          // 3) A successful rename of A makes A active.
          await tabA.goto(`${base}/organizations/settings/`);
          const okForm = tabA.locator('form').filter({ has: tabA.locator('input[name="action"][value="update_name"]') });
          await okForm.locator('[name="name"]').fill(`${seed.a.name} ok`);
          await Promise.all([tabA.waitForNavigation(), okForm.evaluate(el => el.requestSubmit())]);
          assert.equal(await orgNameInB(), `${seed.a.name} ok`);
          console.log(mode, width, 'session', 'PASS: invalid and refused writes kept B active; successful write activated A');
        } finally { await context.close(); }
      }
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
