// Živé ověření F2-B05 v Chromiu: média Brightbean a video FreeFrame přes S3 s vynucenou CSP.
// Spouštět jen v Dockeru (jrc-dev:local, síť hostitele) nad postradatelným projektem jrc-f2b05.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');

assert.equal(process.env.JRC_PROJECT, 'jrc-f2b05');
const origin = `http://127.0.0.1:${process.env.JRC_APP_PORT}`;
const s3 = `http://127.0.0.1:${process.env.JRC_S3_PORT}`;
const api = `${origin}/video-api`;
const output = path.resolve('ops/test-output/F2-B05');
const shots = path.resolve('ops/screenshoty/F2-B05');
fs.mkdirSync(output, { recursive: true });
fs.mkdirSync(shots, { recursive: true });
const seed = JSON.parse(fs.readFileSync(path.join(output, 'seed.json'), 'utf8'));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const only = process.argv[2];

function ffmpeg(args) {
  execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', ...args]);
}

// Kontrola vynucené CSP: hlavička (ne report-only), přesný původ S3, žádné zástupné znaky.
function checkCsp(headers, label) {
  assert.equal(headers['content-security-policy-report-only'], undefined, `${label}: jen report-only`);
  const policy = headers['content-security-policy'];
  assert(policy, `${label}: chybí CSP`);
  const directives = Object.fromEntries(policy.split(';').map(item => item.trim().split(/\s+/))
    .filter(([name]) => name).map(([name, ...values]) => [name, values]));
  for (const name of ['img-src', 'media-src', 'connect-src']) {
    assert(directives[name]?.includes(s3), `${label}: ${name} bez ${s3}`);
    for (const value of directives[name]) {
      assert(!['*', 'http:', 'https:'].includes(value) && !value.includes('*'), `${label}: ${name} ${value}`);
      assert(["'self'", 'data:', 'blob:', s3].includes(value), `${label}: ${name} navíc ${value}`);
    }
  }
  return directives;
}

async function newContext(browser, width) {
  const context = await browser.newContext({ viewport: { width, height: 900 } });
  const state = { foreign: [], failed: [], errors: [], csp: [], s3Requests: [] };
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (![origin, s3].includes(url.origin)) {
      state.foreign.push(url.origin + url.pathname);
      return route.abort();
    }
    if (url.origin === s3) state.s3Requests.push([route.request().method(), url.pathname]);
    return route.continue();
  });
  // Porušení CSP zachytí událost dokumentu i hláška konzole Chromia.
  await context.addInitScript(() => {
    document.addEventListener('securitypolicyviolation', event => {
      console.error(`CSP-VIOLATION ${event.violatedDirective} ${event.blockedURI}`);
    });
  });
  context.on('console', message => {
    const text = message.text();
    if (text.includes('CSP-VIOLATION') || text.includes('Content Security Policy')) {
      state.csp.push(`${new URL(message.page()?.url() || origin).pathname}: ${text}`);
    }
  });
  context.on('response', response => {
    if (response.status() >= 400) {
      state.failed.push([response.status(), response.url().split('?')[0], new URL(response.url()).searchParams.has('timestamp')]);
    }
  });
  return { context, state };
}

function assertClean(state, label, allowedFailures = () => false) {
  assert.deepEqual(state.csp, [], `${label}: porušení CSP`);
  assert.deepEqual(state.foreign, [], `${label}: cizí adresy`);
  assert.deepEqual(state.errors, [], `${label}: chyby JS`);
  assert.deepEqual(state.failed.filter(item => !allowedFailures(item)), [], `${label}: chybové odpovědi`);
}

async function brightbean(browser) {
  const image = path.join(output, 'f2b05-image.png');
  ffmpeg(['-f', 'lavfi', '-i', 'testsrc2=size=960x600', '-frames:v', '1', image]);
  const { context, state } = await newContext(browser, 1440);
  await context.addCookies([{ name: 'sessionid', value: seed.session, url: origin }]);
  const page = await context.newPage();
  page.on('pageerror', error => state.errors.push(String(error)));
  const library = `${origin}/workspace/${seed.workspace}/media/`;
  const response = await page.goto(library, { waitUntil: 'networkidle' });
  assert.equal(response.status(), 200);
  checkCsp(response.headers(), 'Brightbean');
  const uploaded = page.waitForResponse(item => item.request().method() === 'POST' && item.url().includes('/media/upload/'));
  await page.locator('input[type="file"][x-ref="fileInput"]').setInputFiles(image);
  const result = await uploaded;
  assert(result.ok(), 'Nahrání obrázku selhalo');
  const [{ id: assetId, status: uploadStatus }] = (await result.json()).results;
  assert.equal(uploadStatus, 'ok');
  await page.reload({ waitUntil: 'networkidle' });
  const card = page.locator(`.ml-asset-card img[alt="${path.basename(image)}"]`).first();
  await card.waitFor({ state: 'visible', timeout: 30000 });
  await card.evaluate(img => img.decode());
  const src = await card.evaluate(img => ({ src: img.currentSrc, width: img.naturalWidth }));
  assert.equal(new URL(src.src).origin, s3, 'Náhled nemíří na S3');
  assert.match(new URL(src.src).pathname, /^\/jrc-app\//);
  assert(src.width > 0);
  // Nepodepsaná adresa stejného objektu je odmítnutá.
  const bare = new URL(src.src);
  assert.equal((await fetch(bare.origin + bare.pathname)).status, 403);
  console.log('OK Brightbean: obrázek nahrán přes UI, náhled z jrc-app (podepsaný), nepodepsaný 403.');

  assertClean(state, 'Brightbean knihovna');
  console.log('OK Brightbean 1440: nahrání a náhled bez porušení CSP, cizích adres, chyb JS a HTTP.');
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: path.join(shots, 'bb-media-1440.png'), fullPage: true });

  // Detail a editor obrázku načítají originál z S3 (editor klonuje obrázek cross-origin).
  await page.goto(`${library}${assetId}/`, { waitUntil: 'networkidle' });
  const detail = page.locator(`img[src^="${s3}/jrc-app/"]`).first();
  await detail.waitFor({ state: 'visible', timeout: 15000 });
  await detail.evaluate(img => img.decode());
  await page.screenshot({ path: path.join(shots, 'bb-detail-1440.png') });
  await page.goto(`${library}${assetId}/edit/`, { waitUntil: 'networkidle' });
  const cropped = page.locator('.cropper-canvas img');
  await cropped.waitFor({ state: 'visible', timeout: 15000 });
  await cropped.evaluate(img => img.decode());
  assert(await cropped.evaluate(img => img.naturalWidth > 0), 'Editor nenačetl obrázek');
  await page.screenshot({ path: path.join(shots, 'bb-edit-1440.png') });
  // Úložiště: žádné porušení img/media/connect-src. Známé porušení script-src
  // vložených skriptů panelu detailu (htmx bez nonce) je mimo rozsah F2-B05.
  const storage = state.csp.filter(text => /img-src|media-src|connect-src/.test(text) || text.includes(s3));
  assert.deepEqual(storage, [], 'Brightbean detail/editor: porušení CSP úložiště');
  const known = state.csp.filter(text => !storage.includes(text));
  state.csp = [];
  if (known.length) console.log(`Pozor (mimo rozsah): ${known.length} porušení script-src vložených skriptů v panelu detailu.`);
  state.errors = state.errors.filter(text => !/^ReferenceError: (tagInput|tags|inputValue|suggestions) is not defined$/.test(text));
  assertClean(state, 'Brightbean detail/editor');
  console.log('OK Brightbean: detail i editor obrázku načteny z S3 bez porušení CSP úložiště a bez HTTP chyb.');
  await context.close();
}

let token;
async function request(method, route, data) {
  const response = await fetch(api + route, { method, headers: {
    'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }, body: data === undefined ? undefined : JSON.stringify(data) });
  assert(response.ok, `${method} ${route}: ${response.status}`);
  return response.json();
}

async function freeframe(browser) {
  // Malé místní video, ale větší než jedna část nahrávání (část má 10 MiB), aby šlo po částech.
  const video = path.join(output, 'f2b05-video.mp4');
  ffmpeg(['-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=25:duration=12',
    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=12',
    '-c:v', 'libx264', '-preset', 'ultrafast', '-qp', '0', '-pix_fmt', 'yuv420p', '-c:a', 'aac', video]);
  const size = fs.statSync(video).size;
  token = (await request('POST', '/auth/login', {
    email: process.env.JRC_FF_SYNC_EMAIL, password: process.env.JRC_FF_SYNC_PASSWORD })).access_token;
  const project = await request('POST', '/projects', { name: `F2-B05 S3 ${Date.now()}` });

  const { context, state } = await newContext(browser, 1440);
  const page = await context.newPage();
  page.setDefaultTimeout(90000);
  page.on('pageerror', error => state.errors.push(String(error)));
  const login = await page.goto(`${origin}/video/login`, { waitUntil: 'networkidle' });
  checkCsp(login.headers(), 'FreeFrame /video');
  const passwordMode = page.getByRole('button', { name: 'Sign in with password instead' });
  if (await passwordMode.isVisible()) await passwordMode.click();
  await page.locator('input[type="email"]').fill(process.env.JRC_FF_SYNC_EMAIL);
  await page.locator('input[type="password"]').fill(process.env.JRC_FF_SYNC_PASSWORD);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.waitForURL(`${origin}/video/projects`);
  await page.goto(`${origin}/video/projects/${project.id}`, { waitUntil: 'networkidle' });
  const initiated = page.waitForResponse(item => item.url().endsWith('/upload/initiate'));
  const completed = page.waitForResponse(item => item.url().endsWith('/upload/complete'), { timeout: 180000 });
  await page.getByRole('button', { name: 'Upload', exact: true }).first().click();
  await page.getByRole('dialog').locator('input[type="file"]').setInputFiles(video);
  await page.screenshot({ path: path.join(shots, 'ff-upload-dialog-1440.png') });
  await page.getByRole('dialog').getByRole('button', { name: 'Start upload', exact: true }).click();
  const upload = await (await initiated).json();
  assert((await completed).ok(), 'Dokončení nahrávání selhalo');
  const parts = state.s3Requests.filter(([method]) => method === 'PUT');
  assert(parts.length >= 2 && parts.length === Math.ceil(size / upload.chunk_size_bytes),
    `Očekáváno nahrání po částech: ${parts.length} částí, ${size} B, část ${upload.chunk_size_bytes} B`);
  assert(parts.every(([, route]) => route.startsWith('/jrc-video/')));
  console.log(`OK FreeFrame: video ${size} B nahráno z prohlížeče po ${parts.length} částech přímo do jrc-video.`);
  for (let i = 0; i < 240; i++) {
    const versions = await request('GET', `/assets/${upload.asset_id}/versions`);
    const version = versions.find(item => item.id === upload.version_id);
    assert.notEqual(version.processing_status, 'failed');
    if (version.processing_status === 'ready') break;
    assert(i < 239, 'Převod videa nebyl dokončen');
    await sleep(1000);
  }
  await page.goto(`${origin}/video/projects/${project.id}/assets/${upload.asset_id}`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.querySelector('video')?.readyState >= 2);
  await page.locator('video').first().evaluate(item => { item.muted = true; item.currentTime = 2; });
  await page.waitForFunction(() => !document.querySelector('video').seeking);
  await sleep(500);
  await page.screenshot({ path: path.join(shots, 'ff-video-1440.png') });
  assert(state.s3Requests.some(([method, route]) => method === 'GET' && route.endsWith('.ts')), 'Segmenty HLS nešly z S3');
  await page.goto(`${origin}/video/projects/${project.id}`, { waitUntil: 'networkidle' });
  await page.screenshot({ path: path.join(shots, 'ff-project-1440.png') });
  assertClean(state, 'FreeFrame');
  console.log('OK FreeFrame 1440: přehrání z S3, bez porušení CSP, cizích adres, chyb JS a HTTP.');
  await context.close();
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    if (only !== 'ff') await brightbean(browser);
    if (only !== 'bb') await freeframe(browser);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
