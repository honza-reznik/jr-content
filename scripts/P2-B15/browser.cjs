// P2-B15: živý průchod „Use for another output…“ v Chromiu, desktop 1440×900 a mobil 390×844.
// Jen vlastní projekt jrc-p2b15 a vymyšlená data (fixture z seed.py); spouští run-browser.sh.
// Fáze: upload (owner nahraje v1 do YouTube · Video), reuse (dialog, kopie s komentáři i bez, role, History).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');

assert.equal(process.env.JRC_PROJECT, 'jrc-p2b15');
const phase = process.argv[2] || 'reuse';
const root = 'http://127.0.0.1:13915/jrc';
const output = path.resolve('ops/test-output/P2-B15');
const shots = path.resolve('ops/screenshoty/sprint-feedback/P2-B15');
fs.mkdirSync(shots, { recursive: true });
const fixture = JSON.parse(fs.readFileSync(path.join(output, 'fixture.json'), 'utf8'));
const detail = tab => `${root}/workspace/${fixture.workspace}/content/${fixture.post}/?tab=${tab}`;
const results = [];
const DESKTOP = { width: 1440, height: 900 };
const MOBILE = { width: 390, height: 844 };

async function check(name, fn) {
  try {
    const metrics = await fn();
    results.push({ name, pass: true, ...(metrics || {}) });
  } catch (error) {
    results.push({ name, pass: false, error: String((error && error.stack) || error).slice(0, 900) });
  }
  console.log(JSON.stringify(results.at(-1)));
}

const overflow = page => page.evaluate(() => document.documentElement.scrollWidth - innerWidth);

async function login(browser, who, viewport) {
  const mobile = viewport.width < 600;
  const context = await browser.newContext({ viewport, isMobile: mobile, hasTouch: mobile });
  await context.route(/^https?:\/\/(?!127\.0\.0\.1:13915(?:\/|$))/, route => route.abort());
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  page.errors = [];
  page.on('pageerror', error => page.errors.push(String(error)));
  await page.goto(`${root}/accounts/login/`);
  await page.fill('input[name=login]', fixture[who].email);
  await page.fill('input[name=password]', fixture[who].password);
  await Promise.all([page.waitForURL(url => !url.pathname.includes('/accounts/login')), page.click('button[type=submit]')]);
  return { context, page };
}

async function openTab(page, label) {
  const tab = page.locator('[data-video-tab]').filter({ hasText: label }).first();
  if (await tab.count()) await tab.click();
  return page.locator('[data-video-channel]:visible').first();
}

async function frameOf(page) {
  const handle = await page.locator('iframe[data-video-frame]:visible').first().elementHandle();
  const frame = await handle.contentFrame();
  await frame.waitForFunction(() => document.querySelector('video')?.duration > 3, null, { timeout: 90000 });
  return frame;
}

// The Shorts output after the copy: its own ready v1 (same file) with the team's view of the copied comments.
async function shortsPanel(page, shot) {
  const active = page.locator('[data-video-channel]:visible').first();
  assert.match(await active.locator('h3').innerText(), /YouTube · Shorts/);
  assert.equal(await active.locator('[data-video-versions] [data-version]').first().getAttribute('data-status'), 'ready');
  const frame = await frameOf(page);
  await frame.getByText('Client: please shorten the intro', { exact: true }).first().waitFor({ timeout: 30000 });
  await frame.getByText('Done in the next cut', { exact: true }).first().waitFor({ timeout: 30000 });
  await frame.getByText('Team only: music licence check', { exact: true }).first().waitFor({ timeout: 30000 });
  await page.locator('iframe[data-video-frame]:visible').first().scrollIntoViewIfNeeded();
  await page.waitForTimeout(1000);
  await shot('05-shorts-with-comments');
  await active.locator('[data-video-versions]').scrollIntoViewIfNeeded();
  await shot('05b-shorts-version-row');
}

async function verifyShorts() {
  const browser = await chromium.launch({ headless: true });
  const { page } = await login(browser, 'owner', DESKTOP);
  const shot = (name, options = {}) => page.screenshot({ path: `${shots}/desktop-${name}.png`, ...options });
  await check('desktop: Shorts has its own ready v1 with the copied comments (team view)', async () => {
    await page.goto(detail('video'), { waitUntil: 'networkidle' });
    await openTab(page, 'YouTube · Shorts');
    await shortsPanel(page, shot);
    return { overflow: await overflow(page), errors: page.errors.length };
  });
  await browser.close();
}

async function upload() {
  const file = path.join(output, 'garden-tour.mp4');
  if (!fs.existsSync(file)) execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y',
    '-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=25:duration=8',
    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=8',
    '-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', file],
  { timeout: 60000 });
  const browser = await chromium.launch({ headless: true });
  const { page } = await login(browser, 'owner', DESKTOP);
  await check('upload v1 to YouTube · Video and wait for conversion', async () => {
    await page.goto(detail('video'), { waitUntil: 'networkidle' });
    const card = await openTab(page, 'YouTube · Video');
    await card.locator('[data-video-file]').setInputFiles(file);
    await card.locator('[data-version][data-status="ready"]').first().waitFor({ timeout: 240000 });
    return { rows: await card.locator('[data-version]').count() };
  });
  await browser.close();
}

async function reuse() {
  const browser = await chromium.launch({ headless: true });

  // Desktop owner: v1 of YouTube · Video → YouTube · Shorts, keeping the comments.
  {
    const { page } = await login(browser, 'owner', DESKTOP);
    const shot = (name, options = {}) => page.screenshot({ path: `${shots}/desktop-${name}.png`, ...options });
    let card;
    await check('desktop: version row offers "Use for another output…"', async () => {
      await page.goto(detail('video'), { waitUntil: 'networkidle' });
      card = await openTab(page, 'YouTube · Video');
      const button = card.locator('[data-video-reuse]').first();
      await button.waitFor();
      await frameOf(page);
      await button.scrollIntoViewIfNeeded();
      await button.hover();
      await shot('01-version-row');
      await button.focus();
      await shot('01b-version-row-focus', { clip: await card.locator('[data-video-versions]').boundingBox() });
      return { overflow: await overflow(page) };
    });
    await check('desktop: dialog lists the other outputs and asks "Keep comments?"', async () => {
      await card.locator('[data-video-reuse]').first().click();
      const dialog = page.locator('[data-video-reuse-dialog]');
      await dialog.waitFor({ state: 'visible' });
      const targets = (await dialog.locator('[data-reuse-targets] .reuse-option').allInnerTexts()).map(t => t.replace(/\s+/g, ' '));
      assert.equal(targets.length, 3, targets.join(' | '));
      assert.ok(targets.some(t => t.includes('YouTube · Shorts') && t.includes('No video yet')), targets.join(' | '));
      assert.ok(targets.some(t => t.includes('Instagram · Story')), targets.join(' | '));
      assert.match(await dialog.locator('legend').nth(1).innerText(), /Keep comments\?/);
      assert.match(await dialog.locator('[data-reuse-source]').innerText(), /^v1 · YouTube · Video/);
      await shot('02-dialog');
      const box = await dialog.boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= DESKTOP.width && box.height <= DESKTOP.height, JSON.stringify(box));
      await dialog.locator('[data-reuse-submit]').click();
      assert.equal(await dialog.locator('[data-reuse-error]').innerText(), 'Choose an output.');
      await dialog.locator('.reuse-option').filter({ hasText: 'YouTube · Shorts' }).click();
      await dialog.locator('[data-reuse-submit]').click();
      assert.equal(await dialog.locator('[data-reuse-error]').innerText(), 'Choose whether to keep the comments.');
      await shot('03-dialog-missing-answer');
      return { targets: targets.length };
    });
    await check('desktop: Yes → Shorts gets v1 with the comments, page switches to it', async () => {
      const dialog = page.locator('[data-video-reuse-dialog]');
      await dialog.locator('.reuse-option').filter({ hasText: 'Yes, copy the comments' }).click();
      await shot('04-dialog-filled');
      await dialog.locator('[data-reuse-submit]').click();
      await dialog.waitFor({ state: 'hidden' });
      const notice = await page.locator('[data-video-notice]').innerText();
      assert.match(notice, /^v1 is now v1 of YouTube · Shorts · Ella na YouTube\. 3 comments copied\./);
      const active = page.locator('[data-video-channel]:visible').first();
      assert.match(await active.locator('h3').innerText(), /YouTube · Shorts/);
      const copied = await active.locator('[data-video-copied]').innerText();
      assert.equal(copied, 'Same file as v1 · YouTube · Video · Ella na YouTube · comments kept');
      await shortsPanel(page, shot);
      return { notice, overflow: await overflow(page), errors: page.errors.length };
    });
    await check('desktop: History names both outputs', async () => {
      await page.goto(detail('history'), { waitUntil: 'networkidle' });
      const text = await page.locator('body').innerText();
      assert.ok(text.includes('Used video v1 · YouTube · Video · Ella na YouTube for YouTube · Shorts · Ella na YouTube as v1 · comments kept'), text.slice(0, 2000));
      const entry = page.getByText('Used video v1 · YouTube · Video', { exact: false }).first();
      await entry.scrollIntoViewIfNeeded();
      await shot('06-history');
      return { overflow: await overflow(page) };
    });
    assert.deepEqual(page.errors, []);
  }

  // Mobile owner: v1 → Instagram · Story (long channel name), without comments.
  {
    const { page } = await login(browser, 'owner', MOBILE);
    const shot = (name, options = {}) => page.screenshot({ path: `${shots}/mobile-${name}.png`, ...options });
    await check('mobile: dialog as a bottom sheet, long output name wraps', async () => {
      await page.goto(detail('video'), { waitUntil: 'networkidle' });
      const card = await openTab(page, 'YouTube · Video');
      const button = card.locator('[data-video-reuse]').first();
      await button.scrollIntoViewIfNeeded();
      await shot('01-version-row');
      await button.click();
      const dialog = page.locator('[data-video-reuse-dialog]');
      await dialog.waitFor({ state: 'visible' });
      const targets = await dialog.locator('[data-reuse-targets] .reuse-option').allInnerTexts();
      assert.ok(targets.some(t => /Shorts/.test(t) && /Has video up to v1/.test(t)), targets.join(' | '));
      await shot('02-dialog');
      const box = await dialog.boundingBox();
      assert.ok(box.width <= MOBILE.width + 1 && box.y + box.height <= MOBILE.height + 1, JSON.stringify(box));
      const spill = await dialog.evaluate(el => [...el.querySelectorAll('.reuse-option')]
        .filter(o => o.scrollWidth > o.clientWidth + 1).length);
      assert.equal(spill, 0);
      await dialog.locator('.reuse-option').filter({ hasText: 'Instagram · Story' }).click();
      await dialog.locator('.reuse-option').filter({ hasText: 'No, start without comments' }).click();
      await dialog.evaluate(el => { el.scrollTop = el.scrollHeight; });
      await shot('03-dialog-filled');
      await dialog.locator('[data-reuse-submit]').click();
      await dialog.waitFor({ state: 'hidden' });
      const notice = await page.locator('[data-video-notice]').innerText();
      assert.match(notice, /^v1 is now v1 of Instagram · Story/);
      assert.ok(!/comments copied/.test(notice), notice);
      const active = page.locator('[data-video-channel]:visible').first();
      assert.match(await active.locator('[data-video-copied]').innerText(), /^Same file as v1 · YouTube · Video · Ella na YouTube$/);
      await active.locator('[data-video-versions]').scrollIntoViewIfNeeded();
      await shot('04-story-row');
      return { notice, overflow: await overflow(page) };
    });
    await check('mobile: History', async () => {
      await page.goto(detail('history'), { waitUntil: 'networkidle' });
      const text = await page.locator('body').innerText();
      assert.ok(/Used video v1 · YouTube · Video · Ella na YouTube for Instagram · Story · .* as v1 · without comments/.test(text));
      await page.getByText('without comments', { exact: false }).first().scrollIntoViewIfNeeded();
      await shot('05-history');
      return { overflow: await overflow(page) };
    });
    assert.deepEqual(page.errors, []);
  }

  // Client and viewer: no action; the client sees only the public copied comments.
  for (const [who, viewport, device] of [['client', DESKTOP, 'desktop'], ['client', MOBILE, 'mobile'], ['viewer', DESKTOP, 'desktop']]) {
    const { page } = await login(browser, who, viewport);
    await check(`${device}: ${who} sees no "Use for another output…"`, async () => {
      await page.goto(detail('video'), { waitUntil: 'networkidle' });
      assert.equal(await page.locator('[data-video-reuse]').count(), 0);
      assert.equal(await page.locator('[data-video-reuse-dialog]').count(), 0);
      const card = await openTab(page, 'YouTube · Shorts');
      assert.match(await card.locator('[data-video-copied]').innerText(), /^Same file as v1/);
      if (who === 'client' && device === 'desktop') {
        const frame = await frameOf(page);
        await frame.getByText('Client: please shorten the intro', { exact: true }).first().waitFor({ timeout: 30000 });
        assert.equal(await frame.getByText('Team only: music licence check', { exact: true }).count(), 0);
        await page.waitForTimeout(800);
      }
      await page.screenshot({ path: `${shots}/${device}-${who}-shorts.png` });
      return { overflow: await overflow(page), errors: page.errors.length };
    });
  }

  await browser.close();
}

(async () => {
  if (phase === 'upload') await upload(); else if (phase === 'verify-shorts') await verifyShorts(); else await reuse();
  const failed = results.filter(r => !r.pass).length;
  console.log(`RESULT ${results.length - failed}/${results.length}`);
  process.exit(failed ? 1 : 0);
})().catch(error => { console.error(error); process.exit(1); });
