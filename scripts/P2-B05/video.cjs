// Chromium needs a UTF-8 locale to retain non-ASCII download names.
process.env.LANG = 'C.UTF-8';
process.env.LC_ALL = 'C.UTF-8';
// Only run inside Docker: timeout 600s node --max-old-space-size=2048 ...
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');

assert.equal(process.env.JRC_PROJECT, 'jrc-p2b05');
const origin = 'http://127.0.0.1:13905';
const prefix = '/jrc';
const output = path.resolve('ops/test-output/P2-B05');
const shots = path.resolve('ops/screenshoty/sprint-feedback/P2-B05');
const fixture = JSON.parse(fs.readFileSync(path.join(output, 'fixture.json'), 'utf8'));
const filename = 'Žlutý klip – original.MOV';
const file = path.join(output, filename);
if (!fs.existsSync(file)) execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y',
  '-f', 'lavfi', '-i', 'testsrc2=size=1920x1080:rate=25:duration=10',
  '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=10',
  '-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
  '-c:a', 'aac', file], { timeout: 60000 });
const source = fs.readFileSync(file);
const sha = buffer => crypto.createHash('sha256').update(buffer).digest('hex');
const tab = `${origin}${prefix}/workspace/${fixture.workspace}/content/${fixture.post}/?tab=video`;

async function login(browser, who, viewport) {
  const context = await browser.newContext({ viewport, acceptDownloads: true });
  await context.route(/^https?:\/\/(?!127\.0\.0\.1:13905(?:\/|$))/, route => route.abort());
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  await page.goto(`${origin}${prefix}/accounts/login/`);
  await page.locator('[name=login]').fill(fixture[who].email);
  await page.locator('[name=password]').fill(fixture[who].password);
  await Promise.all([page.waitForURL(url => !url.pathname.includes('/accounts/login')), page.locator('button[type=submit]').click()]);
  await page.goto(tab);
  return { page, context };
}

async function drop(page, files, action = 'drop') {
  await page.locator('[data-video-channel]:visible').first().evaluate((card, { files, action }) => {
    const transfer = new DataTransfer();
    for (const f of files) {
      const bytes = f.base64 ? Uint8Array.from(atob(f.base64), c => c.charCodeAt(0)) : new Uint8Array(f.bytes);
      transfer.items.add(new File([bytes], f.name, { type: f.type }));
    }
    card.dispatchEvent(new DragEvent(action, { bubbles: true, cancelable: true, dataTransfer: transfer }));
  }, { files, action });
}

async function readyFrame(page) {
  const handle = await page.locator('iframe[data-video-frame]:visible').first().elementHandle();
  const frame = await handle.contentFrame();
  await frame.locator('video').first().waitFor({ timeout: 90000 });
  await frame.waitForFunction(() => document.querySelector('video')?.videoHeight > 0, null, { timeout: 90000 });
  return frame;
}

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-dev-shm-usage'] });
  try {
    const { page, context } = await login(browser, 'editor', { width: 1440, height: 900 });
    const segments = [];
    page.on('request', r => { if (/\/stream\/hls\/.*(?:playlist\.m3u8|seg_\d+\.ts)/.test(r.url())) segments.push(new URL(r.url()).pathname); });
    if (!await page.locator('[data-video-channel]:visible [data-status=ready]').count()) {
    await page.locator('[data-video-drop]').first().scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(shots, 'desktop-empty.png') });
    await drop(page, [{ name: 'bad.exe', type: 'application/octet-stream', bytes: [1] }], 'dragenter');
    assert.equal(await page.locator('[data-video-drop]').first().getAttribute('data-dragging'), '1');
    await page.screenshot({ path: path.join(shots, 'desktop-drag.png') });
    await drop(page, [{ name: 'bad.exe', type: 'application/octet-stream', bytes: [1] }]);
    await page.getByRole('alert').filter({ hasText: 'Unsupported file type' }).waitFor();
    await page.screenshot({ path: path.join(shots, 'desktop-invalid.png') });
    await drop(page, [{ name: 'empty.mp4', type: 'video/mp4', bytes: [] }]);
    await page.getByRole('alert').filter({ hasText: 'The file is empty' }).waitFor();
    await drop(page, [1, 2].map(n => ({ name: `clip${n}.mp4`, type: 'video/mp4', bytes: [1] })));
    await page.getByRole('alert').filter({ hasText: 'Drop one video at a time' }).waitFor();
    // DataTransfer uses the real local synthetic MOV, not mocked upload replies.
    await drop(page, [{ name: filename, type: 'video/quicktime', base64: source.toString('base64') }]);
    await page.getByText(/Upload finished/).first().waitFor({ timeout: 90000 });
    await page.locator('[data-video-channel]:visible [data-status=ready]').first().waitFor({ timeout: 180000 });
    }
    const frame = await readyFrame(page);
    assert.equal(await frame.locator('video').first().evaluate(v => v.videoHeight), 720);
    assert(segments.some(s => /\/1\/playlist\.m3u8/.test(s)), JSON.stringify(segments));
    assert(!segments.some(s => /\/0\/(?:playlist|seg_)/.test(s)), '1080p loaded without a manual choice');
    await page.locator('iframe[data-video-frame]:visible').first().scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(shots, 'desktop-ready-720.png') });
    await frame.getByRole('button', { name: 'Team and client', exact: true }).click();
    await frame.getByRole('button', { name: 'Team only', exact: true }).click();
    await page.screenshot({ path: path.join(shots, 'desktop-team-only.png') });
    const quality = frame.getByLabel('Quality', { exact: true });
    await quality.selectOption({ label: '1080p' });
    await frame.getByRole('button', { name: 'Play', exact: true }).first().click();
    await frame.waitForFunction(() => document.querySelector('video')?.videoHeight === 1080, null, { timeout: 30000 });
    assert(segments.some(s => /\/0\/playlist\.m3u8/.test(s)));
    const [download] = await Promise.all([page.waitForEvent('download'),
      page.getByRole('link', { name: 'Download version', exact: true }).click()]);
    const downloaded = fs.readFileSync(await download.path());
    const check = await context.request.get(download.url());
    fs.writeFileSync(path.join(output, 'download-headers.json'), JSON.stringify({
      url: check.url(), status: check.status(), headers: { disposition: check.headers()["content-disposition"], type: check.headers()["content-type"] }, suggested: download.suggestedFilename(),
      bytes: downloaded.length, sha256: sha(downloaded),
    }, null, 2));
    assert.equal(download.suggestedFilename(), filename);
    assert.equal(sha(downloaded), sha(source));
    fs.writeFileSync(path.join(output, 'browser-results.json'), JSON.stringify({ filename, bytes: source.length, sha256: sha(source), segments }, null, 2));
    await context.close();

    const mobile = await login(browser, 'editor', { width: 390, height: 844 });
    const mobileFrame = await readyFrame(mobile.page);
    assert.equal(await mobileFrame.locator('video').first().evaluate(v => v.videoHeight), 720);
    await mobileFrame.getByRole('button', { name: 'Play', exact: true }).first().click();
    await mobileFrame.waitForFunction(() => document.querySelector('video').currentTime > 0.3, null, { timeout: 30000 });
    await mobileFrame.getByRole('button', { name: 'Pause', exact: true }).first().click();
    assert(await mobile.page.getByRole('button', { name: 'Upload version' }).first().isVisible());
    assert(await mobile.page.getByRole('link', { name: 'Download version', exact: true }).isVisible());
    assert(await mobile.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert(await mobileFrame.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await mobile.page.locator('iframe[data-video-frame]:visible').first().scrollIntoViewIfNeeded();
    await mobile.page.screenshot({ path: path.join(shots, 'mobile-ready.png') });
    await mobile.context.close();
    console.log('OK: empty/error/highlight, real drag upload, 720p network and picture, manual 1080p, original MOV filename and SHA256, mobile 390×844');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
