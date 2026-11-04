// P1-B08: Chromium jen v Dockeru jrc-p1b08 (kontejner jrc-dev), místní data, cizí adresy zablokované.
const {chromium} = require('/opt/jrc-tools/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');

(async () => {
  // Prohlížeč používá veřejný původ projektu (kvůli CSRF); požadavky se uvnitř sítě Dockeru posílají na proxy.
  const origin = 'http://127.0.0.1:13513';
  const proxy = 'http://jrc-proxy:8080';
  const base = `${origin}/jrc`;
  const root = '/workspace/ops';
  const seed = JSON.parse(await fs.readFile(`${root}/test-output/P1-B08/session.json`, 'utf8'));
  const browser = await chromium.launch({headless: true});
  const errors = [], external = [], log = [];
  const contextFor = async (role, width) => {
    const context = await browser.newContext({timezoneId: 'Europe/Prague', viewport: {width, height: width === 390 ? 844 : 1050}});
    await context.addCookies([{name: 'sessionid', value: seed.sessions[role], url: base}]);
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) { external.push(route.request().url()); return route.abort(); }
      try {
        const response = await route.fetch({url: proxy + url.pathname + url.search});
        if (process.env.DEBUG_ROUTE) console.error('ROUTE', route.request().method(), url.pathname, response.status(), response.headers().location || '');
        return route.fulfill({response});
      } catch (error) { console.error('ROUTE', route.request().method(), url.pathname, String(error)); return route.abort(); }
    });
    context.on('page', page => page.on('pageerror', error => errors.push(String(error))));
    return context;
  };
  const waitText = (page, selector, text) => page.waitForFunction(
    ({selector, text}) => document.querySelector(selector)?.textContent.includes(text), {selector, text});
  const capture = async (page, name, width) => {
    await page.evaluate(() => document.fonts.ready);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Přetečení ${name} ${width}`);
    await page.screenshot({path: `${root}/screenshoty/P1-B08/${name}-${width}.png`, fullPage: true});
    log.push(`${name}-${width}.png`);
  };
  const selectedTab = page => page.locator('[role=tab][aria-selected=true]').textContent();
  try {
    for (const width of [1440, 390]) {
      const detail = `${base}/workspace/${seed.workspace}/content/${seed.posts[width]}/`;
      const owner = await contextFor('owner', width);
      const page = await owner.newPage();
      // Nový obsah bez scénáře otevře záložku Concept; pořadí záložek.
      await page.goto(detail, {waitUntil: 'networkidle'});
      assert.equal((await selectedTab(page)).trim(), 'concept');
      const tabs = (await page.locator('[role=tab]').allTextContents()).map(t => t.trim());
      assert.deepEqual(tabs, ['concept', 'script', 'video', 'caption', 'approval', 'comments', 'publish', 'history']);
      assert.equal((await page.locator('#script-panel h2').textContent()).trim(), 'Concept');
      await page.locator('#script-edit').waitFor({state: 'visible'});
      await capture(page, '01-concept-prazdny', width);
      // Edit text → psaní → průběžné uložení → Save = verze 2.
      await page.locator('#script-edit').click();
      await page.locator('#script-save').waitFor({state: 'visible', timeout: 8000}).catch(async e => { console.error('MSG', await page.locator('#script-message').textContent()); throw e })
      const editor = page.locator('#script-editor .tiptap');
      await editor.click();
      await page.keyboard.type('Hook: three plants that survive autumn. Angle: low effort, big result.');
      await waitText(page, '#script-message', 'Draft saved');
      await capture(page, '02-concept-upravy', width);
      await page.locator('#script-save').click();
      await waitText(page, '#script-version', 'Version 2 · Latest saved');
      await capture(page, '03-concept-ulozeno', width);
      // Use as script draft → záložka Script s textem konceptu.
      if (process.env.DEBUG_ROUTE) { page.on('console', m => console.error('CONSOLE', m.text())); console.error('ACTION', await page.locator('form.script-to-script').getAttribute('action')); }
      await page.getByRole('button', {name: 'Use as script draft'}).click();
      await page.waitForLoadState('networkidle');
      if (process.env.DEBUG_ROUTE) console.error('PAGE', (await page.locator('body').innerText()).slice(0, 1500));
      assert.equal((await selectedTab(page)).trim(), 'script');
      await waitText(page, '#script-version', 'Version 1 · Latest saved');
      assert.match(await page.locator('#script-editor').textContent(), /three plants/);
      assert.match(await page.content(), /copied to the script as version 1/);
      await capture(page, '04-script-z-konceptu', width);
      // Bez parametru tab teď obsah otevře scénář.
      await page.goto(detail, {waitUntil: 'networkidle'});
      assert.equal((await selectedTab(page)).trim(), 'script');
      await owner.close();
      // Klient: čte uloženou verzi, nemůže upravovat ani převzít do scénáře.
      const client = await contextFor('client', width);
      const cpage = await client.newPage();
      await cpage.goto(`${detail}?tab=concept`, {waitUntil: 'networkidle'});
      await waitText(cpage, '#script-version', 'Version 2 · Latest saved');
      assert.match(await cpage.locator('#script-editor').textContent(), /three plants/);
      assert.equal(await cpage.locator('#script-edit').isVisible(), false);
      assert.equal(await cpage.getByRole('button', {name: 'Use as script draft'}).count(), 0);
      await capture(cpage, '05-concept-klient', width);
      await client.close();
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    console.log('OK', log.join(' '));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
