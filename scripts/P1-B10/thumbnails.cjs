// P1-B10: Chromium v Dockeru, místní data a skutečné nahrání/stažení přes soukromé úložiště.
const { chromium } = require('/opt/jrc-tools/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const net = require('node:net');

(async () => {
  const origin = 'http://127.0.0.1:13533';
  const base = `${origin}/jrc`;
  const root = '/workspace/ops';
  const seed = JSON.parse(await fs.readFile(`${root}/test-output/P1-B10/session.json`, 'utf8'));
  // A loopback TCP bridge inside jrc-dev preserves multipart file bytes, cookies,
  // CSRF origin and redirects. No host port or external network is involved.
  const sockets = new Set();
  const bridge = net.createServer(socket => {
    const upstream = net.connect(8080, 'jrc-proxy');
    for (const stream of [socket, upstream]) {
      sockets.add(stream);
      stream.on('close', () => sockets.delete(stream));
      stream.on('error', () => { socket.destroy(); upstream.destroy(); });
    }
    socket.pipe(upstream).pipe(socket);
  });
  await new Promise((resolve, reject) => {
    bridge.once('error', reject);
    bridge.listen(13533, '127.0.0.1', resolve);
  });
  const browser = await chromium.launch({ headless: true });
  const errors = [], external = [], screenshots = [];
  const contextFor = async (role, width) => {
    const context = await browser.newContext({
      timezoneId: 'Europe/Prague', viewport: { width, height: width === 390 ? 844 : 1050 },
      isMobile: width === 390, hasTouch: width === 390,
    });
    await context.addCookies([{ name: 'sessionid', value: seed.sessions[role], url: base }]);
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) { external.push(url.href); return route.abort(); }
      await route.continue();
    });
    context.on('page', page => {
      page.on('pageerror', error => errors.push(String(error)));
      page.on('requestfailed', request => {
        if (request.failure()?.errorText !== 'net::ERR_ABORTED') console.error('REQUEST FAILED', request.url(), request.failure());
      });
    });
    return context;
  };
  const capture = async (page, name, width) => {
    await page.evaluate(() => document.fonts.ready);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Přetečení: ${name}/${width}`);
    await page.screenshot({ path: `${root}/screenshoty/P1-B10/${name}-${width}.png` });
    screenshots.push(`${name}-${width}.png`);
  };
  const upload = async (page, channel, file, version) => {
    const input = channel.locator('[data-thumbnail-input]');
    assert.equal(await input.getAttribute('accept'), 'image/*');
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'networkidle' }),
      input.setInputFiles(`/workspace/ops/test-output/P1-B10/garden.${file}`),
    ]);
    try {
      await channel.locator('[data-thumbnail-current]').filter({ hasText: `Version ${version}` }).waitFor({timeout: 10000});
    } catch (error) {
      console.error('UPLOAD PAGE', (await page.locator('body').innerText()).slice(0, 4000));
      await page.screenshot({path: `${root}/screenshoty/P1-B10/upload-failure.png`, fullPage: true});
      throw error;
    }
    await channel.locator('[data-thumbnail-image]').evaluate(img => {
      if (!img.complete || img.naturalWidth === 0) throw new Error('Náhled se nenačetl');
    });
  };
  const download = async (page, channel, extension) => {
    const [file] = await Promise.all([page.waitForEvent('download'), channel.locator('[data-thumbnail-download]').click()]);
    assert(file.suggestedFilename().endsWith(`.${extension}`));
    const data = await fs.readFile(await file.path());
    const original = await fs.readFile(`/workspace/ops/test-output/P1-B10/garden.${extension}`);
    assert.deepEqual(data, original);
  };
  try {
    for (const width of [1440, 390]) {
      const post = seed.posts[width];
      const detail = `${base}/workspace/${seed.workspace}/content/${post.id}/`;
      const owner = await contextFor('owner', width);
      const page = await owner.newPage();
      await page.goto(`${detail}?tab=caption`, { waitUntil: 'networkidle' });
      const first = page.locator(`#thumbnail-${post.variants[0]}`);
      const second = page.locator(`#thumbnail-${post.variants[1]}`);
      assert.equal(await page.locator('[data-channel-thumbnail]').count(), 2);
      await capture(page, '01-caption-prazdny', width);
      // Picking a thumbnail must not discard unsaved text in another channel.
      const caption = page.locator('[data-caption-counter]').last();
      await caption.fill('Unsaved caption must stay');
      await first.locator('[data-thumbnail-input]').setInputFiles(`${root}/test-output/P1-B10/garden.png`);
      assert.match(await first.locator('[data-thumbnail-status]').textContent(), /Save caption changes/);
      assert.equal(await caption.inputValue(), 'Unsaved caption must stay');
      assert.equal(await first.locator('[data-thumbnail-image]').count(), 0);
      await caption.fill('');
      await upload(page, first, 'png', 1);
      assert.equal(await second.locator('[data-thumbnail-image]').count(), 0);
      await capture(page, '02-caption-nahrano', width);
      await page.goto(`${detail}?tab=publish`, { waitUntil: 'networkidle' });
      await upload(page, second, 'webp', 1);
      await upload(page, first, 'jpg', 2);
      await first.locator('[data-thumbnail-history] summary').click();
      const history = first.locator('[data-thumbnail-history] a').first();
      assert.equal((await history.textContent()).trim(), 'Version 1');
      const oldUrl = await history.getAttribute('href');
      assert.equal(await page.evaluate(async url => (await fetch(url)).status, oldUrl), 200);
      await download(page, first, 'jpg');
      await capture(page, '03-publish-historie', width);
      await page.goto(`${detail}?tab=approval`, { waitUntil: 'networkidle' });
      assert.equal(await page.locator('[data-thumbnail-summary] img').count(), 2);
      assert.equal(await page.locator('[data-thumbnail-input]').count(), 0);
      await capture(page, '04-approval', width);
      await owner.close();

      const client = await contextFor('client', width);
      const cpage = await client.newPage();
      await cpage.goto(`${detail}?tab=publish`, { waitUntil: 'networkidle' });
      assert.equal(await cpage.locator('[data-thumbnail-input]').count(), 0);
      assert.equal(await cpage.locator('[data-thumbnail-download]').count(), 2);
      await download(cpage, cpage.locator(`#thumbnail-${post.variants[0]}`), 'jpg');
      await download(cpage, cpage.locator(`#thumbnail-${post.variants[1]}`), 'webp');
      await capture(cpage, '05-klient-publish', width);
      await cpage.goto(`${detail}?tab=caption`, { waitUntil: 'networkidle' });
      assert.equal(await cpage.locator('[data-thumbnail-image]').count(), 2);
      assert.equal(await cpage.locator('[data-thumbnail-input]').count(), 0);
      await capture(cpage, '06-klient-caption', width);
      await cpage.goto(`${detail}?tab=approval`, { waitUntil: 'networkidle' });
      assert.equal(await cpage.locator('[data-thumbnail-summary] img').count(), 2);
      await capture(cpage, '07-klient-approval', width);
      // Stejná adresa obrázku po odhlášení nesmí vydat soubor.
      await client.clearCookies();
      assert.equal(await cpage.evaluate(async url => (await fetch(url)).status, oldUrl), 403);
      await client.close();
    }
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    console.log('OK: nahrání PNG/JPG/WebP, dvě nezávislé kopie, historie, stažení bajt po bajtu, owner/klient, 1440/390.');
    console.log(screenshots.join('\n'));
  } finally {
    await browser.close();
    for (const socket of sockets) socket.destroy();
    await new Promise(resolve => bridge.close(resolve));
  }
})().catch(error => { console.error(error); process.exit(1); });
