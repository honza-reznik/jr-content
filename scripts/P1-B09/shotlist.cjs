// Jen existující Chromium v jrc-dev; místní průchod P1-B09 (shotlist) bez externích požadavků.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
assert.equal(process.env.JRC_PROJECT, 'jrc-p1b09');
const origin = 'http://127.0.0.1:13523';
const base = origin + '/jrc';
const out = 'ops/screenshoty/P1-B09';

(async () => {
  const seedLog = await fs.readFile('ops/test-output/P1-B09/seed.log', 'utf8');
  const seed = JSON.parse(seedLog.split('\n').find((line) => line.startsWith('{"workspace"')));
  await fs.mkdir(out, { recursive: true });
  // Lokální vstup uvnitř vlastního dev kontejneru zachovává veřejný Host i cookies.
  const server = http.createServer((req, res) => {
    const upstream = http.request(
      { hostname: 'jrc-proxy', port: 8080, path: req.url, method: req.method, headers: req.headers },
      (response) => {
        res.writeHead(response.statusCode, response.headers);
        response.pipe(res);
      },
    );
    res.on('close', () => upstream.destroy());
    upstream.on('error', (error) => {
      res.writeHead(502);
      res.end(error.message);
    });
    req.pipe(upstream);
  });
  await new Promise((resolve) => server.listen(13523, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true });
  const errors = [];
  const external = [];
  const checks = [];

  async function open(width, role) {
    const context = await browser.newContext({ viewport: { width, height: 1000 } });
    await context.addCookies([{ name: 'sessionid', value: seed.sessions[role], url: base + '/' }]);
    await context.route('**/*', (route) => {
      if (new URL(route.request().url()).origin !== origin) {
        external.push(route.request().url());
        return route.abort();
      }
      return route.continue();
    });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    page.on('pageerror', (error) => errors.push(`${role} ${width}: ${error.message}`));
    page.on('console', (message) => {
      // The dropped-connection step aborts one request on purpose.
      if (message.type() === 'error' && !message.text().includes('ERR_CONNECTION_RESET')) {
        errors.push(`${role} ${width} console: ${message.text()}`);
      }
    });
    return { context, page };
  }

  const noOverflow = (page) => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
  const progress = (page) => page.locator('[data-shotlist] [data-shotlist-progress]').innerText();
  const texts = (page) => page.locator('[data-shotlist] .shotlist-text').evaluateAll((nodes) =>
    nodes.map((node) => node.childNodes[0].textContent.trim()),
  );

  try {
    for (const width of [1440, 390]) {
      const post = seed.posts[String(width)];
      const detail = `${base}/workspace/${seed.workspace}/content/${post}/`;
      const { context, page } = await open(width, 'owner');
      console.log(`Owner ${width}: nástěnka`);
      await page.goto(`${base}/board/?client=${seed.workspace}`, { waitUntil: 'networkidle' });
      await page.screenshot({ path: `${out}/board-${width}.png`, fullPage: width === 1440 });
      const card = page.locator(`[data-post="${post}"]`);
      assert.equal((await card.locator('[data-shotlist-progress]').innerText()).trim(), 'Shotlist 1/3 done');
      assert(await noOverflow(page), `board overflow ${width}`);
      checks.push(`owner ${width}: karta nástěnky ukazuje Shotlist 1/3 done`);

      console.log(`Owner ${width}: záložka Shotlist`);
      await page.goto(`${detail}?tab=shotlist`, { waitUntil: 'networkidle' });
      const tabs = await page.locator('[role=tab]').allInnerTexts();
      assert.equal(tabs.map((t) => t.trim().toLowerCase()).indexOf('shotlist'), 1, 'Shotlist za Script');
      assert.equal((await progress(page)).trim(), '1/3 done');
      assert.deepEqual(await texts(page), [
        'Thumbnail photo',
        'Wide shot of the studio',
        'Close-up of hands shaping the clay',
      ]);
      const box = await page.locator('.shotlist-box').first().boundingBox();
      assert(box.width >= 44 && box.height >= 44, 'zaškrtávací pole aspoň 44 px');
      assert(await noOverflow(page), `detail overflow ${width}`);
      await page.screenshot({ path: `${out}/shotlist-owner-${width}.png`, fullPage: true });

      // Enter = další řádek: po přidání zůstane kurzor v prázdném poli.
      const input = page.locator('#shotlist-new-text');
      await input.click();
      await input.fill('Pouring the glaze');
      await input.press('Enter');
      await page.waitForFunction(() => document.querySelectorAll('[data-shotlist] .shotlist-row').length === 4);
      assert.equal(await page.evaluate(() => document.activeElement && document.activeElement.id), 'shotlist-new-text');
      assert.equal(await page.locator('#shotlist-new-text').inputValue(), '');
      await page.keyboard.type('Kiln opening reaction');
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => document.querySelectorAll('[data-shotlist] .shotlist-row').length === 5);
      assert.equal(await page.evaluate(() => document.activeElement.id), 'shotlist-new-text');
      assert.equal((await progress(page)).trim(), '1/5 done');
      checks.push(`owner ${width}: Enter přidá řádek a kurzor zůstane v poli (2×)`);

      // Odškrtnutí automatické položky ručně.
      const thumb = page.locator('[data-kind="thumbnail"] .shotlist-box');
      await thumb.click();
      await page.waitForFunction(
        () => document.querySelector('[data-kind="thumbnail"] .shotlist-box').getAttribute('aria-checked') === 'true',
      );
      assert.equal((await progress(page)).trim(), '2/5 done');
      assert.equal(await page.locator('[data-kind="thumbnail"] .shotlist-delete').count(), 0);
      checks.push(`owner ${width}: Thumbnail photo odškrtnuto ručně, nemá Delete`);

      // Pořadí nahoru.
      const last = page.locator('.shotlist-row').filter({ hasText: 'Kiln opening reaction' });
      await last.getByRole('button', { name: /Move .* up/ }).click();
      await page.waitForFunction(() => {
        const rows = [...document.querySelectorAll('[data-shotlist] .shotlist-text')].map((n) => n.textContent);
        return rows[3].includes('Kiln opening reaction');
      });
      checks.push(`owner ${width}: posun nahoru`);

      // Úprava textu a poznámky.
      const glaze = page.locator('.shotlist-row').filter({ hasText: 'Pouring the glaze' });
      await glaze.locator('summary').click();
      await glaze.locator('input[name=text]').fill('Pouring the blue glaze');
      await glaze.locator('textarea[name=note]').fill('Use the macro lens');
      await glaze.getByRole('button', { name: 'Save' }).click();
      await page.locator('.shotlist-row', { hasText: 'Use the macro lens' }).waitFor();
      assert((await texts(page)).includes('Pouring the blue glaze'));
      checks.push(`owner ${width}: úprava textu a poznámky`);
      await page.screenshot({ path: `${out}/shotlist-owner-edited-${width}.png`, fullPage: true });

      // Smazání s potvrzením.
      page.once('dialog', (dialog) => dialog.accept());
      const kiln = page.locator('.shotlist-row').filter({ hasText: 'Kiln opening reaction' });
      await kiln.locator('summary').click();
      await kiln.getByRole('button', { name: 'Delete item' }).click();
      await page.waitForFunction(() => { const root = document.querySelector('[data-shotlist]'); return root && !root.textContent.includes('Kiln opening'); });
      assert.equal((await progress(page)).trim(), '2/4 done');
      assert(await noOverflow(page), `detail overflow after edits ${width}`);
      checks.push(`owner ${width}: smazání s potvrzením`);

      // Výpadek spojení: server přidání uloží, odpověď se ztratí. Žádné nové odeslání, jen načtení seznamu.
      let posts = 0;
      await page.route('**/shotlist/', async (route) => {
        if (route.request().method() !== 'POST') return route.continue();
        posts += 1;
        await route.fetch();
        return route.abort('connectionreset');
      });
      await page.locator('#shotlist-new-text').fill('Lost answer shot');
      await page.locator('#shotlist-new-text').press('Enter');
      await page.locator('[data-shotlist] [role=alert]', { hasText: 'may or may not have been saved' }).waitFor();
      await page.unroute('**/shotlist/');
      assert.equal(posts, 1, 'POST odeslán jen jednou');
      assert.equal((await texts(page)).filter((t) => t === 'Lost answer shot').length, 1);
      assert.equal(await page.locator('#shotlist-new-text').inputValue(), 'Lost answer shot');
      assert.equal(await page.evaluate(() => document.activeElement.id), 'shotlist-new-text');
      await page.screenshot({ path: `${out}/shotlist-dropped-${width}.png`, fullPage: width === 1440 });
      page.once('dialog', (dialog) => dialog.accept());
      const lost = page.locator('.shotlist-row').filter({ hasText: 'Lost answer shot' });
      await lost.locator('summary').click();
      await lost.getByRole('button', { name: 'Delete item' }).click();
      await page.waitForFunction(() => {
        const root = document.querySelector('[data-shotlist]');
        return root && !root.textContent.includes('Lost answer shot');
      });
      assert.equal((await progress(page)).trim(), '2/4 done');
      checks.push(`owner ${width}: ztracená odpověď neodešle přidání znovu, načte seznam a upozorní`);

      // Spojení může selhat také při čtení těla odpovědi, až po přijetí hlaviček.
      await page.evaluate(() => {
        const original = window.fetch;
        window.shotlistBodyFailurePosts = 0;
        window.fetch = async (...args) => {
          const response = await original(...args);
          if (args[1]?.method === 'POST') {
            window.shotlistBodyFailurePosts += 1;
            response.text = async () => { throw new TypeError('Interrupted response body'); };
          }
          return response;
        };
      });
      await page.locator('#shotlist-new-text').fill('Interrupted body shot');
      await page.locator('#shotlist-new-text').press('Enter');
      await page.locator('[data-shotlist] [role=alert]', { hasText: 'may or may not have been saved' }).waitFor();
      assert.equal(await page.evaluate(() => window.shotlistBodyFailurePosts), 1);
      assert.equal((await texts(page)).filter((t) => t === 'Interrupted body shot').length, 1);
      assert.equal(await page.locator('[data-shotlist]').getAttribute('aria-busy'), null);
      await page.screenshot({ path: `${out}/shotlist-body-dropped-${width}.png`, fullPage: width === 1440 });
      await page.reload({ waitUntil: 'networkidle' });
      page.once('dialog', (dialog) => dialog.accept());
      const interrupted = page.locator('.shotlist-row').filter({ hasText: 'Interrupted body shot' });
      await interrupted.locator('summary').click();
      await interrupted.getByRole('button', { name: 'Delete item' }).click();
      await page.waitForFunction(() => !document.querySelector('[data-shotlist]').textContent.includes('Interrupted body shot'));
      checks.push(`owner ${width}: přerušené čtení odpovědi načte seznam bez opakování POST`);

      // Po obnovení stránky zůstane stav uložený a historie ho ukazuje.
      await page.reload({ waitUntil: 'networkidle' });
      assert.equal((await progress(page)).trim(), '2/4 done');
      await page.goto(`${detail}?tab=history`, { waitUntil: 'networkidle' });
      const history = await page.locator('[role=tabpanel]').innerText();
      for (const line of [
        'Added "Pouring the glaze" to the shotlist',
        'Checked off "Thumbnail photo" in the shotlist',
        'Removed "Kiln opening reaction" from the shotlist',
      ]) {
        assert(history.includes(line), `history: ${line}`);
      }
      await page.screenshot({ path: `${out}/history-${width}.png`, fullPage: width === 1440 });
      checks.push(`owner ${width}: uložení po obnovení a History`);
      await context.close();

      console.log(`Client ${width}: jen čtení`);
      const clientView = await open(width, 'client');
      await clientView.page.goto(`${detail}?tab=shotlist`, { waitUntil: 'networkidle' });
      assert.equal((await progress(clientView.page)).trim(), '2/4 done');
      assert.equal(await clientView.page.locator('[data-shotlist] form').count(), 0);
      assert.equal(await clientView.page.locator('[data-shotlist] [aria-disabled="true"]').count(), 4);
      assert(await noOverflow(clientView.page), `client overflow ${width}`);
      await clientView.page.screenshot({ path: `${out}/shotlist-client-${width}.png`, fullPage: true });
      checks.push(`client ${width}: shotlist vidí, bez formulářů`);
      await clientView.context.close();
    }
    assert.deepEqual(errors, [], 'JavaScript errors');
    assert.deepEqual(external, [], 'external requests');
    console.log(JSON.stringify({ ok: true, checks }, null, 1));
  } finally {
    await browser.close();
    server.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
