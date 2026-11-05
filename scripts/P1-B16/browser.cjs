// P1-B16: spouštět jen přes check-browser.sh (Docker, 3 GB, nejvýše 600 s).
const { chromium } = require('/opt/jrc-tools/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const { execFileSync } = require('node:child_process');

(async () => {
  const mode = process.argv[2];
  assert(['upload', 'verify'].includes(mode));
  const origin = 'http://127.0.0.1:13583';
  const root = '/workspace/ops', output = `${root}/test-output/P1-B16`;
  const seed = JSON.parse(await fs.readFile(`${output}/session.json`, 'utf8'));
  const browser = await chromium.launch({ headless: true, args: ['--js-flags=--max-old-space-size=512'] });
  const deadline = setTimeout(() => { browser.close().finally(() => process.exit(124)); }, 480000);
  const errors = [], external = [];
  async function session(role, width) {
    const context = await browser.newContext({viewport: {width, height: width === 390 ? 844 : 1000}, timezoneId: 'Europe/Prague', isMobile: width === 390, hasTouch: width === 390});
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
    await page.screenshot({path: `${root}/screenshoty/P1-B16/${name}-${width}.png`, fullPage: false});
  }
  async function player(page) {
    const frame = page.frameLocator('iframe[data-video-frame]:visible');
    const video = frame.locator('video').first();
    await video.waitFor({timeout: 60000});
    await assertEventually(async () => (await video.evaluate(element => element.duration)) > 1, 30000);
    await video.evaluate(async element => {
      element.muted = true;
      await Promise.race([element.play(), new Promise((_, reject) => setTimeout(() => reject(new Error('Přehrávání nezačalo do 10 s')), 10000))]);
    });
    await assertEventually(async () => (await video.evaluate(element => element.currentTime)) > .1, 30000);
    await video.evaluate(element => element.pause());
    return frame;
  }
  async function assertEventually(test, ms) {
    const end = Date.now() + ms;
    while (!await test()) { assert(Date.now() < end, 'Čekání vypršelo'); await new Promise(r => setTimeout(r, 250)); }
  }
  try {
    if (mode === 'upload') {
      execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=25:duration=2', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2', '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p', '-c:a', 'aac', `${output}/garden.mp4`], {timeout: 20000, maxBuffer: 65536});
      const {context, page} = await session('owner', 1440);
      assert.equal((await page.goto(origin + seed.urls.detail + '?tab=video')).status(), 200);
      const count = await page.locator('[data-version][data-status]').count();
      const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.getByRole('button', {name: 'Upload version', exact: true}).click()]);
      await chooser.setFiles(`${output}/garden.mp4`);
      await page.locator('[data-version][data-status="ready"]').nth(count).waitFor({timeout: 120000});
      await page.reload();
      await player(page);
      await page.locator('[data-video-final-toggle]:visible').click();
      await page.locator('[data-video-final-badge]:visible').waitFor();
      await snap(page, 'owner-video-before-restart', 1440);
      await context.close();
      console.log('OK: skutečné nahrání, převod, přehrávání a finální verze; worker má použité spojení.');
    } else {
      for (const width of [1440, 390]) {
        for (const role of ['owner', 'client']) {
          const {context, page} = await session(role, width);
          await page.goto(origin + seed.urls.detail + '?tab=video');
          await player(page);
          await snap(page, `${role}-video-after-restart`, width);
          await page.goto(origin + seed.urls.detail + '?tab=publish');
          const link = page.getByRole('link', {name: 'Download thumbnail', exact: true});
          await link.waitFor();
          const response = await context.request.get(origin + await link.getAttribute('href'));
          assert.equal(response.status(), 200);
          assert.match(response.headers()['content-disposition'], /^attachment; filename="autumn-garden-.+\.png"$/);
          assert.equal(response.headers()['content-type'], 'image/png');
          assert.equal(response.headers()['cache-control'], 'private, no-store');
          assert.deepEqual(await response.body(), Buffer.from(seed.image, 'base64'));
          const [download] = await Promise.all([page.waitForEvent('download'), link.click()]);
          assert.equal(await download.failure(), null);
          assert.match(download.suggestedFilename(), /^autumn-garden-.+\.png$/);
          assert.deepEqual(await fs.readFile(await download.path()), Buffer.from(seed.image, 'base64'));
          await snap(page, `${role}-publish`, width);
          console.log(`OK ${role}/${width}: přehrávání po restartu a skutečné stažení náhledu s názvem, shodnými bajty a oprávněním.`);
          await page.goto(origin + seed.urls.profile);
          if (role === 'owner') {
            await page.getByLabel('Workspace colour (HEX)').fill('#5B21B6');
            await page.getByRole('button', {name: 'Save colour', exact: true}).click();
            await page.getByText('Workspace colour saved.', {exact: true}).waitFor();
            const input = page.locator('input[type="file"][name="icon"]');
            await input.setInputFiles({name: 'garden.png', mimeType: 'image/png', buffer: Buffer.from(seed.image, 'base64')});
            await page.getByRole('button', {name: 'Upload icon', exact: true}).click();
            await page.getByText('Channel icon saved.', {exact: true}).waitFor();
            await page.getByRole('button', {name: 'Use default icon', exact: true}).waitFor();
          } else {
            assert.equal(await page.getByRole('button', {name: 'Save colour', exact: true}).count(), 0);
            assert.equal(await page.getByRole('button', {name: 'Upload icon', exact: true}).count(), 0);
          }
          await page.getByText('Current colour: #5B21B6', {exact: false}).waitFor();
          await snap(page, `${role}-profile`, width);
          if (role === 'owner') {
            await page.goto(origin + seed.urls.members);
            const row = page.locator('[id^="invite-"]').filter({hasText: 'invite-p1b16@example.invalid'});
            await row.waitFor();
            const text = (await row.innerText()).replace(/\u00a0/g, ' ');
            assert.match(text, /Expires in (?:6 days|1 week)/);
            assert(!text.includes('0 minutes'));
            const info = row.locator('.text-xs').first();
            assert((await info.boundingBox()).width > 180, 'Platnost pozvánky musí být čitelná i na telefonu');
            for (const action of ['Copy link', 'Resend', 'Revoke']) {
              const box = await row.getByRole('button', {name: action, exact: true}).boundingBox();
              assert(box && box.x >= 0 && box.x + box.width <= width, `Viditelné tlačítko ${action}`);
            }
            await row.scrollIntoViewIfNeeded();
            await snap(page, 'owner-invitation', width);
            console.log(`OK ${width}: ovládání barvy a ikony, pozvánka ukazuje budoucí platnost.`);
          }
          await context.close();
        }
      }
      const anonymous = await browser.newContext();
      const response = await anonymous.request.get(origin + seed.invite);
      assert.equal(response.status(), 200);
      assert(!(await response.text()).includes('has expired'));
      await anonymous.close();
    }
    assert.deepEqual(external, [], 'Bez externích požadavků');
    assert.deepEqual(errors, [], 'Bez chyb JavaScriptu');
  } catch (error) {
    for (const context of browser.contexts()) for (const page of context.pages()) {
      await page.screenshot({path: `${root}/screenshoty/P1-B16/failure-${mode}.png`, timeout: 5000}).catch(() => {});
    }
    throw error;
  } finally { clearTimeout(deadline); await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
