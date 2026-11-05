// P1-B17: pouze check-browser.sh (Docker, 3 GB, timeout 600 s).
const { chromium } = require('/opt/jrc-tools/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const { execFileSync } = require('node:child_process');

(async () => {
  const mode = process.argv[2];
  assert(['upload', 'verify'].includes(mode));
  const origin = 'http://127.0.0.1:13593';
  const output = '/workspace/ops/test-output/P1-B17';
  const shots = '/workspace/ops/screenshoty/P1-B17';
  const seed = JSON.parse(await fs.readFile(`${output}/session.json`, 'utf8'));
  const detail = origin + seed.urls.detail;
  const browser = await chromium.launch({headless: true, args: ['--js-flags=--max-old-space-size=512']});
  const deadline = setTimeout(() => { browser.close().finally(() => process.exit(124)); }, 480000);
  const errors = [], external = [];
  const run = Date.now();
  async function session(role, width) {
    const context = await browser.newContext({viewport: {width, height: width === 390 ? 844 : 1000}, isMobile: width === 390, hasTouch: width === 390});
    await context.addCookies([{name: 'sessionid', value: seed.sessions[role], url: origin}]);
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin === origin) return route.continue();
      external.push(new URL(route.request().url()).origin); return route.abort();
    });
    const page = await context.newPage();
    page.setDefaultTimeout(20000); page.setDefaultNavigationTimeout(30000);
    page.on('pageerror', error => errors.push(error.message));
    return {context, page};
  }
  async function snap(page, name, width) {
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Přetečení ${name}/${width}`);
    await page.screenshot({path: `${shots}/${name}-${width}.png`, fullPage: false});
  }
  async function player(page) {
    const frame = page.frameLocator('iframe[data-video-frame]:visible');
    const video = frame.locator('video').first();
    await video.waitFor({timeout: 60000});
    await eventually(async () => await video.evaluate(v => v.duration > 1), 30000);
    await video.evaluate(async v => {
      v.muted = true;
      await Promise.race([v.play(), new Promise((_, reject) => setTimeout(() => reject(new Error('Přehrávání nezačalo')), 10000))]);
    });
    await eventually(async () => await video.evaluate(v => v.currentTime > .1), 30000);
    await video.evaluate(v => v.pause());
    return frame;
  }
  async function eventually(test, ms) {
    const end = Date.now() + ms;
    while (!await test()) { assert(Date.now() < end, 'Čekání vypršelo'); await new Promise(r => setTimeout(r, 500)); }
  }
  try {
    if (mode === 'upload') {
      execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=25:duration=5', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=5', '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p', '-c:a', 'aac', `${output}/garden.mp4`], {timeout: 20000, maxBuffer: 65536});
      const {context, page} = await session('owner', 1440);
      assert.equal((await page.goto(detail + '?tab=video')).status(), 200);
      assert.equal(await page.locator('[data-version]').count(), 0, 'Scénář vyžaduje čistý obsah');
      for (let number = 1; number <= 2; number++) {
        const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.getByRole('button', {name: 'Upload version', exact: true}).click()]);
        await chooser.setFiles(`${output}/garden.mp4`);
        await page.getByText(/Upload finished/).first().waitFor({timeout: 60000});
        await page.locator('[data-version]').nth(number - 1).waitFor();
        console.log(`OK: nahrána v${number}, bez čekání na převod předchozí verze.`);
      }
      await page.locator('[data-version][data-status="ready"]').nth(1).waitFor({timeout: 180000});
      await page.reload();
      await player(page);
      await snap(page, 'two-versions-ready', 1440);
      await context.close();
    } else {
      const reset = await session('owner', 1440);
      await reset.page.goto(detail + '?tab=video');
      await reset.page.locator('[data-video-final-toggle]:visible').waitFor();
      if (await reset.page.locator('[data-video-final-badge]:visible').count()) {
        await reset.page.locator('[data-video-final-toggle]:visible').click();
        await reset.page.locator('[data-video-final-badge]').waitFor({state: 'hidden'});
      }
      await reset.context.close();
      for (const width of [1440, 390]) for (const role of ['owner', 'client']) {
        const {context, page} = await session(role, width);
        await page.goto(detail + '?tab=video');
        assert.equal(await page.locator('[data-version][data-status="ready"]').count(), 2);
        const frame = await player(page);
        await page.getByText(/To reply to a timecoded comment, open Comments inside the video player/).waitFor();
        await snap(page, `${role}-video`, width);
        if (role === 'owner' && width === 1440 && !await frame.getByText('P1-B17 timecoded garden', {exact: true}).count()) {
          await frame.locator('video').first().evaluate(v => {v.currentTime = 1;});
          await frame.getByPlaceholder('Leave your comment...').fill('P1-B17 timecoded garden');
          await frame.getByTitle('Send (Enter)').click();
          await frame.getByText('P1-B17 timecoded garden', {exact: true}).first().waitFor();
        }
        await frame.getByText('P1-B17 timecoded garden', {exact: true}).first().waitFor();
        await frame.getByRole('button', {name: 'Reply', exact: true}).first().click();
        const replyText = `P1-B17 ${role} reply ${width} ${run}`;
        await frame.getByPlaceholder('Leave your reply here...').fill(replyText);
        await frame.getByPlaceholder('Leave your reply here...').press('Enter');
        await frame.getByText(replyText, {exact: true}).first().waitFor();
        await frame.getByText(replyText, {exact: true}).first().scrollIntoViewIfNeeded();
        await snap(page, `${role}-reply`, width);
        await page.goto(detail + '?tab=publish');
        await page.getByText(/No final video selected/).waitFor();
        await page.getByText(/Thumbnail download requires/).waitFor();
        assert.equal(await page.getByRole('link', {name: 'Download thumbnail', exact: true}).count(), 0);
        const videoLink = page.getByRole('link', {name: 'Video', exact: true});
        assert.equal(await videoLink.getAttribute('href'), '?tab=video');
        await snap(page, `${role}-publish-no-final`, width);
        if (role === 'owner') {
          await page.goto(origin + seed.urls.profile);
          const input = page.locator('input[type="file"][name="icon"]');
          await input.setInputFiles({name: 'garden.png', mimeType: 'image/png', buffer: Buffer.from(seed.image, 'base64')});
          await page.getByText('garden.png selected. Click Upload icon to save it.', {exact: true}).waitFor();
          await page.locator('[data-icon-status]').scrollIntoViewIfNeeded();
          await snap(page, 'owner-icon-selected', width);
          await page.getByRole('button', {name: 'Upload icon', exact: true}).click();
          await page.getByText('Channel icon saved.', {exact: true}).waitFor();
          await page.getByRole('button', {name: 'Use default icon', exact: true}).waitFor();
          await page.getByRole('button', {name: 'Use default icon', exact: true}).scrollIntoViewIfNeeded();
          await snap(page, 'owner-icon-saved', width);
          const image = page.locator('img[data-channel-icon]').first();
          await image.waitFor();
          await eventually(async () => await image.evaluate(img => img.complete && img.naturalWidth > 0), 10000);
          await page.getByRole('button', {name: 'Use default icon', exact: true}).click();
          assert.equal(await page.getByRole('button', {name: 'Use default icon', exact: true}).count(), 0);
          await input.setInputFiles({name: 'bad.svg', mimeType: 'image/svg+xml', buffer: Buffer.from('<svg/>')});
          await page.getByText('Choose a PNG, JPG or WebP image up to 1 MB.', {exact: true}).waitFor();
          await page.locator('[data-icon-status]').scrollIntoViewIfNeeded();
          assert(await page.getByRole('button', {name: 'Upload icon', exact: true}).isDisabled());
          await snap(page, 'owner-icon-error', width);
        }
        await context.close();
        console.log(`OK ${role}/${width}: přehrávání, skutečná odpověď ve vloženém přehrávači, vysvětlení Publish${role === 'owner' ? ', odezva ikony a validace' : ''}.`);
      }
      // Po označení finální verze mohou oba stáhnout skutečný soubor i náhled.
      const owner = await session('owner', 1440);
      await owner.page.goto(detail + '?tab=video');
      await owner.page.locator('[data-video-final-toggle]:visible').click();
      await owner.page.locator('[data-video-final-badge]:visible').waitFor();
      await owner.context.close();
      for (const width of [1440, 390]) for (const role of ['owner', 'client']) {
        const {context, page} = await session(role, width);
        await page.goto(detail + '?tab=publish');
        for (const name of ['Download final video', 'Download thumbnail']) {
          const link = page.getByRole('link', {name, exact: true});
          await link.waitFor();
          const [download] = await Promise.all([page.waitForEvent('download'), link.click()]);
          assert.equal(await download.failure(), null);
          const bytes = await fs.readFile(await download.path());
          if (name === 'Download thumbnail') assert.deepEqual(bytes, Buffer.from(seed.image, 'base64'));
          else assert(bytes.length > 0);
        }
        await snap(page, `${role}-publish-ready`, width);
        await context.close();
        console.log(`OK ${role}/${width}: skutečné stažení videa a náhledu po označení Final.`);
      }
    }
    assert.deepEqual(errors, [], 'Chyby JavaScriptu');
    assert.deepEqual(external, [], 'Externí požadavky');
  } catch (error) {
    for (const context of browser.contexts()) for (const page of context.pages()) {
      await page.screenshot({path: `${shots}/failure-${mode}.png`, timeout: 5000}).catch(() => {});
    }
    throw error;
  } finally { clearTimeout(deadline); await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
