// Pouze Docker, vlastní projekt jrc-f5b07. Videa vznikají lokálně, žádné externí služby.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
const project = process.env.JRC_PROJECT;
assert.equal(project, 'jrc-f5b07', 'Ověření smí běžet jen ve vlastním projektu');
const web = `http://127.0.0.1:${process.env.JRC_APP_PORT || 13000}`;
const api = `${web}/video-api`;
const output = path.resolve('ops/test-output/F5-B07');
const shots = path.resolve('ops/screenshoty/F5-B07');
fs.mkdirSync(output, { recursive: true }); fs.mkdirSync(shots, { recursive: true });
let token;
async function request(method, url, data) {
  const response = await fetch(api + url, { method, headers: {
    'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {})
  }, body: data === undefined ? undefined : JSON.stringify(data) });
  assert(response.ok, `${method} ${url}: ${response.status} ${await response.clone().text()}`);
  return response.status === 204 ? null : response.json();
}
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function upload(projectId, width, height, fps) {
  const video = path.join(output, `square-${width}-${fps}.mp4`);
  // White picture, 40px black square moving 1px per frame. Independent frame clock.
  execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y',
    '-f', 'lavfi', '-i', `color=white:s=${width}x${height}:r=${fps}:d=10`,
    '-f', 'lavfi', '-i', `color=black:s=40x40:r=${fps}:d=10`,
    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=10',
    '-filter_complex', "[0:v][1:v]overlay=x='n':y=80:eval=frame[v]", '-map', '[v]', '-map', '2:a',
    '-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast', '-crf', '18',
    '-pix_fmt', 'yuv420p', '-g', String(Math.round(fps)), '-c:a', 'aac', '-movflags', '+faststart', video]);
  const bytes = fs.readFileSync(video);
  const info = await request('POST', '/upload/initiate', { project_id: projectId,
    asset_name: `F5-B07 ${width}x${height} ${fps}fps`, original_filename: path.basename(video),
    mime_type: 'video/mp4', file_size_bytes: bytes.length });
  const parts = [];
  for (let start = 0; start < bytes.length; start += info.chunk_size_bytes) {
    const part = parts.length + 1;
    const signed = await request('POST', '/upload/presign-part', {
      s3_key: info.s3_key, upload_id: info.upload_id, part_number: part });
    const response = await fetch(signed.presigned_url, { method: 'PUT', body: bytes.subarray(start, start + info.chunk_size_bytes) });
    assert(response.ok); parts.push({ PartNumber: part, ETag: response.headers.get('etag') });
  }
  await request('POST', '/upload/complete', { upload_id: info.upload_id, s3_key: info.s3_key,
    asset_id: info.asset_id, version_id: info.version_id, parts });
  for (let i = 0; i < 180; i++) {
    const versions = await request('GET', `/assets/${info.asset_id}/versions`);
    const version = versions.find(v => v.id === info.version_id);
    assert.notEqual(version.processing_status, 'failed');
    if (version.processing_status === 'ready') {
      const actual = version.files.find(f => f.file_type === 'video').fps;
      assert(Math.abs(actual - fps) < 0.01, 'FPS metadat souboru');
      return { ...info, fps: actual, width, height };
    }
    await sleep(1000);
  }
  throw Error('Video nebylo převedeno včas');
}
async function sample(page, next = false) {
  return page.evaluate(next => {
    const video = document.querySelector('video');
    const label = document.querySelector('[data-testid="video-timecode"]');
    const read = () => {
      const canvas = document.createElement('canvas'); canvas.width = video.videoWidth; canvas.height = 1;
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      // Transcoder can scale portrait footage; map the square's original y=100.
      const scale = video.videoWidth / Number(video.dataset.originalWidth);
      ctx.drawImage(video, 0, 100 * scale, video.videoWidth, 1, 0, 0, video.videoWidth, 1);
      const pixels = ctx.getImageData(0, 0, video.videoWidth, 1).data;
      let x = -1;
      for (let i = 0; i < video.videoWidth; i++) {
        if (pixels[i * 4] < 45 && pixels[i * 4 + 1] < 45 && pixels[i * 4 + 2] < 45) { x = i; break; }
      }
      // FFmpeg overlay n starts at 1, hence square x=1 on frame zero.
      return { frame: Math.round(x / scale) - 1, seconds: Number(label.dataset.seconds),
        text: label.textContent, hidden: label.hidden, time: video.currentTime, x };
    };
    return next ? new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error(`Čekání na snímek: paused=${video.paused}, ended=${video.ended}, ready=${video.readyState}, time=${video.currentTime}`)), 5000);
      video.requestVideoFrameCallback(() => requestAnimationFrame(() => {
        clearTimeout(timer); try { resolve(read()); } catch (error) { reject(error); }
      }));
    }) : read();
  }, next);
}
function sync(sample, fps, format = 'timecode', offset = 0) {
  assert(sample.x >= 0 && !sample.hidden, JSON.stringify(sample));
  assert(Math.abs((sample.seconds - offset) * fps - sample.frame) <= 1.1, `Synchronizace: ${JSON.stringify(sample)}`);
  const frames = Math.floor(sample.seconds * fps + 0.0001);
  const nominal = Math.round(fps), whole = Math.floor(frames / nominal);
  const expected = format === 'frames' ? String(frames) : [Math.floor(whole / 3600), Math.floor(whole / 60) % 60,
    whole % 60, frames % nominal].map(n => String(n).padStart(2, '0')).join(':');
  assert.equal(sample.text, expected, 'Štítek používá FPS souboru');
}
async function seek(page, time) {
  await page.evaluate(time => new Promise((resolve, reject) => {
    const video = document.querySelector('video');
    const timer = setTimeout(() => reject(Error('Posun nebyl dokončen')), 5000);
    video.addEventListener('seeked', () => { clearTimeout(timer); resolve(); }, { once: true }); video.currentTime = time;
  }), time);
  await page.waitForFunction(() => !document.querySelector('[data-testid="video-timecode"]').hidden);
}
async function geometry(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const box = await page.evaluate(() => {
    const v = document.querySelector('video'), label = document.querySelector('[data-testid="video-timecode"]');
    const picture = label.parentElement.getBoundingClientRect(), l = label.getBoundingClientRect();
    return { left: l.left, right: l.right, top: l.top, bottom: l.bottom,
      picture: picture.toJSON(), width: innerWidth, height: innerHeight,
      scroll: document.documentElement.scrollWidth, font: parseFloat(getComputedStyle(label).fontSize),
      aspect: v.videoWidth / v.videoHeight, player: v.parentElement.parentElement.getBoundingClientRect().toJSON(),
      fullscreen: !!document.fullscreenElement || v.parentElement.parentElement.classList.contains('fixed') };
  });
  assert(box.left >= box.picture.left && box.right <= box.picture.right + 1);
  assert(box.top >= box.picture.top && box.bottom <= box.picture.bottom + 1, JSON.stringify(box));
  assert(Math.abs(box.picture.width / box.picture.height - box.aspect) < 0.02);
  assert(box.left >= 0 && box.right <= box.width && box.top >= 0 && box.bottom <= box.height);
  assert(box.scroll <= box.width && box.font >= 15, 'Čitelný štítek bez vodorovného posunu');
  if (box.fullscreen) assert(Math.abs(box.player.width - box.width) <= 1 && Math.abs(box.player.height - box.height) <= 1, 'Přehrávač vyplňuje celé okno');
}
(async () => {
  const credentials = await request('POST', '/auth/login', { email: process.env.JRC_FF_SYNC_EMAIL,
    password: process.env.JRC_FF_SYNC_PASSWORD });
  token = credentials.access_token;
  const fixtureFile = path.join(output, 'fixtures.json');
  const cached = fs.existsSync(fixtureFile) ? JSON.parse(fs.readFileSync(fixtureFile, 'utf8')) : null;
  let fixtures = cached?.version === 3 ? cached : null;
  if (fixtures) {
    const exists = await fetch(`${api}/projects/${fixtures.project.id}`, { headers: { Authorization: `Bearer ${token}` } });
    if (exists.status === 404) fixtures = null; // Own Docker data may have been cleaned.
    else assert(exists.ok, `Kontrola ověřovacího projektu: ${exists.status}`);
  }
  const testProject = fixtures?.project ?? await request('POST', '/projects', { name: 'F5-B07 — synchronizace' });
  const assets = fixtures?.assets ?? [await upload(testProject.id, 640, 360, 25), await upload(testProject.id, 404, 720, 30)];
  fs.writeFileSync(fixtureFile, JSON.stringify({ version: 3, project: { id: testProject.id }, assets }));
  // HLS normalizes MPEG-TS timestamps to the earliest stream. AAC priming can
  // put its first video frame a few milliseconds after zero. Probe the actual
  // encoded segment independently; the picture clock never reads player time.
  for (const asset of assets) {
    const stream = await request('GET', `/assets/${asset.asset_id}/stream`);
    const masterUrl = api + stream.url;
    const master = await (await fetch(masterUrl)).text();
    const variantUrl = new URL(master.split('\n').find(line => line && !line.startsWith('#')), masterUrl);
    const variant = await (await fetch(variantUrl)).text();
    const segmentUrl = variant.split('\n').find(line => line && !line.startsWith('#'));
    const segment = path.join(output, `first-${asset.asset_id}.ts`);
    fs.writeFileSync(segment, Buffer.from(await (await fetch(segmentUrl)).arrayBuffer()));
    const probe = JSON.parse(execFileSync('ffprobe', ['-v', 'error', '-read_intervals', '%+#20',
      '-show_packets', '-show_entries', 'packet=codec_type,pts_time,dts_time', '-of', 'json', segment], { encoding: 'utf8' }));
    const videoStart = Number(probe.packets.find(p => p.codec_type === 'video').pts_time);
    asset.presentationOffset = videoStart - Math.min(...probe.packets.map(p => Number(p.dts_time)));
  }
  console.log('OK lokální upload a metadata: 25 a 30 fps; počátek HLS ověřen FFprobe:', assets.map(a => a.presentationOffset));
  const browser = await chromium.launch({ headless: true });
  const report = [];
  try {
    for (const [name, viewport] of [['desktop-1440', { width: 1440, height: 1000 }], ['mobil-390', { width: 390, height: 844 }]]) {
      for (const asset of assets) {
        const context = await browser.newContext({ viewport });
        const foreign = [];
        await context.route('**/*', route => {
          if (new URL(route.request().url()).hostname !== '127.0.0.1') { foreign.push(route.request().url()); return route.abort(); }
          return route.continue();
        });
        await context.addCookies([{ name: 'ff_access_token', value: token, url: web }]);
        await context.addInitScript(credentials => {
          localStorage.setItem('ff_access_token', credentials.access_token);
          localStorage.setItem('ff_refresh_token', credentials.refresh_token);
        }, credentials);
        const page = await context.newPage();
        page.setDefaultTimeout(90000);
        page.setDefaultNavigationTimeout(120000);
        page.on('pageerror', error => console.error('Chyba stránky:', error.message));
        await page.goto(`${web}/video/projects/${testProject.id}/assets/${asset.asset_id}`, { waitUntil: 'domcontentloaded' });
        await page.waitForFunction(() => document.querySelector('video')?.readyState >= 2);
        await page.locator('video').first().evaluate((v, width) => { v.dataset.originalWidth = width; v.muted = true; }, String(asset.width));
        await page.getByTestId('video-timecode').waitFor({ state: 'visible' });
        await geometry(page);
        await page.getByRole('button', { name: 'Play', exact: true }).click();
        await page.waitForFunction(() => document.querySelector('video').currentTime > 1);
        for (let i = 0; i < 8; i++) sync(await sample(page, true), asset.fps, 'timecode', asset.presentationOffset);
        await page.getByRole('button', { name: 'Pause', exact: true }).click();
        await sleep(150); // Allow the last already submitted frame to be painted.
        sync(await sample(page), asset.fps, 'timecode', asset.presentationOffset);
        const paused = await sample(page);
        assert.throws(() => sync({ ...paused, seconds: paused.seconds + 0.1 }, asset.fps, 'timecode', asset.presentationOffset), 'Kontrola musí odhalit chybu 0,1 sekundy');
        await sleep(200);
        assert.equal((await sample(page)).text, paused.text);
        for (const time of [7.48, 1.16, 5.52]) { await seek(page, time); sync(await sample(page), asset.fps, 'timecode', asset.presentationOffset); }
        // Assert the transition itself, not just the eventual seek result.
        const transition = await page.evaluate(() => new Promise(resolve => {
          const v = document.querySelector('video'), l = document.querySelector('[data-testid="video-timecode"]');
          const old = l.textContent;
          v.addEventListener('seeking', () => resolve({ hidden: l.hidden, text: l.textContent, old }), { once: true });
          v.currentTime = 3.6;
        }));
        assert(transition.hidden && transition.text === transition.old, 'Nový čas nesmí předběhnout obraz při posunu');
        await page.waitForFunction(() => !document.querySelector('[data-testid="video-timecode"]').hidden);
        sync(await sample(page), asset.fps, 'timecode', asset.presentationOffset);
        // Exercise the real transport handler without the page's asset-navigation keys.
        const track = await page.getByTestId('progress-bar-track').boundingBox();
        await page.mouse.click(track.x + track.width * 0.85, track.y + track.height / 2);
        await page.waitForFunction(() => !document.querySelector('[data-testid="video-timecode"]').hidden);
        sync(await sample(page), asset.fps, 'timecode', asset.presentationOffset);
        await page.screenshot({ path: path.join(shots, `${name}-${asset.width}-normal.png`) });
        await page.getByRole('button', { name: 'Enter fullscreen', exact: true }).click();
        await page.waitForFunction(() => !!document.fullscreenElement);
        await geometry(page); sync(await sample(page), asset.fps, 'timecode', asset.presentationOffset);
        await page.screenshot({ path: path.join(shots, `${name}-${asset.width}-fullscreen.png`) });
        await page.getByRole('button', { name: 'Exit fullscreen', exact: true }).click();
        await page.waitForFunction(() => !document.fullscreenElement);
        // Simulate iPhone: native-video fullscreen would lose custom overlays.
        await page.evaluate(() => { Element.prototype.requestFullscreen = undefined; Element.prototype.webkitRequestFullscreen = undefined; HTMLElement.prototype.webkitRequestFullscreen = undefined; });
        await page.getByRole('button', { name: 'Enter fullscreen', exact: true }).click();
        await page.getByRole('button', { name: 'Exit fullscreen', exact: true }).waitFor();
        assert.equal(await page.evaluate(() => document.fullscreenElement), null, 'Náhradní režim nepoužívá nativní fullscreen');
        await geometry(page);
        await seek(page, 2.48); sync(await sample(page), asset.fps, 'timecode', asset.presentationOffset);
        await page.screenshot({ path: path.join(shots, `${name}-${asset.width}-window-fullscreen.png`) });
        await page.keyboard.press('Escape');
        await page.getByRole('button', { name: 'Enter fullscreen', exact: true }).waitFor();
        assert.deepEqual(foreign, []);
        report.push({ viewport: name, width: asset.width, fps: asset.fps, presentationOffset: asset.presentationOffset, result: 'OK' });
        console.log(`OK ${name}, ${asset.width}, ${asset.fps}fps: obraz, pauza, posun, táhlo, fullscreen, náhradní fullscreen.`);
        await context.close();
      }
    }
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
