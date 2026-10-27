// Jen existující Chromium v jrc-dev; místní průchod P1-B06 bez externích požadavků.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
assert.equal(process.env.JRC_PROJECT, 'jrc-p1b06');
const origin = 'http://127.0.0.1:13493';
const base = origin + '/jrc';
(async () => {
  const seedLog = await fs.readFile('ops/test-output/P1-B06/seed.log', 'utf8');
  const seed = JSON.parse(seedLog.split('\n').find(line => line.startsWith('{"workspace"')));
  // Lokální vstup uvnitř vlastního dev kontejneru zachovává veřejný Host i cookies.
  const server = http.createServer((req, res) => {
    const upstream = http.request({hostname: 'jrc-proxy', port: 8080, path: req.url, method: req.method, headers: req.headers}, response => {
      res.writeHead(response.statusCode, response.headers); response.pipe(res);
    });
    res.on('close', () => upstream.destroy());
    upstream.on('error', error => { res.writeHead(502); res.end(error.message); });
    req.pipe(upstream);
  });
  await new Promise(resolve => server.listen(13493, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true});
  const checks = [], errors = [], external = [];
  const runId = Date.now();
  let lastPage;
  try {
    for (const width of [1440, 390]) {
      const context = await browser.newContext({viewport: {width, height: 1000}});
      await context.addCookies([{name: 'sessionid', value: seed.session, url: base + '/'}]);
      await context.route('**/*', route => {
        if (new URL(route.request().url()).origin !== origin) {
          external.push(route.request().url()); return route.abort();
        }
        return route.continue();
      });
      const page = await context.newPage();
      lastPage = page;
      page.setDefaultTimeout(10000);
      page.setDefaultNavigationTimeout(15000);
      console.log(`Průchod ${width}: nástěnka`);
      page.on('pageerror', error => errors.push(error.message));
      const noOverflow = () => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
      await page.goto(base + '/board/?channel=none', {waitUntil: 'networkidle'});
      await page.getByRole('link', {name: 'Unassigned studio idea'}).waitFor();
      assert(await noOverflow(), `Nástěnka ${width}`);
      await page.locator('.board-new summary').click();
      await page.locator('#new-client').selectOption(seed.workspace);
      assert.equal(await page.locator('input[name=channels]:checked').count(), 0);
      await page.locator('#new-title').fill(`Idea from browser ${width} ${runId}`);
      await Promise.all([page.waitForURL(url => !url.searchParams.has('channel')), page.getByRole('button', {name: 'Add to Idea'}).click()]);
      await page.getByRole('link', {name: `Idea from browser ${width} ${runId}`}).click();
      await page.getByRole('heading', {name: `Idea from browser ${width} ${runId}`, exact: true}).waitFor();
      const detail = page.url().split('?')[0];
      console.log(`Průchod ${width}: obsah bez kanálu`);
      for (const tab of ['video', 'caption', 'publish']) {
        await page.goto(detail + '?tab=' + tab, {waitUntil: 'networkidle'});
        await page.getByText('Add a channel first', {exact: true}).waitFor();
        assert(await noOverflow(), `${tab} ${width}`);
      }
      await page.locator('[data-open-channels]').click();
      assert(await page.locator('#content-channels').evaluate(el => el.open));
      await page.locator('#add-channel').selectOption(seed.channel);
      await Promise.all([page.waitForURL(url => url.hash === '#content-channels'), page.locator('#content-channels button').filter({hasText: /^Add channel$/}).click()]);
      await page.locator('#content-channels').getByRole('button', {name: 'Remove channel'}).waitFor();
      await page.locator('#content-channels').getByRole('button', {name: 'Remove channel'}).click();
      await page.getByText('Add a channel first', {exact: true}).waitFor();
      await page.goto(detail + '?tab=caption', {waitUntil: 'networkidle'});
      await page.locator('[data-open-channels]').click();
      await page.locator('#add-channel').selectOption(seed.channel);
      await page.locator('#content-channels button').filter({hasText: /^Add channel$/}).click();
      console.log(`Průchod ${width}: přidán kanál, popisek`);
      await page.getByRole('heading', {name: 'Channel captions'}).waitFor();
      await page.locator('textarea[name=caption]').fill('A saved caption keeps its channel.');
      await page.getByRole('button', {name: 'Save version'}).click();
      await page.getByText('A saved caption keeps its channel.', {exact: true}).first().waitFor();
      await page.locator('#content-channels').evaluate(el => { el.open = true; });
      await page.locator('#content-channels').getByRole('button', {name: 'Remove channel'}).click();
      await page.getByRole('alert').filter({hasText: 'caption history or video cannot be removed'}).waitFor();
      assert(await noOverflow(), `Detail s historií ${width}`);
      await page.screenshot({path: `ops/screenshoty/P1-B06/channels-${width}.png`, fullPage: true});
      await page.goto(`${base}/workspace/${seed.workspace}/calendar/?mode=list&tab=drafts&channel=none`, {waitUntil: 'networkidle'});
      await page.getByRole('link', {name: 'Unassigned studio idea'}).waitFor();
      await page.goto(base + '/board/', {waitUntil: 'networkidle'});
      await page.locator('.board-new summary').click();
      await page.locator('#new-client').selectOption(seed.workspace);
      await page.locator('#new-title').fill(`Selected channel ${width} ${runId}`);
      await page.locator(`.board-new input[name=channels][value="${seed.channel}"]`).check();
      assert.equal(await page.locator('.board-new input[name=channels]:checked').count(), 1);
      await page.getByRole('button', {name: 'Add to Idea'}).click();
      await page.getByRole('link', {name: `Selected channel ${width} ${runId}`}).click();
      const selectedDetail = page.url().split('?')[0];
      await page.goto(selectedDetail + '?tab=caption', {waitUntil: 'networkidle'});
      await page.getByRole('heading', {name: 'Channel captions', exact: true}).waitFor();
      assert.equal(await page.locator('article').count(), 1, 'Jen výslovně vybraný IG, druhý klientův kanál se nepřipojí');
      checks.push({width, createdWithoutChannel: true, selectedChannelAtCreation: true, tabs: true, addRemove: true, historyProtected: true, list: true});
      await context.close();
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    await fs.writeFile('ops/test-output/P1-B06/browser.json', JSON.stringify({checks, errors, external}, null, 2));
    console.log('OK P1-B06: desktop i mobil, založení bez kanálu, záložky, přidání/odebrání, ochrana historie a seznam; bez externích požadavků.');
  } catch (error) {
    if (lastPage) {
      console.error(`Selhání na ${lastPage.url()}`);
      await lastPage.screenshot({path: "ops/screenshoty/P1-B06/failure.png", fullPage: true}).catch(() => {});
      console.error((await lastPage.locator("body").innerText().catch(() => "")).slice(-1500));
    }
    throw error;
  } finally { await browser.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => {console.error(error); process.exitCode = 1;});
