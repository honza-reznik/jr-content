// Pouze Chromium v Dockeru jrc-f4b02, místní data a zablokované cizí adresy.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');

(async () => {
  const base = 'http://jrc-bb-web:8000';
  const root = '/workspace/ops';
  const seed = JSON.parse(await fs.readFile(`${root}/test-output/F4-B02/session.json`, 'utf8'));
  const browser = await chromium.launch({headless: true});
  const errors = [], external = [], checks = [];
  let lastPage;
  const contextFor = async (role, width) => {
    const context = await browser.newContext({timezoneId: 'Europe/Prague', viewport: {width, height: width === 390 ? 844 : 1050}});
    await context.addCookies([{name: 'sessionid', value: seed.sessions[role], url: base}]);
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin !== base) {
        external.push(route.request().url());
        return route.abort();
      }
      return route.continue();
    });
    context.on('page', page => page.on('pageerror', error => errors.push(String(error))));
    return context;
  };
  const waitText = (page, selector, text) => page.waitForFunction(
    ({selector, text}) => document.querySelector(selector)?.textContent.includes(text), {selector, text});
  const capture = async (page, name, width) => {
    await page.evaluate(() => document.fonts.ready);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Přetečení ${name} ${width}`);
    await page.screenshot({path: `${root}/screenshoty/F4-B02/${name}-${width}.png`, fullPage: true});
  };
  const request = async (page, action, data = {}) => page.evaluate(async ({action, data}) => {
    const panel = document.querySelector('#script-panel');
    const response = await fetch(panel.dataset.url, {
      method: action ? 'POST' : 'GET',
      headers: action ? {'Content-Type': 'application/json', 'X-CSRFToken': panel.querySelector('input').value} : {},
      body: action ? JSON.stringify({action, ...data}) : undefined,
    });
    return {status: response.status, data: response.headers.get('content-type')?.includes('application/json') ? await response.json() : null};
  }, {action, data});
  const fill = async (page, text) => {
    const editor = page.getByRole('textbox', {name: 'Script text'});
    await editor.fill(text);
  };
  try {
    for (const width of [1440, 390]) {
      const context = await contextFor('admin', width);
      const page = await context.newPage();
      lastPage = page;
      const url = `${base}/workspace/${seed.workspace}/content/${seed.posts[width]}/?tab=script`;
      await page.goto(url, {waitUntil: 'networkidle'});
      await waitText(page, '#script-version', 'Version 2');
      assert.equal(await page.locator('#script-editor h2').textContent(), 'A morning in the studio');
      assert.equal(await page.locator('#script-editor input:checked').count(), 1);
      await capture(page, '01-saved', width);
      await page.locator('#script-versions button').last().click();
      await waitText(page, '#script-version', 'Older version');
      assert.equal(await page.locator('#script-editor h1').textContent(), 'A slower start');
      assert.equal(await page.locator('#script-edit').isVisible(), false);
      await capture(page, '02-older-version', width);
      await page.locator('#script-versions button').first().click();
      await page.getByRole('button', {name: 'Edit text', exact: true}).click();
      await waitText(page, '#script-version', 'Draft');
      // Samotné zpřístupnění editoru nesmí vytvořit falešnou úpravu ani zápis.
      await page.waitForTimeout(1200);
      assert.equal((await request(page)).data.draft.revision, 0);
      await fill(page, `An updated opening ${width}`);
      await waitText(page, '#script-message', 'Draft saved');
      await page.reload({waitUntil: 'networkidle'});
      await page.getByRole('button', {name: 'Resume editing', exact: true}).waitFor();
      assert(!(await page.locator('#script-editor').textContent()).includes('An updated opening'));
      await page.getByRole('button', {name: 'Resume editing', exact: true}).click();
      assert((await page.locator('#script-editor').textContent()).includes(`An updated opening ${width}`));
      // Formátování skutečným panelem nástrojů.
      await page.getByRole('button', {name: 'Heading', exact: true}).click();
      await page.getByRole('textbox', {name: 'Script text'}).press('End');
      await page.getByRole('textbox', {name: 'Script text'}).press('Enter');
      await page.getByRole('button', {name: 'Checklist', exact: true}).click();
      await page.getByRole('textbox', {name: 'Script text'}).pressSequentially('Film the opening shot');
      await waitText(page, '#script-message', 'Draft saved');
      assert((await page.locator('#script-editor h2').count()) >= 1);
      assert.equal(await page.locator('#script-editor ul[data-type=taskList]').count(), 1);
      assert.equal(await page.locator('#script-editor ul[data-type=taskList] > li').first().evaluate(el => getComputedStyle(el).display), 'flex');
      await capture(page, '03-editing', width);
      // Druhý člen přečte uložený text, nikoli rozpracovaný koncept.
      const other = await contextFor('editor', width);
      const otherPage = await other.newPage();
      await otherPage.goto(url, {waitUntil: 'networkidle'});
      await waitText(otherPage, '#script-lock', 'Jamie is editing since');
      assert.equal(await otherPage.locator('#script-edit').isVisible(), false);
      assert(!(await otherPage.locator('#script-editor').textContent()).includes('An updated opening'));
      await capture(otherPage, '04-other-editor', width);
      await other.close();
      // Chyba připojení zachová text; Retry zapíše stejný koncept.
      await context.route('**/script/', route => route.request().method() === 'POST' ? route.abort('failed') : route.fallback());
      await fill(page, `Recovered after connection loss ${width}`);
      await waitText(page, '#script-message', 'Connection lost');
      await capture(page, '05-connection-error', width);
      await context.unroute('**/script/');
      await page.getByRole('button', {name: 'Retry draft save', exact: true}).click();
      await waitText(page, '#script-message', 'Draft saved');
      // Opožděné průběžné uložení musí doběhnout před Save, které použije novou revizi.
      let releaseAutosave;
      const gate = new Promise(resolve => {releaseAutosave = resolve;});
      const started = page.waitForRequest(req => req.method() === 'POST' && req.url().endsWith('/script/') && req.postDataJSON()?.action === 'autosave');
      await context.route('**/script/', async route => {
        if (route.request().postDataJSON()?.action === 'autosave') {
          await gate;
        }
        await route.fallback();
      });
      await fill(page, `Saved after pending autosave ${width}`);
      await started;
      await page.getByRole('button', {name: 'Save', exact: true}).click();
      releaseAutosave();
      await waitText(page, '#script-message', 'Script saved.');
      await context.unroute('**/script/');
      assert.equal((await request(page)).data.versions.length, 3);
      await capture(page, '06-new-version', width);
      // Dvě karty stejného uživatele: stará revize nesmí přepsat novější koncept.
      await page.getByRole('button', {name: 'Edit text', exact: true}).click();
      await waitText(page, '#script-version', 'Draft');
      const second = await context.newPage();
      await second.goto(url, {waitUntil: 'networkidle'});
      await second.getByRole('button', {name: 'Resume editing', exact: true}).click();
      await fill(page, `Newer tab ${width}`);
      await waitText(page, '#script-message', 'Draft saved');
      await fill(second, `Stale tab ${width}`);
      await waitText(second, '#script-message', 'another tab');
      assert.equal(await second.getByRole('button', {name: 'Save', exact: true}).isDisabled(), true);
      assert((await second.locator('#script-editor').textContent()).includes('Stale tab'));
      assert(JSON.stringify((await request(page)).data.draft.content).includes('Newer tab'));
      await capture(second, '07-conflict', width);
      await second.close();
      page.once('dialog', dialog => dialog.accept());
      await page.getByRole('button', {name: 'Discard', exact: true}).click();
      await waitText(page, '#script-message', 'Draft discarded');
      assert.equal((await request(page)).data.versions.length, 3);
      assert((await page.locator('#script-editor').textContent()).includes('Saved after pending autosave'));
      // Beze změny se další verze nevytvoří.
      await page.getByRole('button', {name: 'Edit text', exact: true}).click();
      await waitText(page, '#script-version', 'Draft');
      await page.getByRole('button', {name: 'Save', exact: true}).click();
      await waitText(page, '#script-message', 'Script saved.');
      assert.equal((await request(page)).data.versions.length, 3);
      const clientContext = await contextFor('client', width);
      const clientPage = await clientContext.newPage();
      await clientPage.goto(url, {waitUntil: 'networkidle'});
      await waitText(clientPage, '#script-message', 'Read only.');
      assert.equal(await clientPage.locator('#script-edit').isVisible(), false);
      assert.equal(await clientPage.locator('.tiptap').getAttribute('contenteditable'), 'false');
      await clientPage.locator('#script-versions button').last().click();
      await waitText(clientPage, '#script-version', 'Older version');
      assert.equal((await request(clientPage, 'edit', {base: (await request(clientPage)).data.latest})).status, 403);
      await capture(clientPage, '08-client-reading', width);
      await clientContext.close();
      if (width === 390) {
        await page.goto(`${base}/workspace/${seed.workspace}/content/${seed.empty}/?tab=script`, {waitUntil: 'networkidle'});
        await waitText(page, '#script-version', 'No script yet');
        await capture(page, '09-empty', width);
        await page.getByRole('button', {name: 'Edit text', exact: true}).click();
        await waitText(page, '#script-version', 'Draft');
        await fill(page, 'First script');
        await page.getByRole('button', {name: 'Save', exact: true}).click();
        await waitText(page, '#script-message', 'Script saved.');
        // Jiná karta uloží koncept mezi zápisem a jeho potvrzením prohlížeči.
        await page.getByRole('button', {name: 'Edit text', exact: true}).click();
        await waitText(page, '#script-version', 'Draft');
        await context.route('**/script/', async route => {
          if (route.request().method() !== 'POST' || route.request().postDataJSON()?.action !== 'autosave') return route.fallback();
          const response = await route.fetch();
          const next = await response.json();
          const saved = await context.request.post(route.request().url(), {
            headers: {'X-CSRFToken': route.request().headers()['x-csrftoken']},
            data: {action: 'save', version: next.draft.id, revision: next.draft.revision, content: next.draft.content},
          });
          assert.equal(saved.status(), 200);
          await route.fulfill({json: await saved.json()});
        });
        await fill(page, 'Finalized in another tab');
        await waitText(page, '#script-message', 'saved or replaced in another tab');
        assert((await page.locator('#script-editor').textContent()).includes('Finalized in another tab'));
        assert.equal(await page.getByRole('button', {name: 'Save', exact: true}).isDisabled(), true);
        await capture(page, '10-finalized-elsewhere', width);
        await context.unroute('**/script/');
        page.once('dialog', dialog => dialog.accept());
        await page.getByRole('button', {name: 'Reload script', exact: true}).click();
        await waitText(page, '#script-message', 'Script reloaded.');
        assert((await page.locator('#script-editor').textContent()).includes('Finalized in another tab'));
      }
      checks.push({width, history: true, formatting: true, autosave: true, resume: true, save: true,
        discard: true, noop: true, otherEditor: true, retry: true, pendingSave: true, conflict: true, client: true,
        ...(width === 390 ? {empty: true, finalizedElsewhere: true} : {})});
      await context.close();
    }
    assert.deepEqual(errors, [], 'Chyby JavaScriptu');
    assert.deepEqual(external, [], 'Cizí adresy');
    await fs.writeFile(`${root}/test-output/F4-B02/browser-report.json`, JSON.stringify({checks, errors, external}, null, 2));
    console.log('OK: editor 1440/390, verze, formátování, koncept, obnova, souběh, chyby a klient; žádné cizí požadavky ani chyby JS.');
  } catch (error) {
    if (lastPage && !lastPage.isClosed()) {
      console.error('Stav editoru:', await lastPage.locator('#script-message').textContent());
      await lastPage.screenshot({path: `${root}/test-output/F4-B02/failure.png`, fullPage: true});
    }
    throw error;
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
