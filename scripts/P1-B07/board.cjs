// Jen existující Chromium v jrc-dev; místní průchod nástěnky P1-B07 bez externích požadavků.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
assert.equal(process.env.JRC_PROJECT, 'jrc-p1b07');
const origin = 'http://127.0.0.1:13503';
const base = origin + '/jrc';
const shots = 'ops/screenshoty/P1-B07';
const results = [];
const note = (name, value) => { results.push({ name, value }); console.log(`${name}: ${JSON.stringify(value)}`); };

(async () => {
  const seedLog = await fs.readFile('ops/test-output/P1-B07/seed.log', 'utf8');
  const seed = JSON.parse(seedLog.split('\n').find(line => line.startsWith('{"ella_ws"')));
  // Lokální vstup uvnitř vlastního dev kontejneru zachovává veřejný Host i cookies.
  const server = http.createServer((req, res) => {
    const upstream = http.request({ hostname: 'jrc-proxy', port: 8080, path: req.url, method: req.method, headers: req.headers }, response => {
      res.writeHead(response.statusCode, response.headers); response.pipe(res);
    });
    res.on('close', () => upstream.destroy());
    upstream.on('error', error => { res.writeHead(502); res.end(error.message); });
    req.pipe(upstream);
  });
  await new Promise(resolve => server.listen(13503, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true });
  const errors = [], external = [];
  const runId = Date.now() % 100000;
  try {
    for (const width of [1440, 390]) {
      const mobile = width < 768;
      const open = async session => {
        const context = await browser.newContext({ viewport: { width, height: mobile ? 844 : 900 }, hasTouch: mobile, isMobile: mobile });
        await context.addCookies([{ name: 'sessionid', value: session, url: base + '/' }]);
        await context.route('**/*', route => {
          if (new URL(route.request().url()).origin !== origin) { external.push(route.request().url()); return route.abort(); }
          return route.continue();
        });
        const page = await context.newPage();
        page.setDefaultTimeout(10000);
        page.on('pageerror', error => errors.push(`${width}: ${error.message}`));
        page.on('console', msg => { if (msg.type() === 'error') errors.push(`${width} console: ${msg.text()}`); });
        return { context, page };
      };
      const { context, page } = await open(seed.owner_session);
      const noOverflow = () => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);

      // AK4: všichni klienti → pak Ella; bez parametrů se nástěnka otevře na posledním klientovi.
      await page.goto(base + '/board/?client=', { waitUntil: 'networkidle' });
      assert(await page.locator('.board-card').count() >= 40, 'aspoň 40 karet');
      assert(await page.locator('.board-client').first().isVisible(), 'jméno klienta při více klientech');
      await page.screenshot({ path: `${shots}/board-all-${width}.png`, fullPage: false });
      await page.locator('#filter-client').selectOption(seed.ella_ws);
      await Promise.all([page.waitForURL(/client=/), page.getByRole('button', { name: 'Apply filters' }).click()]);
      await page.goto(base + '/board/', { waitUntil: 'networkidle' });
      assert.equal(await page.locator('#filter-client').inputValue(), seed.ella_ws, 'pamatovaný klient');
      assert.equal(await page.locator('.board-card .board-client').count(), 0, 'filtr na klienta skryje jméno');
      assert(await noOverflow(), `stránka bez vodorovného přetečení ${width}`);
      await page.screenshot({ path: `${shots}/board-${width}.png`, fullPage: false });

      // AK2: kompaktní karta – výška a ořez názvu.
      const heights = await page.locator('.board-card').evaluateAll(cards => cards.map(c => c.getBoundingClientRect().height));
      const avg = Math.round(heights.reduce((a, b) => a + b, 0) / heights.length);
      note(`karta ${width} px: průměrná / max. výška`, [avg, Math.round(Math.max(...heights))]);
      const longTitle = page.locator('.board-card h3').filter({ hasText: 'Morning routine in the studio' });
      const clamp = await longTitle.evaluate(h => ({ lines: Math.round(h.getBoundingClientRect().height / parseFloat(getComputedStyle(h).lineHeight)), clipped: h.scrollHeight > h.clientHeight }));
      note(`dlouhý název ${width} px`, clamp);
      assert(clamp.lines <= 3 && clamp.clipped, 'dlouhý název max. 3 řádky s „…“');
      const unassigned = page.locator('.board-card').filter({ hasText: 'Idea 1: quick thought' }).first();
      assert(await unassigned.getByText('No channel', { exact: true }).isVisible());
      const commented = page.locator('.board-card').filter({ hasText: 'Script: watering mistakes' });
      assert.equal(parseInt(await commented.locator('.board-comments').innerText()), 3, 'owner vidí všechny 3 komentáře');
      assert(await commented.locator('time').isVisible(), 'navržené datum');
      assert.equal(await unassigned.locator('.board-comments, time').count(), 0, 'bez komentářů a data nic navíc');

      // AK4: řazení – datované první (nejbližší), pak podle změny.
      const ideaTitles = await page.locator('.board-column[data-stage="idea"] .board-card h3').allInnerTexts();
      assert.deepEqual(ideaTitles.slice(0, 3).map(t => t.slice(0, 20)), ['Dated idea for tomor', 'Morning routine in t', 'Dated idea for next ']);

      // AK3: sloupce – záhlaví zůstává viditelné, prázdné úzké, posun jen uvnitř nástěnky.
      const idea = page.locator('.board-column[data-stage="idea"]');
      if (!mobile) {
        const widths = await page.locator('.board-column').evaluateAll(cols => cols.map(c => [c.dataset.stage, Math.round(c.getBoundingClientRect().width), c.classList.contains('is-empty')]));
        note(`šířky sloupců ${width}`, widths);
        const empty = widths.filter(w => w[2]), full = widths.filter(w => !w[2]);
        assert(empty.length && Math.max(...empty.map(w => w[1])) < Math.min(...full.map(w => w[1])), 'prázdné sloupce užší');
        const grid = await page.locator('.board-grid').evaluate(g => ({ scroll: g.scrollWidth, client: g.clientWidth }));
        note(`nástěnka ${width}: scrollWidth/clientWidth`, grid);
        await idea.evaluate(c => { c.scrollTop = 900; });
        const sticky = await idea.evaluate(c => ({ col: c.getBoundingClientRect().top, head: c.querySelector('h2').getBoundingClientRect().top, scrolled: c.scrollTop }));
        note(`lepicí záhlaví ${width}`, sticky);
        assert(sticky.scrolled > 0 && Math.abs(sticky.head - sticky.col) < 6, 'záhlaví sloupce zůstává nahoře');
        await page.screenshot({ path: `${shots}/board-${width}-scrolled.png` });
        await idea.evaluate(c => { c.scrollTop = 0; });
      } else {
        await page.evaluate(() => { const s = document.querySelector('.board-grid').closest('.overflow-y-auto'); s.scrollTop = 900; });
        const sticky = await idea.evaluate(c => {
          const scroller = c.closest('.overflow-y-auto');
          return { scroller: scroller.getBoundingClientRect().top, head: c.querySelector('h2').getBoundingClientRect().top, col: c.getBoundingClientRect().top };
        });
        note(`lepicí záhlaví ${width}`, sticky);
        assert(sticky.col < sticky.scroller && Math.abs(sticky.head - sticky.scroller) < 6, 'záhlaví sloupce zůstává nahoře');
        await page.screenshot({ path: `${shots}/board-${width}-scrolled.png` });
        await page.evaluate(() => { document.querySelector('.board-grid').closest('.overflow-y-auto').scrollTop = 0; });
        const emptyHeight = await page.locator('.board-column.is-empty').first().evaluate(c => c.getBoundingClientRect().height);
        note(`prázdný sloupec ${width}: výška`, Math.round(emptyHeight));
        assert(emptyHeight < 60, 'prázdný sloupec nenápadný');
      }

      // AK1: celá karta je odkaz (kliknutí mimo název, klávesnice), Move… nespouští odkaz.
      const target = page.locator('.board-card').filter({ hasText: 'Script: watering mistakes' });
      await target.scrollIntoViewIfNeeded();
      const box = await target.boundingBox();
      if (mobile) await page.touchscreen.tap(box.x + 8, box.y + box.height - 6); else await page.mouse.click(box.x + 8, box.y + box.height - 6);
      await page.getByRole('heading', { name: 'Script: watering mistakes', exact: true }).waitFor();
      assert(/\/content\//.test(page.url()), 'klik na plochu karty otevře detail');
      await page.goBack({ waitUntil: 'networkidle' });
      const link = page.locator('.board-card-link').filter({ hasText: 'Editing: soil comparison' });
      await link.focus();
      await page.keyboard.press('Enter');
      await page.getByRole('heading', { name: 'Editing: soil comparison', exact: true }).waitFor();
      await page.goBack({ waitUntil: 'networkidle' });
      const before = page.url();
      const moveCard = page.locator('.board-card').filter({ has: page.getByRole('heading', { name: 'Script: three cuttings', exact: true }) });
      await moveCard.locator('summary').click();
      await moveCard.getByLabel('Move to', { exact: true }).waitFor();
      assert.equal(page.url(), before, 'Move… neotevře detail');
      await moveCard.getByLabel('Move to', { exact: true }).selectOption('shooting');
      await page.screenshot({ path: `${shots}/board-${width}-move.png` });
      await Promise.all([page.waitForURL(/client=/), moveCard.getByRole('button', { name: 'Confirm move' }).click()]);
      await page.locator('.board-column[data-stage="shooting"] .board-card').filter({ hasText: 'Script: three cuttings' }).waitFor();
      // zpět, aby druhý průchod začínal stejně
      const back = page.locator('.board-card').filter({ has: page.getByRole('heading', { name: 'Script: three cuttings', exact: true }) });
      await back.locator('summary').click();
      await back.getByLabel('Move to', { exact: true }).selectOption('script');
      await Promise.all([page.waitForURL(/client=/), back.getByRole('button', { name: 'Confirm move' }).click()]);

      // AK4b: tři nápady za sebou do 15 s, bez přechodu jinam.
      await page.goto(base + '/board/', { waitUntil: 'networkidle' });
      const ideaCount = async () => Number(await idea.locator('[data-stage-count]').innerText());
      const startCount = await ideaCount();
      const field = page.locator('#idea-title');
      if (mobile) {
        assert(!(await field.isVisible()), 'na telefonu pole schované za „+ Idea“');
        assert(await page.getByRole('button', { name: '+ Idea' }).isVisible());
        await page.getByRole('button', { name: '+ Idea' }).click();
      }
      assert(await field.isVisible(), 'pole Idea… viditelné');
      assert.equal(await field.getAttribute('placeholder'), 'Idea…');
      const url = page.url();
      const started = Date.now();
      for (const n of [1, 2, 3]) {
        await field.fill(`Quick idea ${n} ${width} ${runId}`);
        await field.press('Enter');
        await idea.locator('.board-card').filter({ hasText: `Quick idea ${n} ${width} ${runId}` }).waitFor();
        assert.equal(await field.inputValue(), '', 'pole se vyprázdní');
      }
      const seconds = (Date.now() - started) / 1000;
      note(`3 nápady ${width} px (s)`, seconds);
      assert(seconds < 15);
      assert.equal(page.url(), url, 'bez přechodu jinam');
      assert.equal(await ideaCount(), startCount + 3);
      assert(await field.evaluate(el => el === document.activeElement), 'kurzor zůstává v poli');
      await page.screenshot({ path: `${shots}/board-${width}-ideas.png` });
      await page.reload({ waitUntil: 'networkidle' });
      const saved = page.locator('.board-card').filter({ hasText: `Quick idea 3 ${width} ${runId}` });
      assert.equal(await saved.getByText('No channel', { exact: true }).count(), 1, 'nápad bez kanálu');
      assert.equal(await saved.locator('time').count(), 0, 'nápad bez data');
      assert(await noOverflow(), `po nápadech bez přetečení ${width}`);
      await context.close();

      // Klient: vlastní workspace, vidí jen externí komentáře, smí přidat nápad.
      const client = await open(seed.client_session);
      const cpage = client.page;
      await cpage.goto(base + '/board/', { waitUntil: 'networkidle' });
      const ccard = cpage.locator('.board-card').filter({ hasText: 'Script: watering mistakes' });
      assert.equal(parseInt(await ccard.locator('.board-comments').innerText()), 2, 'klient vidí jen 2 externí komentáře');
      assert.equal(await cpage.locator('#idea-client').count(), 0, 'klient s jedním workspace nevybírá klienta');
      if (mobile) await cpage.getByRole('button', { name: '+ Idea' }).click();
      await cpage.locator('#idea-title').fill(`Client idea ${width} ${runId}`);
      await cpage.getByRole('button', { name: 'Add', exact: true }).click();
      await cpage.locator('.board-column[data-stage="idea"] .board-card').filter({ hasText: `Client idea ${width} ${runId}` }).waitFor();
      assert(await cpage.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await cpage.screenshot({ path: `${shots}/client-${width}.png` });
      await client.context.close();
    }
    assert.deepEqual(errors, [], 'bez chyb JavaScriptu');
    assert.deepEqual(external, [], 'bez externích požadavků');
    console.log('VÝSLEDEK: PROŠLO');
  } catch (error) {
    console.error('VÝSLEDEK: SELHALO', error);
    process.exitCode = 1;
  } finally {
    await fs.writeFile('ops/test-output/P1-B07/browser.json', JSON.stringify({ results, errors, external }, null, 2));
    await browser.close();
    server.close();
  }
})();
