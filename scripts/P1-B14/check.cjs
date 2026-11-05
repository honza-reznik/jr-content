// P1-B14: Chromium pouze v Dockeru, skutečné uložení, nahrání a stažení vlastních dat.
const { chromium } = require('/opt/jrc-tools/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const { createReadStream } = require('node:fs');
const { createHash } = require('node:crypto');
const { execFileSync } = require('node:child_process');
const net = require('node:net');

(async () => {
  const origin = 'http://127.0.0.1:13563', base = `${origin}/jrc`;
  const root = '/workspace/ops', output = `${root}/test-output/P1-B14`;
  const seed = JSON.parse(await fs.readFile(`${output}/session.json`, 'utf8'));
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
  await new Promise((resolve, reject) => { bridge.once('error', reject); bridge.listen(13563, '127.0.0.1', resolve); });
  const browser = await chromium.launch({headless: true, args: ['--js-flags=--max-old-space-size=512']});
  process.once('SIGTERM', async () => {
    await browser.close();
    process.exit(124);
  });
  const errors = [], external = [];
  async function login(role, width) {
    const context = await browser.newContext({
      viewport: {width, height: width === 390 ? 844 : 1000}, timezoneId: 'Europe/Prague',
      isMobile: width === 390, hasTouch: width === 390,
    });
    await context.addCookies([{name: 'sessionid', value: seed.sessions[role], url: base}]);
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.origin === origin) return route.continue();
      external.push(url.origin); return route.abort();
    });
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    page.setDefaultNavigationTimeout(30000);
    page.on('pageerror', e => errors.push(e.message));
    page.on('response', response => { if (response.status() >= 400) console.log('HTTP', response.status(), new URL(response.url()).pathname); });
    return {context, page};
  }
  async function snap(page, name, width) {
    await page.evaluate(() => document.fonts.ready);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Přetečení ${name}/${width}`);
    await page.screenshot({path: `${root}/screenshoty/P1-B14/${name}-${width}.png`, fullPage: true});
  }
  async function saved(page, kind, text, number) {
    await page.locator('#script-edit').click();
    await page.locator('#script-editor [contenteditable=true]').waitFor();
    await page.getByRole('textbox', {name: `${kind} text`, exact: true}).fill(text);
    await page.locator('#script-save').click();
    await page.locator('#script-version').filter({hasText: `Version ${number} · Latest saved`}).waitFor();
    assert.equal(await page.locator('#script-versions > li').count(), number);
  }
  async function history(page) {
    for (const text of ['Saved Concept version 1', 'Saved Concept version 2', 'Saved Script version 1', 'Saved Caption version 1', 'Instagram', 'Garden reels']) {
      assert((await page.locator('body').innerText()).includes(text), `History: ${text}`);
    }
  }
  const finalHashes = new Map();
  async function downloads(page, width) {
    const copied = page.locator('[data-copy-caption]');
    assert.equal(await copied.count(), 1);
    await copied.click();
    await page.waitForFunction(() => {
      const status = document.querySelector('[data-copy-status]')?.textContent || '';
      return /copied|Select and copy/.test(status);
    });
    for (const label of ['Download final video', 'Download thumbnail']) {
      const [file] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', {name: label, exact: true}).click()]);
      assert.equal(await file.failure(), null);
      async function digest(path) {
        const hash = createHash('sha256');
        for await (const chunk of createReadStream(path)) hash.update(chunk);
        return hash.digest('hex');
      }
      const path = await file.path(), hash = await digest(path);
      if (label.includes('video')) {
        // FreeFrame serves a transcoded MP4; it need not equal the source upload.
        const info = JSON.parse(execFileSync('ffprobe', [
          '-v', 'error', '-show_format', '-show_streams', '-of', 'json', path,
        ], {timeout: 10000, maxBuffer: 65536}).toString());
        assert(info.streams.some(stream => stream.codec_type === 'video' && stream.width === 180 && stream.height === 320));
        assert(Math.abs(Number(info.format.duration) - 2) < 0.3, 'Délka staženého videa');
        if (finalHashes.has(width)) assert.equal(hash, finalHashes.get(width), 'Stejné finální video po publikaci');
        else finalHashes.set(width, hash);
      } else {
        assert.equal(hash, await digest(`${output}/garden.png`), label);
      }
    }
  }
  try {
    for (const width of [1440, 390]) {
      console.log(`P1-B14 ${process.argv[2]} ${width}: začátek`);
      const post = seed.posts[width];
      const detail = `${base}/workspace/${seed.workspace}/content/${post.id}/`;
      const {page, context} = await login('owner', width);
      if (process.argv[2] === 'texts') {
        await page.goto(`${detail}?tab=concept`, {waitUntil: 'networkidle'});
        // Discarding the first draft must still let the first actual save be v1.
        await page.locator('#script-edit').click();
        await page.locator('#script-editor [contenteditable=true]').waitFor();
        page.once('dialog', dialog => dialog.accept());
        await page.locator('#script-discard').click();
        await page.locator('#script-edit').waitFor({state: 'visible'});
        await saved(page, 'Concept', 'Three plants for autumn', 1);
        await saved(page, 'Concept', 'Four plants for autumn', 2);
        await snap(page, 'owner-concept', width);
        await page.goto(`${detail}?tab=script`, {waitUntil: 'networkidle'});
        await saved(page, 'Script', 'Show the garden and introduce four plants.', 1);
        await snap(page, 'owner-script', width);
        await page.goto(`${detail}?tab=caption`, {waitUntil: 'networkidle'});
        await page.locator('[data-caption-counter]').fill('Four plants for autumn in your garden.');
        await page.getByRole('button', {name: 'Save version', exact: true}).click();
        await page.getByText('Saved version 1', {exact: true}).waitFor();
        const input = page.locator('[data-thumbnail-input]');
        const status = page.locator('[data-thumbnail-status]');
        await Promise.all([page.waitForNavigation({waitUntil: 'networkidle'}), input.setInputFiles(`${output}/garden.png`)]);
        await page.locator('[data-thumbnail-current]').filter({hasText: 'Version 1'}).waitFor();
        let uploads = 0;
        const track = request => { if (request.method() === 'POST' && request.url().includes('/thumbnail')) uploads++; };
        page.on('request', track);
        for (const file of [
          {name: 'video.MOV', mimeType: 'video/quicktime', buffer: Buffer.from('fake movie')},
          {name: 'animation.gif', mimeType: 'image/gif', buffer: Buffer.from('GIF89a')},
          {name: 'huge.png', mimeType: 'image/png', buffer: Buffer.alloc(20 * 1024 * 1024 + 1)},
        ]) {
          await input.setInputFiles(file);
          await status.filter({hasText: /not supported|larger than/}).waitFor();
          assert.match(await status.innerText(), /JPG, PNG or WebP, up to 20 MB/);
          assert.equal(await input.getAttribute('aria-invalid'), 'true');
          assert.match(await page.locator('[data-thumbnail-current]').innerText(), /Version 1/);
        }
        assert.equal(uploads, 0, 'Neplatné soubory se neodesílají');
        page.off('request', track);
        await snap(page, 'owner-thumbnail-rejected', width);
        // A forged image passes the browser checks, then the server must explain the failure by this field.
        await Promise.all([page.waitForNavigation({waitUntil: 'networkidle'}), input.setInputFiles({name: 'fake.png', mimeType: 'image/png', buffer: Buffer.from('not a PNG')})]);
        await status.filter({hasText: 'Upload a JPG, PNG or WebP image.'}).waitFor();
        assert.equal(await input.getAttribute('aria-invalid'), 'true');
        assert.match(await page.locator('[data-thumbnail-current]').innerText(), /Version 1/);
        await snap(page, 'owner-thumbnail-server-error', width);
        await page.goto(`${detail}?tab=history`, {waitUntil: 'networkidle'});
        await history(page); await snap(page, 'owner-history', width);
        console.log(`P1-B14 texts ${width}: texty, History a odmítnutí náhledu OK`);
        await page.goto(`${detail}?tab=video`, {waitUntil: 'networkidle'});
        const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.getByRole('button', {name: 'Upload version', exact: true}).click()]);
        await chooser.setFiles(`${output}/final.mp4`);
        await page.locator('[data-video-versions] > li').filter({hasText: 'v1 '}).getByText('Ready', {exact: true}).waitFor({timeout: 180000});
        await page.locator('[data-video-final-toggle]').click();
        await page.locator('[data-video-final-toggle]').filter({hasText: 'Remove final mark'}).waitFor();
        await context.close();
        const client = await login('client', width);
        for (const tab of ['concept', 'script']) {
          await client.page.goto(`${detail}?tab=${tab}`, {waitUntil: 'networkidle'});
          await client.page.locator('#script-version').filter({hasText: 'Latest saved'}).waitFor();
          assert.equal(await client.page.locator('#script-edit').isVisible(), false);
          await snap(client.page, `client-${tab}`, width);
        }
        await client.page.goto(`${detail}?tab=caption`, {waitUntil: 'networkidle'});
        assert.equal(await client.page.locator('[data-thumbnail-input]').count(), 0);
        assert.equal(await client.page.locator('[data-caption-counter]').getAttribute('readonly'), '');
        await client.page.goto(`${detail}?tab=history`, {waitUntil: 'networkidle'});
        await history(client.page); await snap(client.page, 'client-history', width);
        await client.context.close();
      } else {
        await page.goto(`${detail}?tab=publish`, {waitUntil: 'networkidle'});
        await downloads(page, width); await snap(page, 'owner-before-publish', width);
        await context.close();
        const actorRole = width === 1440 ? 'owner' : 'client';
        const actor = await login(actorRole, width);
        await actor.page.goto(`${detail}?tab=publish`, {waitUntil: 'networkidle'});
        actor.page.once('dialog', dialog => dialog.accept());
        await actor.page.getByRole('button', {name: 'Mark as published', exact: true}).click();
        await actor.page.getByText('Published manually', {exact: true}).waitFor();
        await actor.context.close();
        for (const role of ['owner', 'client']) {
          const reader = await login(role, width);
          await reader.page.goto(`${detail}?tab=publish`, {waitUntil: 'networkidle'});
          await reader.page.getByText('Published manually', {exact: true}).waitFor();
          assert.equal(await reader.page.getByRole('button', {name: 'Mark as published', exact: true}).count(), 0);
          await downloads(reader.page, width); await snap(reader.page, `${role}-published`, width);
          await reader.context.close();
        }
      }
    }
    assert.deepEqual(errors, [], 'Konzole Chromium');
    assert.deepEqual(external, [], 'Žádné vnější požadavky');
    console.log(`OK P1-B14 ${process.argv[2]}: owner + klient, 1440/390, skutečné soubory, bez chyb konzole.`);
  } finally {
    await browser.close(); for (const socket of sockets) socket.destroy();
    await new Promise(resolve => bridge.close(resolve));
  }
})().catch(error => { console.error(error); process.exit(1); });
