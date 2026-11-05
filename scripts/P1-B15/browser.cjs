// P1-B15: lokální Chromium. Spouštět jen v Dockeru s pamětí <=3 GB a timeoutem <=600 s.
const { chromium } = require('/opt/jrc-tools/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');

(async () => {
  const origin = 'http://127.0.0.1:13573';
  const root = '/workspace/ops';
  const seedLog = await fs.readFile(`${root}/test-output/P1-B15/seed.log`, 'utf8');
  const seed = JSON.parse(seedLog.split('\n').find(line => line.startsWith('SEED=')).slice(5));
  const path = name => origin + seed.urls[name];
  const screenshot = `${root}/screenshoty/P1-B15`;
  const browser = await chromium.launch({ headless: true, args: ['--js-flags=--max-old-space-size=512'] });
  const deadline = setTimeout(() => { browser.close().finally(() => process.exit(124)); }, 480000);
  const external = [], errors = [];
  async function session(role, width) {
    const context = await browser.newContext({
      viewport: { width, height: width === 390 ? 844 : 1000 },
      timezoneId: 'Europe/Prague', isMobile: width === 390, hasTouch: width === 390,
    });
    await context.addCookies([{ name: 'sessionid', value: seed.sessions[role], url: origin }]);
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin === origin) return route.continue();
      external.push(route.request().url()); return route.abort();
    });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    page.setDefaultNavigationTimeout(30000);
    page.on('pageerror', error => errors.push(error.message));
    return { context, page };
  }
  async function snap(page, name, width) {
    await page.evaluate(() => document.fonts.ready);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Přetečení ${name}/${width}`);
    await page.screenshot({ path: `${screenshot}/${name}-${width}.png`, fullPage: false });
  }
  async function iconsLoaded(page) {
    const icons = page.locator(`img[data-channel-icon="${seed.custom}"]:visible`);
    assert(await icons.count() > 0, 'Viditelná vlastní ikona');
    await icons.first().evaluate(img => img.decode());
    assert(await icons.first().evaluate(img => img.naturalWidth === 128));
  }
  try {
    for (const width of [1440, 390]) {
      const { context, page } = await session('owner', width);
      assert.equal((await page.goto(path('profile'))).status(), 200);
      await page.getByLabel('Workspace colour (HEX)').fill('#FFFFFF');
      await page.getByRole('button', { name: 'Save colour', exact: true }).click();
      await page.getByText('Choose a darker colour:', { exact: false }).waitFor();
      await page.getByRole('button', { name: 'Use Violet', exact: true }).click();
      await page.getByText('Workspace colour saved.', { exact: true }).waitFor();
      await page.getByLabel('Workspace colour (HEX)').fill('#5b21b6');
      await page.getByRole('button', { name: 'Save colour', exact: true }).click();
      await page.getByText('Current colour: #5B21B6', { exact: false }).waitFor();
      const upload = page.locator(`#icon-${seed.custom}`);
      await upload.setInputFiles({ name: 'unsafe.svg', mimeType: 'image/svg+xml', buffer: Buffer.from('<svg></svg>') });
      await page.getByRole('button', { name: 'Upload icon', exact: true }).click();
      await page.getByText('Upload a PNG, JPG or WebP image, at most 1 MB and 4 million pixels.', { exact: true }).waitFor();
      await upload.setInputFiles({ name: 'forendors.png', mimeType: 'image/png', buffer: Buffer.from(seed.png, 'base64') });
      await page.getByRole('button', { name: 'Upload icon', exact: true }).click();
      await page.getByText('Channel icon saved.', { exact: true }).waitFor();
      await iconsLoaded(page);
      await snap(page, 'owner-profile', width);
      await page.locator(`[data-channel=custom]`).scrollIntoViewIfNeeded();
      await snap(page, 'owner-channels', width);
      // Persistence and each requested surface, including the channel menu.
      await page.reload();
      assert.equal(await page.getByLabel('Workspace colour (HEX)').inputValue(), '#5B21B6');
      for (const [name, suffix] of [['detail', ''], ['caption', '?tab=caption'], ['publish', '?tab=publish']]) {
        assert.equal((await page.goto(path('detail') + suffix)).status(), 200);
        assert.equal(await page.locator('header[style*="border-top"]').evaluate(el => getComputedStyle(el).borderTopColor), 'rgb(91, 33, 182)');
        await iconsLoaded(page);
        if (name !== 'detail') assert(await page.locator(`h3 img[data-channel-icon="${seed.custom}"]`).count() > 0);
        await snap(page, `owner-${name}`, width);
      }
      for (const name of ['calendar', 'cross_calendar', 'board']) {
        assert.equal((await page.goto(path(name))).status(), 200);
        assert((await page.content()).includes('#5B21B6'));
        await iconsLoaded(page);
        if (name === 'board') assert(await page.locator(`.board-channel img[data-channel-icon="${seed.custom}"]`).count() > 0);
        if (name === 'cross_calendar') assert(await page.locator(`.cross-post-chip img[data-channel-icon="${seed.custom}"]`).count() > 0);
        await snap(page, `owner-${name}`, width);
      }
      const imageUrl = await page.locator(`img[data-channel-icon="${seed.custom}"]`).first().getAttribute('src');
      await context.close();
      const client = await session('client', width);
      assert.equal((await client.page.goto(path('profile'))).status(), 200);
      assert.equal(await client.page.getByRole('button', { name: 'Save colour', exact: true }).count(), 0);
      assert.equal(await client.page.getByRole('button', { name: 'Upload icon', exact: true }).count(), 0);
      await client.page.getByText('Current colour: #5B21B6', { exact: false }).waitFor();
      await iconsLoaded(client.page);
      const response = await client.context.request.get(origin + imageUrl);
      assert.equal(response.status(), 200);
      assert.equal(response.headers()['cache-control'], 'private, no-store');
      assert.equal((await response.body()).subarray(1, 4).toString(), 'PNG');
      await snap(client.page, 'client-profile', width);
      await client.page.locator(`[data-channel=custom]`).scrollIntoViewIfNeeded();
      await snap(client.page, 'client-channels', width);
      for (const name of ['detail', 'caption', 'publish', 'calendar', 'board']) {
        const url = ['caption', 'publish'].includes(name) ? path('detail') + `?tab=${name}` : path(name);
        assert.equal((await client.page.goto(url)).status(), 200);
        await iconsLoaded(client.page);
        await snap(client.page, `client-${name}`, width);
      }
      await client.context.close();
      const anonymous = await browser.newContext();
      assert.equal((await anonymous.request.get(origin + imageUrl)).status(), 403);
      await anonymous.close();
      console.log(`OK Chromium ${width}: HEX, paleta, kontrast, odmítnutí SVG, upload PNG, přetrvání, owner/klient, soukromá ikona a zobrazení.`);
    }
    assert.deepEqual(external, [], 'Žádné požadavky mimo místní aplikaci');
    assert.deepEqual(errors, [], 'Žádné chyby JavaScriptu');
    console.log('OK: Chromium bez přetečení stránky a bez externích požadavků.');
  } finally {
    clearTimeout(deadline);
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
