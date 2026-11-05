// Jen Docker: timeout 580s node --max-old-space-size=2048 scripts/P1-B18/video-without-audio.cjs
// Kontejner musí mít limit paměti; data připraví seed.py do ignorovaného fixture.json.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');

assert.equal(process.env.JRC_PROJECT, 'jrc-p1b18');
const origin = 'http://127.0.0.1:13613';
const base = origin + '/jrc';
const output = path.resolve('ops/test-output/P1-B18');
const shots = path.resolve('ops/screenshoty/P1-B18');
const fixture = JSON.parse(fs.readFileSync(path.join(output, 'fixture.json'), 'utf8'));
const detail = `${base}/workspace/${fixture.workspace}/content/${fixture.post}/?tab=video`;
const errors = [];
const manifests = [];
const evidence = [];
fs.mkdirSync(shots, { recursive: true });

function sample(audio) {
  const file = path.join(output, audio ? 'audio.mp4' : 'silent.mp4');
  const args = ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=25:duration=8'];
  if (audio) args.push('-f', 'lavfi', '-i', 'sine=frequency=440:duration=8', '-c:a', 'aac');
  args.push('-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-g', '50', file);
  execFileSync('ffmpeg', args, { timeout: 30000, stdio: 'pipe' });
  return file;
}

async function session(browser, role) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  await context.route('**/*', route => new URL(route.request().url()).origin === origin
    ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    if (new URL(response.url()).pathname.endsWith('/master.m3u8') && response.ok()) {
      manifests.push(response.text().then(text => {
        assert(text.includes('#EXT-X-STREAM-INF:'), 'HLS master nemá variantu');
      }));
    }
  });
  await page.goto(`${base}/accounts/login/`);
  await page.locator('[name=login]').fill(fixture[role].email);
  await page.locator('[name=password]').fill(fixture[role].password);
  await page.locator('button[type=submit]').click();
  await page.waitForURL(url => !url.pathname.includes('/accounts/login/'));
  return { context, page };
}

async function player(page, selectedId) {
  const id = selectedId || await page.locator('[data-video-select]').inputValue();
  const channels = JSON.parse(await page.locator('#jrc-video-data').textContent());
  const version = channels.flatMap(channel => channel.versions).find(version => version.id === id);
  assert(version?.ff_version_id, 'Chybí FreeFrame ID vybrané verze');
  const frame = await (await page.locator('iframe[data-video-frame]:visible').elementHandle()).contentFrame();
  await frame.waitForURL(url => url.searchParams.get('version') === version.ff_version_id, { timeout: 60000 });
  await frame.waitForFunction(() => document.querySelector('video')?.duration > 7, null, { timeout: 60000 });
  return frame;
}

async function verify(page, role, version, audio) {
  await page.goto(detail);
  const select = page.locator('[data-video-select]');
  await select.locator('option').nth(1).waitFor({ state: 'attached', timeout: 30000 });
  await player(page);
  const value = await select.locator('option').filter({ hasText: new RegExp(`^v${version}\\b`) }).getAttribute('value');
  await select.selectOption(value);
  const frame = await player(page, value);
  const video = frame.locator('video').first();
  await video.evaluate(async video => { video.muted = true; video.currentTime = 3; await video.play(); });
  await frame.waitForFunction(() => {
    const video = document.querySelector('video');
    return video?.currentTime > 4 && !video.seeking && video.readyState >= 2;
  }, null, { timeout: 30000 });
  await video.evaluate(video => video.pause());
  assert.match((await frame.getByTestId('video-timecode').innerText()).trim(), /^0:0[4-7]$/);
  const comment = `P1-B18 ${role} ${audio ? 'audio' : 'silent'} v${version}`;
  await frame.getByPlaceholder('Leave your comment...').fill(comment);
  await frame.getByTitle('Send (Enter)').click();
  await frame.getByText(comment, { exact: true }).first().waitFor();
  await frame.waitForFunction(text => [...document.querySelectorAll('button')].some(button =>
    /^0:0[4-7]$/.test(button.textContent.trim()) && button.closest('div')?.parentElement?.textContent.includes(text)), comment);
  await page.screenshot({ path: path.join(shots, `${role}-v${version}.png`) });
  await page.reload();
  await player(page);
  await page.locator('[data-video-select]').selectOption(value);
  const reopened = await player(page, value);
  await reopened.getByText(comment, { exact: true }).first().waitFor();
  const jump = reopened.locator('[class*="group/comment"]').filter({ hasText: comment })
    .getByTitle('Jump to timecode');
  const [minutes, seconds] = (await jump.innerText()).trim().split(':').map(Number);
  const expectedTime = minutes * 60 + seconds;
  await jump.click();
  await reopened.waitForFunction(expected => {
    const video = document.querySelector('video');
    // Časový štítek zaokrouhluje na sekundy; komentář ukládá přesný čas snímku.
    return video && Math.abs(video.currentTime - expected) < 0.6 && !video.seeking;
  }, expectedTime, { timeout: 30000 });
  evidence.push({ role, version, audio, playback: true, timecode: true, persistedComment: true, jumpToComment: true });
  console.log(`OK ${role} v${version}: přehrání, čas, uložený komentář a skok na čas`);
}

async function main() {
  const samples = [sample(false), sample(true)];
  const browser = await chromium.launch({ headless: true, args: ['--js-flags=--max-old-space-size=2048'] });
  try {
    const owner = await session(browser, 'admin');
    for (let i = 0; i < samples.length; i++) {
      await owner.page.goto(detail);
      await owner.page.locator('[data-video-file]').setInputFiles(samples[i]);
      await owner.page.locator(`[data-version][data-status=ready]`).nth(i).waitFor({ timeout: 180000 });
    }
    await verify(owner.page, 'admin', 1, false);
    await verify(owner.page, 'admin', 2, true);
    await owner.context.close();
    const client = await session(browser, 'client');
    await verify(client.page, 'client', 1, false);
    await verify(client.page, 'client', 2, true);
    await client.context.close();
    await Promise.all(manifests);
    assert(manifests.length >= 4, 'Chybí skutečné požadavky na HLS master');
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({ evidence, masters: manifests.length }, null, 2));
    console.log(JSON.stringify({ passed: evidence.length, masters: manifests.length }));
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
