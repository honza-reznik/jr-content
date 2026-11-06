// P1-B26: živé ověření nahrání v Chromiu (jen Docker, vymyšlené účty, lokální testovací média).
// Neplatný soubor musí být odmítnut do 30 s bez vzniku verze; platné projdou do Ready;
// během převodu je vidět stav/procenta a stránka se po dokončení sama obnoví.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
assert.equal(process.env.JRC_PROJECT, 'jrc-p1b26');
const output = path.resolve('ops/test-output/P1-B26');
const shots = path.resolve('ops/screenshoty/P1-B26');
fs.mkdirSync(shots, { recursive: true });
const fixture = JSON.parse(fs.readFileSync(path.join(output, 'fixture.json')));
const origin = 'http://127.0.0.1:13683';
const root = origin + '/jrc';
const detail = `${root}/workspace/${fixture.workspace}/content/${fixture.upload_post}/?tab=video`;
const big = process.argv.includes('--big');
const results = [];
const errors = [];

function record(name, pass, metrics) {
  results.push({ name, pass, ...metrics });
  console.log(JSON.stringify(results.at(-1)));
}
const shot = (page, name) => page.screenshot({ path: path.join(shots, `${name}.png`), fullPage: true, timeout: 15000 });

async function rows(page) {
  return page.locator('#jrc-video li[data-version]').evaluateAll(items => items.map(li => ({
    status: li.dataset.status, name: li.dataset.name,
    label: li.querySelector('[data-video-status]')?.textContent, hint: !!li.querySelector('[data-video-hint]'),
  })));
}

async function upload(page, file) {
  const input = page.locator('[data-video-file]').first();
  await input.setInputFiles(file);
}

async function refused(page, name, file) {
  const before = (await rows(page)).length;
  const started = Date.now();
  await upload(page, file);
  const box = page.locator('[data-video-error]:visible').first();
  await box.waitFor({ timeout: 30000 });
  const seconds = (Date.now() - started) / 1000;
  const text = await box.textContent();
  await page.waitForTimeout(1500);
  const after = await rows(page);
  await box.scrollIntoViewIfNeeded();
  await shot(page, `${name}-message`);
  await page.reload();
  await page.locator('#jrc-video').waitFor();
  await page.waitForTimeout(1500);
  const reloaded = await rows(page);
  await shot(page, `${name}-refused`);
  record(`${name}-refused`, seconds < 30 && /not a playable video/.test(text) && !/Continue upload/.test(text) &&
    after.length === before && reloaded.length === before && !reloaded.some(r => r.status === 'processing'),
  { seconds, text, before, after: after.length, reloaded: reloaded.length });
}

async function accepted(page, name, file, timeoutMs) {
  const before = (await rows(page)).filter(r => r.status === 'ready').length;
  const started = Date.now();
  await upload(page, file);
  const labels = new Set();
  let hint = false;
  let notice = '';
  while (Date.now() - started < timeoutMs) {
    const list = await rows(page);
    for (const row of list) if (row.status === 'processing') { labels.add(row.label); hint = hint || row.hint; }
    if (list.filter(r => r.status === 'ready').length > before) break;
    if (list.some(r => r.status === 'failed')) break;
    await page.waitForTimeout(1000);
  }
  notice = (await page.locator('[data-video-notice]').textContent()) || '';
  const list = await rows(page);
  const ready = list.filter(r => r.status === 'ready').length > before;
  const frame = page.locator('iframe[data-video-frame]:visible');
  const playerShown = await frame.count() > 0;
  await shot(page, `${name}-accepted`);
  record(`${name}-accepted`, ready && labels.size > 0 && hint && /ready to play/.test(notice) && playerShown,
    { seconds: (Date.now() - started) / 1000, labels: [...labels], hint, notice, playerShown });
}

async function missingVideo(page) {
  // AK5: a video that does not exist (deleted) answers with a message, not endless "Loading asset...".
  await page.goto(detail);
  await page.locator('#jrc-video').waitFor();
  const project = await page.evaluate(() => JSON.parse(document.getElementById('jrc-video-data').textContent)
    .map(c => c.player_path).find(Boolean));
  assert(project, 'player path');
  const next = project.replace(/assets\/[^/?]+/, 'assets/00000000-0000-4000-8000-000000000000') + '?embed=1';
  await page.goto(`${root}/video-sso/?next=${encodeURIComponent(next)}`);
  await page.getByRole('button', { name: 'Continue to video' }).click();
  // Not getByRole('alert'): Next's empty route announcer has that role too.
  const alert = page.locator('[data-asset-load-error]');
  await alert.waitFor({ timeout: 30000 });
  const text = await alert.textContent();
  const loading = await page.getByText('Loading asset...').count();
  await shot(page, 'missing-video');
  const status = await alert.getAttribute('data-asset-load-error');
  record('missing-video-message', /no longer exists/.test(text) && loading === 0 && status === '404', { text, status, url: page.url().replace(/#.*/, '') });
}

async function main() {
  const browser = await chromium.launch({ headless: true, args: ['--js-flags=--max-old-space-size=2048'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort('blockedbyclient'));
    await context.addCookies([{ ...fixture.admin.cookie, url: root + '/' }]);
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(detail);
    await page.locator('#jrc-video').waitFor();
    if (process.argv.includes('--missing-only')) {
      await missingVideo(page);
    } else if (big) {
      await accepted(page, 'velke-270MB', '/media/velke-270MB.mp4', 900000);
    } else {
      const cut = path.join(output, 'prerusene.mp4');
      const data = fs.readFileSync('/media/kratke-1s.mp4');
      fs.writeFileSync(cut, data.subarray(0, Math.floor(data.length / 2)));
      await refused(page, 'neplatne', '/media/neplatne.mp4');
      await refused(page, 'prerusene', cut);
      await accepted(page, 'kratke-1s', '/media/kratke-1s.mp4', 240000);
      await accepted(page, 'video-webm', '/media/video.webm', 240000);
      await accepted(page, 'ctverec-mov', '/media/ctverec.mov', 240000);
      await missingVideo(page);
    }
    await context.close();
  } finally { await browser.close(); }
  fs.writeFileSync(path.join(output, big ? 'live-big.json' : process.argv.includes('--missing-only') ? 'live-missing.json' : 'live-upload.json'), JSON.stringify({ results, errors }, null, 2));
  assert.deepEqual(errors, []);
  assert.equal(results.filter(r => !r.pass).length, 0, 'Některé ověření neprošlo');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
