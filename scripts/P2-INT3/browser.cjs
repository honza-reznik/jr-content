// P2-INT3: živé ověření integrace main + P2-B14 + P2-B15 + P2-B16 v Chromiu, desktop 1440×900 a mobil 390×844.
// Jen vlastní projekt jrc-int3 a vymyšlená data (fixture ze seed.py); spouští run-browser.sh.
// Fáze: upload (owner nahraje v1 do YouTube · Video), check (duplikát videa, popisky, shotlist, script, nástěnka,
// hledání, admin přehled, role klient a viewer).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');

assert.equal(process.env.JRC_PROJECT, 'jrc-int3');
const phase = process.argv[2] || 'check';
const root = 'http://127.0.0.1:13930/jrc';
const output = path.resolve('ops/test-output/P2-INT3');
const shots = path.resolve('ops/screenshoty/P2-INT3');
fs.mkdirSync(shots, { recursive: true });
const fixture = JSON.parse(fs.readFileSync(path.join(output, 'fixture.json'), 'utf8'));
const detail = (tab, extra = '') => `${root}/workspace/${fixture.workspace}/content/${fixture.post}/?tab=${tab}${extra}`;
const results = [];
const DESKTOP = { width: 1440, height: 900 };
const MOBILE = { width: 390, height: 844 };
const run = Date.now().toString(36);

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
  const context = await browser.newContext({ viewport, isMobile: mobile, hasTouch: mobile, locale: 'en-US' });
  await context.route(/^https?:\/\/(?!127\.0\.0\.1:(13930|14630)(?:\/|$))/, route => route.abort());
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

async function openVideoOutput(page, label) {
  const tab = page.locator('[data-video-tab]').filter({ hasText: label }).first();
  if (await tab.count()) await tab.click();
  return page.locator('[data-video-channel]:visible').first();
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
    const card = await openVideoOutput(page, 'YouTube · Video');
    await card.locator('[data-video-file]').setInputFiles(file);
    await card.locator('[data-video-versions] [data-version][data-status="ready"]').first().waitFor({ timeout: 240000 });
    return { rows: await card.locator('[data-video-versions] [data-version]').count(), errors: page.errors.length };
  });
  await browser.close();
}

async function addComment(panel, text, visibility) {
  const form = panel.locator('form[data-comment-form]').first();
  await form.locator('textarea[name=body]').fill(text);
  if (visibility) await form.locator('select[name=visibility]').selectOption(visibility);
  await form.locator('button[type=submit]').click();
}

async function desktopOwner(browser) {
  const { page } = await login(browser, 'owner', DESKTOP);
  const shot = (name, options = {}) => page.screenshot({ path: `${shots}/desktop-${name}.png`, ...options });

  await check('desktop: Video accordion row offers "Use for another output…" and the dialog lists the other outputs', async () => {
    await page.goto(detail('video'), { waitUntil: 'networkidle' });
    const card = await openVideoOutput(page, 'YouTube · Video');
    const details = card.locator('[data-video-versions] details').first();
    if (!(await details.evaluate(el => el.open))) await details.locator('summary').click();
    const button = card.locator('[data-video-reuse]').first();
    await button.waitFor();
    await button.scrollIntoViewIfNeeded();
    await shot('01-video-row');
    await button.click();
    const dialog = page.locator('[data-video-reuse-dialog]');
    await dialog.waitFor({ state: 'visible' });
    const targets = (await dialog.locator('[data-reuse-targets] .reuse-option').allInnerTexts()).map(t => t.replace(/\s+/g, ' '));
    assert.equal(targets.length, 3, targets.join(' | '));
    assert.ok(targets.some(t => t.includes('YouTube · Shorts') && t.includes('No video yet')), targets.join(' | '));
    await shot('02-reuse-dialog');
    await dialog.locator('.reuse-option').filter({ hasText: 'YouTube · Shorts' }).click();
    await dialog.locator('.reuse-option').filter({ hasText: 'Yes, copy the comments' }).click();
    await dialog.locator('[data-reuse-submit]').click();
    await dialog.waitFor({ state: 'hidden', timeout: 60000 });
    const active = page.locator('[data-video-channel]:visible').first();
    await active.locator('[data-video-versions] [data-version][data-status="ready"]').first().waitFor({ timeout: 60000 });
    assert.match(await active.locator('h3').first().innerText(), /YouTube · Shorts/);
    assert.match(await active.locator('[data-video-copied]').first().innerText(), /Same file as v1 · YouTube · Video/);
    await active.scrollIntoViewIfNeeded();
    await shot('03-shorts-same-file');
    return { overflow: await overflow(page), errors: page.errors.length };
  });

  await check('desktop: Caption tab keeps outputs apart and comments per caption version', async () => {
    await page.goto(detail('caption'), { waitUntil: 'networkidle' });
    const cards = page.locator('[data-caption-channel]');
    assert.equal(await cards.count(), 4);
    const shorts = cards.filter({ hasText: 'YouTube · Shorts' }).first();
    const panel = shorts.locator('[data-version-comments]');
    await panel.getByText('Add a question to the caption?').waitFor();
    assert.match(await panel.innerText(), /V1 · Read version/);
    const text = `Caption note ${run}`;
    await addComment(panel, text, 'external');
    await shorts.locator('[data-version-comments]').getByText(text).waitFor();
    // The other output's caption thread stays empty.
    const video = cards.filter({ hasText: 'YouTube · Video' }).first();
    assert.equal(await video.locator('[data-version-comments]').getByText(text).count(), 0);
    await shorts.scrollIntoViewIfNeeded();
    await shot('04-caption-comments');
    return { overflow: await overflow(page), errors: page.errors.length };
  });

  await check('desktop: Shotlist sub-tabs per format with their own comment threads', async () => {
    await page.goto(detail('shotlist'), { waitUntil: 'networkidle' });
    const tabs = await page.locator('[data-shotlist-format]').allInnerTexts();
    assert.deepEqual(tabs.map(t => t.trim()), ['Long form', 'Short form', 'Story']);
    assert.ok(await page.getByText('Wide shot of the gate').count());
    assert.equal(await page.locator('[data-version-comments]').getByText('Shoot the hook first.').count(), 0);
    await page.locator('[data-shotlist-format="short_form"]').click();
    await page.waitForURL(/format=short_form/);
    await page.waitForLoadState('networkidle');
    const panel = page.locator('[data-version-comments]');
    await panel.getByText('Shoot the hook first.').waitFor();
    assert.ok(await page.getByText('Vertical hook: hands in soil').count());
    const text = `Shotlist note ${run}`;
    await addComment(panel, text);
    await page.locator('[data-version-comments]').getByText(text).waitFor();
    await shot('05-shotlist-short-form', { fullPage: true });
    return { overflow: await overflow(page), errors: page.errors.length };
  });

  await check('desktop: Script comments thread spans versions; badge loads the old text', async () => {
    await page.goto(detail('script'), { waitUntil: 'networkidle' });
    const panel = page.locator('[data-version-comments]').first();
    await panel.getByText('Please confirm the intro location.').waitFor();
    await panel.getByText('Team only: is the pond outro too long?').waitFor();
    const badge = panel.locator('details[data-comment-version-url]').filter({ hasText: 'V1' }).first();
    await badge.locator('summary').click();
    await badge.locator('pre').filter({ hasText: 'Intro in the greenhouse' }).waitFor();
    await panel.scrollIntoViewIfNeeded();
    await shot('06-script-comments');
    return { overflow: await overflow(page), errors: page.errors.length };
  });

  await check('desktop: Discussion does not list comments on versions', async () => {
    await page.goto(detail('discussion'), { waitUntil: 'networkidle' });
    const main = page.locator('main');
    assert.equal(await main.getByText('Shoot the hook first.').count(), 0);
    assert.equal(await main.getByText('Please confirm the intro location.').count(), 0);
    return { errors: page.errors.length };
  });

  await check('desktop: board shows the current client only, channels once with formats, comment labels', async () => {
    await page.goto(`${root}/board/`, { waitUntil: 'networkidle' });
    const card = page.locator('.board-card, [data-board-card], article').filter({ hasText: 'Garden tour in November' }).first();
    await card.waitFor();
    assert.equal(await page.getByText('Bruno sourdough reel').count(), 0);
    const channels = (await card.locator('.board-channel').allInnerTexts()).map(t => t.replace(/\s+/g, ' ').trim());
    assert.equal(channels.length, 2, channels.join(' | '));
    assert.ok(channels.some(t => t.includes('Video, Shorts')), channels.join(' | '));
    assert.ok(channels.some(t => t.includes('Reel, Story')), channels.join(' | '));
    const comments = await card.locator('.board-comments').innerText();
    assert.match(comments, /Shotlist · V1/);
    await card.scrollIntoViewIfNeeded();
    await shot('07-board');
    await shot('07b-board-card', { clip: await card.boundingBox() });
    return { overflow: await overflow(page), errors: page.errors.length };
  });

  await check('desktop: board search finds a comment on a version (current client only)', async () => {
    await page.goto(`${root}/board/?q=${encodeURIComponent('hook first')}`, { waitUntil: 'networkidle' });
    assert.ok(await page.getByText('Garden tour in November').count());
    await page.goto(`${root}/board/?q=${encodeURIComponent('sourdough')}`, { waitUntil: 'networkidle' });
    assert.equal(await page.getByText('Bruno sourdough reel').count(), 0);
    return { errors: page.errors.length };
  });

  await check('desktop: admin overview board and summary know outputs and version comments', async () => {
    await page.goto(`${root}/overview/`, { waitUntil: 'networkidle' });
    assert.ok(await page.getByText('Bruno sourdough reel').count());
    const card = page.locator('.ov-card').filter({ hasText: 'Garden tour in November' }).first();
    const channels = (await card.locator('.ov-channel').allInnerTexts()).map(t => t.replace(/\s+/g, ' ').trim());
    assert.equal(channels.length, 2, channels.join(' | '));
    assert.ok(channels.some(t => t.includes('Video, Shorts')), channels.join(' | '));
    await shot('08-overview-board');
    await card.locator('h3 a').click();
    await page.waitForLoadState('networkidle');
    const summary = await page.locator('main').innerText();
    assert.match(summary, /YouTube · Shorts · Ella na YouTube · v1/);
    assert.match(summary, /Shotlist · V1/);
    assert.match(summary, /Short form/);
    await shot('09-overview-summary', { fullPage: true });
    return { overflow: await overflow(page), errors: page.errors.length };
  });
}

async function mobileOwner(browser) {
  const { page } = await login(browser, 'owner', MOBILE);
  const shot = (name, options = {}) => page.screenshot({ path: `${shots}/mobile-${name}.png`, ...options });
  for (const [tab, name, extra] of [['video', '01-video', ''], ['caption', '02-caption', ''], ['shotlist', '03-shotlist', '&format=short_form'], ['script', '04-script', '']]) {
    await check(`mobile: ${tab} tab has no horizontal overflow`, async () => {
      await page.goto(detail(tab, extra), { waitUntil: 'networkidle' });
      if (tab === 'video') {
        const card = await openVideoOutput(page, 'YouTube · Shorts');
        await card.locator('[data-video-versions]').scrollIntoViewIfNeeded();
      } else {
        await page.locator('[data-version-comments]').first().scrollIntoViewIfNeeded();
      }
      await page.waitForTimeout(500);
      await shot(name);
      const spill = await overflow(page);
      assert.ok(spill <= 1, `overflow ${spill}`);
      return { overflow: spill, errors: page.errors.length };
    });
  }
  await check('mobile: board card', async () => {
    await page.goto(`${root}/board/`, { waitUntil: 'networkidle' });
    const card = page.locator('.board-card, [data-board-card], article').filter({ hasText: 'Garden tour in November' }).first();
    await card.scrollIntoViewIfNeeded();
    await shot('05-board');
    const spill = await overflow(page);
    assert.ok(spill <= 1, `overflow ${spill}`);
    return { overflow: spill, errors: page.errors.length };
  });
}

async function clientAndViewer(browser) {
  for (const [who, viewport, device] of [['client', MOBILE, 'mobile'], ['viewer', DESKTOP, 'desktop']]) {
    const { page, context } = await login(browser, who, viewport);
    await check(`${device}: ${who} sees only "Team and client" version comments${who === 'viewer' ? ' and cannot write' : ''}`, async () => {
      await page.goto(detail('script'), { waitUntil: 'networkidle' });
      const panel = page.locator('[data-version-comments]').first();
      await panel.getByText('Please confirm the intro location.').waitFor();
      assert.equal(await panel.getByText('Team only: is the pond outro too long?').count(), 0);
      assert.equal(await panel.locator('form[data-comment-form]').count() > 0, who === 'client');
      await page.goto(detail('shotlist', '&format=short_form'), { waitUntil: 'networkidle' });
      const list = page.locator('[data-version-comments]').first();
      await list.getByText('Shoot the hook first.').waitFor();
      assert.equal(await list.getByText(`Shotlist note ${run}`).count(), 0);
      await page.goto(detail('video'), { waitUntil: 'networkidle' });
      assert.equal(await page.locator('[data-video-reuse]:visible').count(), 0);
      await page.screenshot({ path: `${shots}/${device}-${who}-video.png` });
      const spill = await overflow(page);
      assert.ok(spill <= 1, `overflow ${spill}`);
      return { overflow: spill, errors: page.errors.length };
    });
    await context.close();
  }
}

// After a style change: the board card again, desktop and mobile (the check phase changes data and runs once).
async function boardOnly(browser) {
  for (const [viewport, device] of [[DESKTOP, 'desktop'], [MOBILE, 'mobile']]) {
    const { page, context } = await login(browser, 'owner', viewport);
    await check(`${device}: board card channel chips are readable`, async () => {
      await page.goto(`${root}/board/`, { waitUntil: 'networkidle' });
      const card = page.locator('article').filter({ hasText: 'Garden tour in November' }).first();
      await card.scrollIntoViewIfNeeded();
      const colours = await card.locator('.board-channel').evaluateAll(chips => chips.map(chip => [
        getComputedStyle(chip).color, getComputedStyle(chip.querySelector('.board-formats')).color]));
      for (const [chip, formats] of colours) assert.equal(formats, chip);
      await page.screenshot({ path: `${shots}/${device}-10-board-card-after-fix.png`, clip: await card.boundingBox() });
      return { overflow: await overflow(page), errors: page.errors.length };
    });
    await context.close();
  }
}

(async () => {
  if (phase === 'upload') await upload();
  else if (phase === 'board') {
    const browser = await chromium.launch({ headless: true });
    await boardOnly(browser);
    await browser.close();
  }
  else {
    const browser = await chromium.launch({ headless: true });
    await desktopOwner(browser);
    await mobileOwner(browser);
    await clientAndViewer(browser);
    await browser.close();
  }
  fs.writeFileSync(path.join(output, `results-${phase}.json`), JSON.stringify(results, null, 2));
  const failed = results.filter(r => !r.pass || r.errors);
  console.log(`${results.length - failed.length}/${results.length} ok`);
  process.exit(failed.length ? 1 : 0);
})();
