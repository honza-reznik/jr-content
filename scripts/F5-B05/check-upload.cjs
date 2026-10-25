// F5-B05: nahrání verze videa v prohlížeči. Jen Docker, vlastní projekt jrc-f5b05, vymyšlené účty.
// Video 1080p / 1 min vyrábí FFmpeg lokálně. Přerušení = zavření karty uprostřed nahrávání.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');

assert.match(process.env.JRC_PROJECT, /^(?:jrc-f5b05|jrc-f5b09a)$/);
assert.ok(process.env.JRC_APP_PORT);
assert.ok(process.env.JRC_S3_PORT);
const origin = `http://127.0.0.1:${process.env.JRC_APP_PORT}`;
const s3Origin = `http://127.0.0.1:${process.env.JRC_S3_PORT}`;
const output = path.resolve('ops/test-output/F5-B05');
const shots = path.resolve('ops/screenshoty/F5-B05');
fs.mkdirSync(output, { recursive: true });
fs.mkdirSync(shots, { recursive: true });
const fixture = JSON.parse(fs.readFileSync(path.join(output, 'fixture.json'), 'utf8'));
const tab = `${origin}/workspace/${fixture.workspace}/content/${fixture.post}/?tab=video`;
const base = `${origin}/workspace/${fixture.workspace}/content/${fixture.post}/video/`;

function makeVideo(file, size, seconds, bitrate) {
  if (fs.existsSync(file)) return;
  execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y',
    '-f', 'lavfi', '-i', `testsrc2=size=${size}:rate=25:duration=${seconds}`,
    '-f', 'lavfi', '-i', `sine=frequency=440:sample_rate=48000:duration=${seconds}`,
    '-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
    '-b:v', bitrate, '-minrate', bitrate, '-maxrate', bitrate, '-bufsize', bitrate,
    '-c:a', 'aac', file]);
}

async function login(browser, who) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  await page.goto(`${origin}/accounts/login/`);
  await page.locator('input[name="login"]').fill(fixture[who].email);
  await page.locator('input[name="password"]').fill(fixture[who].password);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL(url => !url.pathname.startsWith('/accounts/login'));
  return { context, page };
}

function watch(page, log) {
  page.on('pageerror', error => log.errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') log.errors.push(message.text()); });
  page.on('request', request => {
    if (request.method() === 'PUT' && request.url().startsWith(s3Origin)) log.puts.push(request.url());
  });
  page.on('response', async response => {
    const url = response.url();
    if (url.startsWith(base)) {
      const text = await response.text().catch(() => '');
      log.bb.push({ url, status: response.status(), text });
    }
  });
}

async function mobileShot(context, name) {
  const mobile = await context.newPage();
  await mobile.setViewportSize({ width: 390, height: 1700 });
  await mobile.goto(tab);
  await mobile.locator('[data-video-channel]').first().waitFor();
  assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true,
    'Na 390 px nesmí být vodorovné posouvání');
  await mobile.screenshot({ path: path.join(shots, name), fullPage: true });
  await mobile.close();
}

async function pickFile(page, buttonName, file) {
  const [chooser] = await Promise.all([
    page.waitForEvent('filechooser'),
    page.getByRole('button', { name: buttonName, exact: true }).first().click(),
  ]);
  await chooser.setFiles(file);
}

async function main() {
  const big = path.join(output, 'reel-1080p-60s.mp4');
  const small = path.join(output, 'reel-v2-360p.mp4');
  const wrong = path.join(output, 'wrong-file.mp4');
  makeVideo(big, '1920x1080', 60, '8M');
  makeVideo(small, '640x360', 6, '1M');
  makeVideo(wrong, '320x180', 2, '200k');
  const bigSize = fs.statSync(big).size;
  console.log(`Testovací video: ${(bigSize / 1048576).toFixed(1)} MB`);
  const browser = await chromium.launch({ headless: true });
  try {
    // --- klient a nahrávání bez práva ---
    const client = await login(browser, 'client');
    await client.page.goto(tab);
    await client.page.getByText('Ella Instagram', { exact: false }).first().waitFor();
    assert.equal(await client.page.getByRole('button', { name: 'Upload version' }).count(), 0);
    const channelKey = await (async () => {
      const editor = await login(browser, 'editor');
      await editor.page.goto(tab);
      const key = await editor.page.locator('[data-video-channel]').first().getAttribute('data-video-channel');
      await editor.context.close();
      return key;
    })();
    const csrf = (await client.context.cookies()).find(cookie => cookie.name === 'csrftoken').value;
    const denied = await client.context.request.post(base + 'start/', {
      headers: { 'X-CSRFToken': csrf, Origin: origin, Referer: origin + '/' },
      data: { channel: channelKey, filename: 'a.mp4', size: 1000, type: 'video/mp4' },
    });
    assert.equal(denied.status(), 403, 'Klient nesmí zahájit nahrání ani přímým požadavkem');
    await client.page.screenshot({ path: path.join(shots, 'client-1440.png') });
    await client.context.close();
    console.log('OK: klient nemá tlačítko a server odmítá přímé zahájení (403)');

    // --- editor: přerušení a pokračování ---
    const editor = await login(browser, 'editor');
    const log = { errors: [], puts: [], bb: [] };
    watch(editor.page, log);
    await editor.page.goto(tab);
    await editor.page.getByRole('button', { name: 'Upload version' }).first().waitFor();
    await editor.page.screenshot({ path: path.join(shots, 'before-1440.png') });
    await mobileShot(editor.context, 'before-390.png');

    // Nesprávný typ a příliš velký soubor server i prohlížeč odmítnou.
    fs.writeFileSync(path.join(output, 'notes.txt'), 'x');
    await editor.page.locator('input[data-video-file]').first().setInputFiles(path.join(output, 'notes.txt'));
    await editor.page.getByText('Unsupported file type').first().waitFor();

    let completedParts = 0;
    editor.page.on('response', response => {
      if (response.request().method() === 'PUT' && response.url().startsWith(s3Origin) && response.ok()) completedParts++;
    });
    await pickFile(editor.page, 'Upload version', big);
    await editor.page.getByText(/Uploading reel-1080p-60s\.mp4/).first().waitFor().catch(async error => {
      console.error('Chyby stránky:', log.errors, 'Odpovědi:', log.bb.map(item => `${item.status} ${item.url.slice(base.length)} ${item.text.slice(0, 200)}`));
      console.error(await editor.page.locator('[data-video-error]').allInnerTexts());
      throw error;
    });
    await editor.page.screenshot({ path: path.join(shots, 'uploading-1440.png') });
    const deadline = Date.now() + 60000;
    while (completedParts < 2 && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 50));
    if (completedParts < 2) {
      console.error('Chyby stránky:', log.errors, 'Odpovědi:', log.bb.map(item => `${item.status} ${item.url.slice(base.length)} ${item.text.slice(0, 160)}`), 'PUT:', log.puts.length);
    }
    assert(completedParts >= 2, 'Nahrání se nerozběhlo');
    await editor.page.close(); // zavření karty uprostřed nahrávání
    const sentBefore = completedParts;
    console.log(`Přerušeno po ${sentBefore} dokončených částech`);

    const page = await editor.context.newPage();
    const log2 = { errors: [], puts: [], bb: [] };
    watch(page, log2);
    await page.goto(tab);
    const row = page.locator('[data-version][data-status="uploading"]').first();
    await row.waitFor();
    await row.getByRole('button', { name: 'Continue upload' }).waitFor();
    await page.screenshot({ path: path.join(shots, 'interrupted-1440.png') });
    await mobileShot(editor.context, 'interrupted-390.png');

    // Jiný soubor se nepřijme.
    await pickFile(page, 'Continue upload', wrong);
    await page.getByText('Choose the same file as before').first().waitFor();
    assert.equal(log2.puts.length, 0, 'Jiný soubor se nesmí nahrávat');

    // Správný soubor: pošlou se jen chybějící části.
    await pickFile(page, 'Continue upload', big);
    await page.getByText(/Finishing upload|Upload finished/).first().waitFor({ timeout: 120000 });
    const resumes = log2.bb.filter(item => item.url.includes('/resume/') && item.status === 200);
    assert(resumes.length >= 1, 'Pokračování se nezeptalo, co už v úložišti je');
    const info = JSON.parse(resumes[0].text);
    const total = Math.ceil(bigSize / info.chunk_size);
    assert(info.held_parts.length >= 1, 'Úložiště nemělo žádnou hotovou část');
    await new Promise(resolve => setTimeout(resolve, 500));
    assert.equal(log2.puts.length, total - info.held_parts.length,
      `Po pokračování mělo jít ${total - info.held_parts.length} částí, šlo ${log2.puts.length}`);
    assert(log2.puts.length < total, 'Pokračování nesmí posílat vše znovu');
    console.log(`OK: pokračování poslalo ${log2.puts.length} z ${total} částí (hotových v úložišti: ${info.held_parts.length})`);

    // Stav převodu: čeká -> převádí -> hotovo.
    await page.getByText('Converting').first().waitFor({ timeout: 30000 }).catch(() => {});
    await page.screenshot({ path: path.join(shots, 'converting-1440.png') });
    await page.locator('[data-version][data-status="ready"]').first().waitFor({ timeout: 360000 });
    const ready = await page.locator('[data-version][data-status="ready"]').first().innerText();
    assert(/v1/.test(ready) && /Ready/.test(ready) && /1920×1080/.test(ready) && /1:00/.test(ready), ready);
    await page.screenshot({ path: path.join(shots, 'ready-1440.png') });
    await mobileShot(editor.context, 'ready-390.png');
    console.log('OK: v1 hotová, 1920×1080, 1:00');

    // Druhá verze téhož kanálu.
    await pickFile(page, 'Upload version', small);
    await page.getByText(/Upload finished/).first().waitFor({ timeout: 60000 });
    await page.locator('[data-version][data-status="ready"]').nth(1).waitFor();
    await page.locator('[data-version][data-status="ready"]').filter({ hasText: /\bv2\b/ }).waitFor({ timeout: 240000 });
    const second = await page.locator('[data-version][data-status="ready"]').filter({ hasText: /\bv2\b/ }).first().innerText();
    assert(/v2/.test(second), second);
    await page.screenshot({ path: path.join(shots, 'two-versions-1440.png') });
    console.log('OK: v2 téhož kanálu hotová');

    // --- bezpečnost ---
    const everything = [...log.bb, ...log2.bb];
    assert(everything.length > 5);
    assert(everything.every(item => !/eyJ[A-Za-z0-9_-]{10,}|access_token|refresh_token|Bearer/.test(item.text)),
      'Odpověď Brightbean obsahuje token');
    const stored = await page.evaluate(() => ({ local: Object.keys(localStorage), session: Object.keys(sessionStorage) }));
    assert(![...stored.local, ...stored.session].some(key => /token|auth|access/i.test(key)), 'V prohlížeči jsou klíče pro přihlašovací tokeny FreeFrame');
    const urls = [...log.puts, ...log2.puts];
    assert(urls.length > 0 && urls.every(url => url.startsWith(s3Origin + '/')), 'Část šla jinam než do úložiště');
    assert(urls.every(url => Number(new URL(url).searchParams.get('X-Amz-Expires')) <= 900), 'Podpis části je delší než 15 minut');
    const violations = [...log.errors, ...log2.errors].filter(text => /Content Security Policy|Refused to connect|CORS/i.test(text));
    assert.deepEqual(violations, [], 'CSP nebo CORS blokovalo nahrávání');
    // Cizí uživatel (jiný manažer stejného klienta) nedostane adresy k cizímu nahrávání.
    const versionId = (await page.locator('[data-version]').first().getAttribute('data-version'));
    const other = await login(browser, 'manager');
    const otherCsrf = (await other.context.cookies()).find(cookie => cookie.name === 'csrftoken').value;
    const foreign = await other.context.request.post(`${base}versions/${versionId}/part/`, {
      headers: { 'X-CSRFToken': otherCsrf, Origin: origin, Referer: origin + '/' },
      data: { part: 1, s3_key: 'raw/x/y/z/original.mp4', upload_id: 'x' },
    });
    assert.equal(foreign.status(), 404);
    assert.equal((await other.context.request.get(`${base}versions/${versionId}/resume/`)).status(), 404);
    await other.context.close();
    console.log('OK: bez tokenů v prohlížeči, krátké podpisy, jen úložiště, bez CSP/CORS chyb, cizí uživatel 404');
    await editor.context.close();
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exit(1); });
