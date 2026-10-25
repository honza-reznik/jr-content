// Jeden společný průchod F2–F8 přes proxy. Chromium a FFmpeg běží jen v jrc-dev.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');

assert.equal(process.env.JRC_PROJECT, 'jrc-f10b02');
assert.equal(process.env.JRC_APP_PORT, '13433');
assert.equal(process.env.JRC_S3_PORT, '14153');
const origin = `http://127.0.0.1:${process.env.JRC_APP_PORT}`;
const fixture = JSON.parse(fs.readFileSync('ops/test-output/F10-B02/fixture.json', 'utf8'));
const shots = path.resolve('ops/screenshoty/F10-B02');
const output = path.resolve('ops/test-output/F10-B02');
const errors = [];
const external = [];
fs.mkdirSync(shots, { recursive: true });
fs.mkdirSync(output, { recursive: true });

function makeVideo(file, seconds, source = 'testsrc2') {
  execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y',
    '-f', 'lavfi', '-i', `${source}=size=640x360:rate=25:duration=${seconds}`,
    '-f', 'lavfi', '-i', `sine=frequency=440:sample_rate=48000:duration=${seconds}`,
    '-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-g', '25',
    '-b:v', '800k', '-c:a', 'aac', file]);
}

async function session(browser, who, width = 1440) {
  const context = await browser.newContext({
    viewport: { width, height: width === 390 ? 2200 : 1500 },
    timezoneId: 'Europe/Prague',
  });
  // Local requests remain native browser requests. Intercept only external HTTP(S)
  // URLs, so Playwright does not mediate the API → signed S3 redirect used by HLS.
  await context.route(/^https?:\/\/(?!127\.0\.0\.1:(?:13433|14153)(?:\/|$)).*/, route => {
    const url = route.request().url();
    external.push(url);
    return route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(`${who}: ${error.message}`));
  page.on('console', message => {
    if (message.type() === 'error') errors.push(`${who}: ${message.text()}`);
  });
  await page.goto(`${origin}/accounts/login/`);
  await page.locator('input[name="login"]').fill(fixture[who].email);
  await page.locator('input[name="password"]').fill(fixture[who].password);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL(url => !url.pathname.startsWith('/accounts/login'));
  return { context, page };
}

async function capture(page, name, width) {
  await page.evaluate(() => document.fonts.ready);
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name}: vodorovný posun při ${width}px`);
  await page.screenshot({ path: path.join(shots, `${name}-${width}.png`), fullPage: true });
}

async function move(page, title, stage, date) {
  await page.goto(`${origin}/board/`);
  const card = page.locator('.board-card').filter({ has: page.getByRole('heading', { name: title, exact: true }) });
  await card.locator('summary').click();
  await card.getByLabel('Move to', { exact: true }).selectOption(stage);
  if (date) await card.locator('input[type="datetime-local"]').fill(date);
  await card.getByRole('button', { name: 'Confirm move' }).click();
  await page.waitForLoadState('networkidle');
}

async function upload(page, file, number) {
  const [chooser] = await Promise.all([
    page.waitForEvent('filechooser'),
    page.getByRole('button', { name: 'Upload version', exact: true }).first().click(),
  ]);
  await chooser.setFiles(file);
  await page.getByText(/Upload finished/).first().waitFor({ timeout: 60000 });
  await page.locator(`[data-video-channel]:visible [data-version][data-status="ready"]`).nth(number - 1)
    .waitFor({ timeout: 240000 });
}

async function playerFrame(page, number) {
  const handle = await page.locator('iframe[data-video-frame]:visible').elementHandle();
  const frame = await handle.contentFrame();
  await frame.locator('video').first().waitFor({ state: 'visible', timeout: 60000 });
  await frame.waitForFunction(() => { const video = document.querySelector('video'); return video && video.duration > 1; }, null, { timeout: 60000 });
  if (number) {
    const selected = await page.locator('[data-video-channel]:visible [data-video-select] option:checked').innerText();
    assert(selected.startsWith(`v${number}`), `Přehrávač nezobrazuje v${number}: ${selected}`);
  }
  return frame;
}

async function saveScript(page, text) {
  await page.getByRole('button', { name: 'Edit text', exact: true }).click();
  const editor = page.getByRole('textbox', { name: 'Script text' });
  await page.waitForFunction(() => document.querySelector('#script-editor [contenteditable="true"]'));
  await editor.fill(text);
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.getByText('Script saved.').waitFor();
}

async function main() {
  const v1 = path.join(output, 'ella-v1-20s.mp4');
  const v2 = path.join(output, 'ella-v2-26s.mp4');
  makeVideo(v1, 20);
  makeVideo(v2, 26, 'testsrc');
  const browser = await chromium.launch({ headless: true });
  try {
    const admin = await session(browser, 'admin');
    await admin.page.goto(`${origin}/board/`);
    await admin.page.getByText('+ New content', { exact: true }).click();
    await admin.page.getByLabel('Content title', { exact: true }).fill('Reel pro Ellu — ranní zahrada');
    await admin.page.getByRole('button', { name: 'Add to Idea' }).click();
    await admin.page.waitForLoadState('networkidle');
    const card = admin.page.locator('.board-card').filter({ hasText: 'Reel pro Ellu — ranní zahrada' });
    const postId = await card.getAttribute('data-post');
    assert(postId, 'Nový obsah není na nástěnce');
    const detail = `${origin}/workspace/${fixture.workspace}/content/${postId}/`;
    assert.match(detail, /\/workspace\/[^/]+\/content\/[^/]+\/$/);
    const title = 'Reel pro Ellu — ranní zahrada';
    await capture(admin.page, '01-new-idea', 1440);

    await move(admin.page, title, 'script');
    await move(admin.page, title, 'shooting');
    await admin.page.goto(`${detail}?tab=script`);
    await saveScript(admin.page, 'SCENE ONE: Ella waters the roses at dawn.');
    await saveScript(admin.page, 'SCENE ONE: Ella waters the roses at dawn.\nSCENE TWO: a quiet ending.');
    await admin.page.locator('#script-compare:not([hidden])').waitFor();
    assert(await admin.page.locator('#script-diff ins').count() > 0, 'Porovnání scénářů nezvýraznilo novou verzi');
    await capture(admin.page, '02-script-versions', 1440);

    await admin.page.goto(`${detail}?tab=caption`);
    const caption = 'Roses at dawn. A slow morning in Ella’s garden. 🌱 #garden';
    const textarea = admin.page.locator('textarea[name="caption"]').first();
    await textarea.fill(caption);
    await admin.page.getByRole('button', { name: 'Save version', exact: true }).first().click();
    await admin.page.getByText('Caption saved as a new version.', { exact: true }).first().waitFor();
    await admin.page.goto(`${detail}?tab=caption`);
    await textarea.fill('x'.repeat(2201));
    await admin.page.waitForFunction(() => document.querySelector('[data-caption-count]')?.textContent === '2201');
    assert.equal(await admin.page.locator('[data-caption-counter]').first().getAttribute('data-limit'), '2200');
    await admin.page.locator('textarea[name="caption"]').first().fill(caption);
    await capture(admin.page, '03-caption-limit', 1440);

    await admin.page.goto(`${detail}?tab=video`);
    await upload(admin.page, v1, 1);
    await admin.page.goto(`${detail}?tab=approval`);
    await move(admin.page, title, 'review');
    await admin.page.goto(`${detail}?tab=approval`);
    await admin.page.getByRole('button', { name: 'Approve', exact: true }).click();
    await admin.page.getByText('Approved by the team. It now waits for the client').first().waitFor();
    await admin.page.goto(`${detail}?tab=history`);
    assert((await admin.page.locator('main').innerText()).includes('Review'));
    await capture(admin.page, '04-team-review', 1440);

    const ella = await session(browser, 'client');
    await ella.page.goto(`${detail}?tab=video`);
    await capture(ella.page, '05-ella-video', 1440);
    const mobile = await ella.context.newPage();
    await mobile.setViewportSize({ width: 390, height: 2200 });
    await mobile.goto(`${detail}?tab=video`);
    await capture(mobile, '05-ella-video', 390);
    const frame = await playerFrame(ella.page, 1);
    await frame.locator('video').first().evaluate(video => {
      video.muted = true;
      video.play().catch(() => {});
      video.currentTime = 17;
    });
    await frame.waitForFunction(() => {
      const video = document.querySelector('video');
      const label = document.querySelector('[data-testid="video-timecode"]')?.textContent?.trim();
      return video && video.readyState >= 2 && !video.seeking && video.currentTime >= 17 && /^0:1[78]$/.test(label || '');
    }, null, { timeout: 30000 });
    await frame.locator('video').first().evaluate(video => video.pause());
    assert.match((await frame.getByTestId('video-timecode').innerText()).trim(), /^0:1[78]$/);
    await frame.getByPlaceholder('Leave your comment...').fill('zkrátit');
    await frame.getByTitle('Send (Enter)').click();
    await frame.getByText('zkrátit', { exact: true }).first().waitFor({ timeout: 20000 });
    await capture(ella.page, '05-ella-video', 1440);
    await mobile.reload(); // Refresh the second device after the comment was posted on desktop.
    const mobileFrame = await playerFrame(mobile, 1);
    await mobileFrame.getByText('zkrátit', { exact: true }).first().waitFor({ timeout: 20000 });
    await capture(mobile, '05-ella-video', 390);
    await ella.page.goto(`${detail}?tab=approval`);
    await ella.page.getByLabel(/What should change/).fill('At 0:17, please shorten the ending.');
    await ella.page.getByRole('button', { name: 'Request changes', exact: true }).click();
    await ella.page.getByText('Changes requested. The team can see your comment.', { exact: true }).first().waitFor();
    await capture(ella.page, '06-ella-changes', 1440);
    await capture(mobile, '06-ella-mobile-review', 390);
    await ella.context.close();
    await mobile.close();

    await move(admin.page, title, 'editing');
    await admin.page.goto(`${detail}?tab=script`);
    await saveScript(admin.page, 'SCENE ONE: Ella waters the roses at dawn.\nSCENE TWO: a short, quiet ending.');
    await admin.page.goto(`${detail}?tab=caption`);
    await admin.page.locator('textarea[name="caption"]').first().fill(`${caption} New ending.`);
    await admin.page.getByRole('button', { name: 'Save version', exact: true }).first().click();
    await admin.page.getByText('Caption saved as a new version.', { exact: true }).first().waitFor();
    await admin.page.goto(`${detail}?tab=video`);
    await upload(admin.page, v2, 2);
    await admin.page.locator('[data-video-channel]:visible [data-video-final-toggle]').click();
    await admin.page.locator('[data-video-channel]:visible [data-video-final-badge]').waitFor({ state: 'visible' });
    // The pipeline owns this editorial transition. After Editing the platform
    // statuses are drafts, so move the content back to Review from the board.
    await move(admin.page, title, 'review');
    await admin.page.goto(`${detail}?tab=approval`);
    await admin.page.getByRole('button', { name: 'Approve', exact: true }).click();
    await admin.page.getByText('Approved by the team. It now waits for the client').first().waitFor();

    const ellaAgain = await session(browser, 'client');
    await ellaAgain.page.goto(`${origin}/board/`);
    await ellaAgain.page.locator('[data-waiting-for-you]').getByText(title).waitFor();
    await ellaAgain.page.goto(`${detail}?tab=approval`);
    await ellaAgain.page.getByRole('button', { name: 'Approve', exact: true }).click();
    await ellaAgain.page.getByText('Approved. Thank you.', { exact: true }).first().waitFor();
    await capture(ellaAgain.page, '07-ella-approved', 1440);
    const ellaMobile = await ellaAgain.context.newPage();
    await ellaMobile.setViewportSize({ width: 390, height: 2200 });
    await ellaMobile.goto(`${detail}?tab=approval`);
    assert.equal(await ellaMobile.locator('[data-approval-decision]').count(), 0);
    await capture(ellaMobile, '07-ella-approved', 390);
    await ellaMobile.close();

    await move(admin.page, title, 'ready', '2030-01-05T12:30');
    await admin.page.goto(`${detail}?tab=publish`);
    await admin.page.getByRole('heading', { name: 'Publish channels' }).waitFor();
    await admin.context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin });
    await admin.page.getByRole('button', { name: 'Copy caption' }).first().click();
    await admin.page.getByText('Caption copied.', { exact: true }).waitFor();
    assert.equal(await admin.page.evaluate(() => navigator.clipboard.readText()), `${caption} New ending.`);
    const download = admin.page.getByRole('link', { name: 'Download final video' });
    await download.waitFor();
    const [downloadedVideo] = await Promise.all([admin.page.waitForEvent('download'), download.click()]);
    const downloadedPath = await downloadedVideo.path();
    assert(downloadedPath, 'Stažení finálního videa nevytvořilo soubor');
    const downloadedBytes = await fs.promises.readFile(downloadedPath);
    assert(downloadedBytes.length > 12, 'Stažené finální video je prázdné');
    assert.equal(downloadedBytes.subarray(4, 8).toString('ascii'), 'ftyp', 'Stažený soubor není MP4');
    await capture(admin.page, '08-publish-ready', 1440);
    await admin.page.getByRole('button', { name: 'Mark as published' }).first().click();
    await admin.page.waitForLoadState('networkidle');
    assert((await admin.page.locator('main').innerText()).includes('Published manually'));
    await capture(admin.page, '09-published', 1440);
    await admin.page.goto(`${detail}?tab=history`);
    const history = await admin.page.locator('main').innerText();
    assert(history.includes('Changes Requested') && history.includes('Marked Instagram as published'), 'Historie schválení/publikace chybí');
    await ellaAgain.page.goto(`${origin}/notifications/`);
    assert((await ellaAgain.page.locator('main').innerText()).toLowerCase().includes('notification'), 'Ella nevidí upozornění');

    assert.deepEqual(external, [], `Prohlížeč poslal požadavky mimo localhost: ${external.slice(0, 5)}`);
    assert.deepEqual(errors.filter(text => /Content Security Policy|Refused to|CORS/i.test(text)), [], 'Chyby CSP/CORS');
    assert.deepEqual(errors, [], `Chyby konzole/prohlížeče: ${errors.slice(0, 8).join(' | ')}`);
    console.log('OK: klientka a Instagram, Idea → Script → Shooting → Editing → Review → Approved → Ready → Published; scénář v1/v2 a porovnání; IG limit; video v1/v2, komentář v 0:17, žádost o změny, Final; schválení, ruční publikace, historie a upozornění; 390px bez přetečení; jen localhost, bez chyb konzole/CSP.');
    await admin.context.close();
    await ellaAgain.context.close();
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); if (errors.length) console.error('Prohlížeč:', errors.slice(0, 10)); process.exit(1); });
