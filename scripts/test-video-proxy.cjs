// Živé ověření F2-B04; pouze Docker a vlastní postradatelný projekt.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
assert.equal(process.env.JRC_PROJECT, 'jrc-f2b04');
const origin = `http://127.0.0.1:${process.env.JRC_APP_PORT || 13000}`;
const api = `${origin}/video-api`;
const s3 = `http://127.0.0.1:${process.env.JRC_S3_PORT || 13900}`;
const output = path.resolve('ops/test-output/F2-B04');
const shots = path.resolve('ops/screenshoty/F2-B04');
fs.mkdirSync(output, { recursive: true });
fs.mkdirSync(shots, { recursive: true });
let token;
async function request(method, route, data) {
  const response = await fetch(api + route, { method, headers: {
    'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }, body: data === undefined ? undefined : JSON.stringify(data) });
  assert(response.ok, `${method} ${route}: ${response.status}`);
  return response.json();
}
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function fixture() {
  token = (await request('POST', '/auth/login', {
    email: process.env.JRC_FF_SYNC_EMAIL, password: process.env.JRC_FF_SYNC_PASSWORD,
  })).access_token;
  const project = await request('POST', '/projects', { name: 'F2-B04 — local video' });
  const video = path.join(output, 'local-video.mp4');
  execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y',
    '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=25:duration=8',
    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=8',
    '-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
    '-c:a', 'aac', video]);
  const bytes = fs.readFileSync(video);
  const upload = await request('POST', '/upload/initiate', { project_id: project.id,
    asset_name: 'Local video', original_filename: 'local-video.mp4',
    mime_type: 'video/mp4', file_size_bytes: bytes.length });
  const parts = [];
  for (let start = 0; start < bytes.length; start += upload.chunk_size_bytes) {
    const signed = await request('POST', '/upload/presign-part', {
      s3_key: upload.s3_key, upload_id: upload.upload_id, part_number: parts.length + 1 });
    assert.equal(new URL(signed.presigned_url).origin, s3);
    const response = await fetch(signed.presigned_url, { method: 'PUT',
      body: bytes.subarray(start, start + upload.chunk_size_bytes) });
    assert(response.ok);
    parts.push({ PartNumber: parts.length + 1, ETag: response.headers.get('etag') });
  }
  await request('POST', '/upload/complete', { ...Object.fromEntries(
    ['s3_key', 'upload_id', 'asset_id', 'version_id'].map(key => [key, upload[key]])), parts });
  for (let i = 0; i < 180; i++) {
    const versions = await request('GET', `/assets/${upload.asset_id}/versions`);
    const version = versions.find(item => item.id === upload.version_id);
    assert.notEqual(version.processing_status, 'failed');
    if (version.processing_status === 'ready') return { project, upload };
    await sleep(1000);
  }
  throw Error('Převod videa nebyl dokončen');
}
async function main() {
  for (const [route, destination] of [['/video/', '/video'], ['/video-api', '/video-api/']]) {
    const response = await fetch(origin + route, { redirect: 'manual' });
    assert.equal(response.status, 308);
    assert.equal(new URL(response.headers.get('location'), origin).href, origin + destination);
  }
  assert.equal((await fetch(`${api}/health`)).status, 200);
  const redirected = await fetch(`${api}/health/`, { redirect: 'manual' });
  assert.equal(redirected.status, 307);
  assert.equal(redirected.headers.get('location'), `${api}/health`);
  for (const route of ['/docs', '/redoc', '/openapi.json']) {
    assert.equal((await fetch(api + route)).status, 404, route);
  }
  assert.equal((await fetch(`${api}/projects`)).status, 401);
  assert.equal((await fetch(`${api}/projects`, { headers: { Authorization: 'Bearer invalid' } })).status, 401);
  assert.equal((await fetch(`${api}/auth/login`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
      email: process.env.JRC_FF_SYNC_EMAIL, password: 'incorrect-local-password',
    }),
  })).status, 401);
  assert.equal((await fetch(`${api}/stream/hls/master.m3u8?token=invalid`)).status, 401);
  assert.equal((await fetch(`${api}/missing-route`)).status, 404);
  for (const [requestOrigin, expected] of [[origin, 200], ['https://foreign.invalid', 400]]) {
    const response = await fetch(`${api}/auth/login`, { method: 'OPTIONS', headers: {
      Origin: requestOrigin, 'Access-Control-Request-Method': 'POST',
      'Access-Control-Request-Headers': 'content-type',
    } });
    assert.equal(response.status, expected);
    if (expected === 200) assert.equal(response.headers.get('access-control-allow-origin'), origin);
    else assert.equal(response.headers.get('access-control-allow-origin'), null);
  }
  // Proxy přepisuje X-Real-IP, podvržená hlavička tedy neobejde limit (30 za minutu).
  const shareStatuses = [];
  for (let i = 0; i < 31; i++) {
    shareStatuses.push((await fetch(`${api}/share/f2b04-missing`, {
      headers: { 'X-Real-IP': `198.51.100.${i + 1}` } })).status);
  }
  assert.equal(shareStatuses.at(-1), 429, `X-Real-IP: ${shareStatuses.join(' ')}`);
  const { project, upload } = await fixture();
  assert.equal((await fetch(`${api}/assets/${upload.asset_id}/stream`)).status, 401);
  // Ani platný token správce instance nedosáhne přes společný vstup na /admin.
  assert.equal((await fetch(`${api}/admin/users`, {
    headers: { Authorization: `Bearer ${token}` } })).status, 403);
  console.log('OK proxy API: předpony, 401/404, docs 404, /admin 403, CORS, X-Real-IP, přihlášení, místní upload a převod ready.');
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [1440, 390]) {
      const context = await browser.newContext({ viewport: { width, height: 900 } });
      const foreign = [], errors = [], badResponses = [], manifests = [], segments = [];
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (![origin, s3].includes(url.origin)) { foreign.push(url.origin + url.pathname); return route.abort(); }
        return route.continue();
      });
      const page = await context.newPage();
      page.setDefaultTimeout(90000);
      page.setDefaultNavigationTimeout(120000);
      page.on('pageerror', error => errors.push(error.message));
      page.on('response', response => {
        const url = new URL(response.url());
        if (response.status() >= 400) {
          badResponses.push([url.origin + url.pathname, response.status()]);
        }
        if (url.pathname.endsWith('.m3u8')) manifests.push([url.pathname, response.status()]);
        if (url.pathname.endsWith('.ts')) segments.push([url.origin, response.status()]);
      });
      await page.goto(`${origin}/video/`, { waitUntil: 'networkidle' });
      await page.waitForURL(`${origin}/video/login**`);
      console.log(`Chromium ${width}: login pod /video.`);
      const passwordMode = page.getByRole('button', { name: 'Sign in with password instead' });
      if (await passwordMode.isVisible()) await passwordMode.click();
      await page.locator('input[type="email"]').fill(process.env.JRC_FF_SYNC_EMAIL);
      await page.screenshot({ path: path.join(shots, `login-${width}.png`), fullPage: true });
      await page.locator('input[type="password"]').fill(process.env.JRC_FF_SYNC_PASSWORD);
      await page.getByRole('button', { name: 'Sign in', exact: true }).click();
      await page.waitForURL(`${origin}/video/projects`);
      console.log(`Chromium ${width}: heslové přihlášení OK.`);
      await page.goto(`${origin}/video/projects/${project.id}/assets/${upload.asset_id}`, { waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => document.querySelector('video')?.readyState >= 2);
      console.log(`Chromium ${width}: video načtené.`);
      await page.locator('video').first().evaluate(video => { video.muted = true; });
      await page.getByRole('button', { name: 'Play', exact: true }).click();
      await page.waitForFunction(() => document.querySelector('video').currentTime > 1);
      await page.getByRole('button', { name: 'Pause', exact: true }).click();
      const playback = await page.locator('video').first().evaluate(video => ({
        time: video.currentTime, width: video.videoWidth, frames: video.getVideoPlaybackQuality().totalVideoFrames,
      }));
      assert(playback.time > 1 && playback.width > 0 && playback.frames > 0);
      await page.locator('video').first().evaluate(video => { video.currentTime = 4; });
      await page.waitForFunction(() => !document.querySelector('video').seeking && document.querySelector('video').currentTime >= 4);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Vodorovné přetékání');
      await page.screenshot({ path: path.join(shots, `playback-${width}.png`) });
      assert(manifests.length >= 2 && manifests.every(([route, status]) => route.startsWith('/video-api/stream/hls/') && status === 200));
      assert(segments.length > 0 && segments.every(([host, status]) => host === s3 && status < 400));
      assert.deepEqual(foreign, []);
      assert.deepEqual(errors, []);
      assert.deepEqual(badResponses, []);
      // Reload musí zachovat přihlášení i předponu; po vymazání relace se vrátí login.
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => document.querySelector('video')?.readyState >= 2);
      await page.evaluate(() => localStorage.clear());
      await context.clearCookies();
      await page.goto(`${origin}/video/projects`, { waitUntil: 'networkidle' });
      await page.waitForURL(`${origin}/video/login**`);
      console.log(`OK Chromium ${width}: přihlášení formulářem, HLS/segmenty, dekódované snímky, pauza/posun, reload, odhlášení; bez cizích adres a chyb.`);
      await context.close();
    }
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
