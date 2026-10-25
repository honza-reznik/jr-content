// F5-B08: záložka Video v prohlížeči. Jen Docker, vlastní projekt jrc-f5b08, vymyšlené účty.
// Scénář: tým nahraje v1 a v2, Ella (klient) otevře Video, přehraje v2, přidá komentář k 0:17
// ("zkrátit"), tým ho vidí; tým označí v2 jako Final for publishing, klient a viewer to nesmí.
// Testovací videa vyrábí FFmpeg lokálně.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');

assert.match(process.env.JRC_PROJECT, /^(?:jrc-f5b08|jrc-f5b09a)$/);
assert.ok(process.env.JRC_APP_PORT);
const origin = `http://127.0.0.1:${process.env.JRC_APP_PORT}`;
const output = path.resolve('ops/test-output/F5-B08');
const shots = path.resolve('ops/screenshoty/F5-B08');
fs.mkdirSync(output, { recursive: true });
fs.mkdirSync(shots, { recursive: true });
const fixture = JSON.parse(fs.readFileSync(path.join(output, 'fixture.json'), 'utf8'));
const tab = `${origin}/workspace/${fixture.workspace}/content/${fixture.post}/?tab=video`;
const base = `${origin}/workspace/${fixture.workspace}/content/${fixture.post}/video/`;
const errors = [];

function makeVideo(file, source, size, seconds) {
  if (fs.existsSync(file)) return;
  execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y',
    '-f', 'lavfi', '-i', `${source}=size=${size}:rate=25:duration=${seconds}`,
    '-f', 'lavfi', '-i', `sine=frequency=440:sample_rate=48000:duration=${seconds}`,
    '-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-g', '25',
    '-b:v', '800k', '-c:a', 'aac', file]);
}

async function login(browser, who, viewport = { width: 1440, height: 1750 }) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(`${who}: ${error.message}`));
  page.on('console', message => { if (message.type() === 'error') errors.push(`${who}: ${message.text()}`); });
  await page.goto(`${origin}/accounts/login/`);
  await page.locator('input[name="login"]').fill(fixture[who].email);
  await page.locator('input[name="password"]').fill(fixture[who].password);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL(url => !url.pathname.startsWith('/accounts/login'));
  return { context, page, who };
}

async function pickFile(page, file) {
  const [chooser] = await Promise.all([
    page.waitForEvent('filechooser'),
    page.getByRole('button', { name: 'Upload version', exact: true }).first().click(),
  ]);
  await chooser.setFiles(file);
}

// The embedded FreeFrame viewer of the visible channel, signed in by the ticket (no login form).
async function playerFrame(page, versionNumber) {
  if (versionNumber) {
    // FreeFrame's id of the wanted version, from the same feed the page uses.
    const ffId = await page.evaluate(async number => {
      const root = document.getElementById('jrc-video');
      const data = await (await fetch(root.dataset.statusUrl, { credentials: 'same-origin' })).json();
      const panel = document.querySelector('[data-video-channel]:not(.hidden)');
      const card = data.channels.find(c => c.key === panel.dataset.videoChannel);
      return card.versions.find(v => v.number === number).ff_version_id;
    }, versionNumber);
    await page.waitForFunction(id => {
      const frame = document.querySelector('iframe[data-video-frame]:not(.hidden)');
      try { return frame && new URL(frame.contentWindow.location.href).searchParams.get('version') === id; } catch (e) { return false; }
    }, ffId, { timeout: 60000 });
  }
  const handle = await page.locator('iframe[data-video-frame]:visible').elementHandle();
  const frame = await handle.contentFrame();
  await frame.locator('video').first().waitFor({ state: 'visible', timeout: 60000 });
  await frame.waitForFunction(() => { const v = document.querySelector('video'); return v && v.duration > 1; }, null, { timeout: 60000 });
  const url = new URL(frame.url());
  assert(url.pathname.includes('/assets/'), `Přehrávač je na ${url.pathname}`);
  assert.equal(url.searchParams.get('embed'), '1');
  assert(!url.pathname.includes('/login'), 'V přehrávači se nesmí objevit přihlášení');
  assert.equal(await frame.getByLabel('Email address').count(), 0, 'Formulář přihlášení ve FreeFrame');
  if (versionNumber) {
    const label = await page.locator('[data-video-channel]:visible [data-video-select] option:checked').innerText();
    assert(label.startsWith(`v${versionNumber}`), `Vybraná verze: ${label}`);
  }
  return frame;
}

async function noOverflow(page, label) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true,
    `${label}: vodorovné posouvání`);
}

async function main() {
  const v1 = path.join(output, 'reel-v1-640x360-20s.mp4');
  const v2 = path.join(output, 'reel-v2-960x540-26s.mp4');
  makeVideo(v1, 'testsrc2', '640x360', 20);
  makeVideo(v2, 'testsrc', '960x540', 26);
  const browser = await chromium.launch({ headless: true });
  try {
    // --- tým nahraje dvě verze prvního kanálu ---
    const editor = await login(browser, 'editor');
    await editor.page.goto(tab);
    await editor.page.getByRole('button', { name: 'Upload version' }).first().waitFor();
    // Bez videa: žádný přehrávač, srozumitelná zpráva.
    await editor.page.getByText('No video yet. Use “Upload version” to add one.').first().waitFor();
    assert.equal(await editor.page.locator('iframe[data-video-frame]:visible').count(), 0);
    await editor.page.screenshot({ path: path.join(shots, 'empty-1440.png') });
    for (const [file, number] of [[v1, 1], [v2, 2]]) {
      await pickFile(editor.page, file);
      await editor.page.getByText(/Upload finished/).first().waitFor({ timeout: 60000 });
      await editor.page.locator(`[data-video-channel]:visible [data-version][data-status="ready"]`).nth(number - 1)
        .waitFor({ timeout: 240000 });
      console.log(`OK: v${number} nahrána a převedena`);
    }
    // Po nahrání se přehrávač objeví sám (bez znovunačtení stránky) a ukazuje nejnovější verzi.
    await playerFrame(editor.page, 2);
    console.log('OK: přehrávač se objevil sám po převodu, výchozí je poslední verze v2');
    await editor.context.close();

    // --- Ella (klient) otevře Video ---
    const ella = await login(browser, 'client');
    await ella.page.goto(tab);
    assert.equal(await ella.page.getByRole('button', { name: 'Upload version' }).count(), 0);
    assert.equal(await ella.page.locator('[data-video-final-toggle]:visible').count(), 0, 'Klient nemá tlačítko Final');
    let frame = await playerFrame(ella.page, 2);
    const duration2 = await frame.evaluate(() => document.querySelector('video').duration);
    assert(duration2 > 24 && duration2 < 28, `Délka v2: ${duration2}`);
    // Vložený režim: bez lišty FreeFrame, Share a New Version.
    for (const text of ['New Version', 'Share', 'Approve', 'Reject']) {
      assert.equal(await frame.getByText(text, { exact: true }).count(), 0, text);
    }
    // Výběr verze: v1 je kratší a menší, pak zpět na v2.
    await ella.page.locator('[data-video-channel]:visible [data-video-select]').selectOption({ label: 'v1' });
    frame = await playerFrame(ella.page, 1);
    const duration1 = await frame.evaluate(() => document.querySelector('video').duration);
    assert(duration1 > 18 && duration1 < 22, `Délka v1: ${duration1}`);
    await ella.page.locator('[data-video-channel]:visible [data-video-select]').selectOption({ label: 'v2' });
    frame = await playerFrame(ella.page, 2);
    console.log(`OK: výběr verze přepíná přehrávač (v1 ${duration1.toFixed(1)} s, v2 ${duration2.toFixed(1)} s)`);

    // Přehraje v2 a skočí na 0:17.
    await frame.locator('video').first().evaluate(async element => { element.muted = true; try { await element.play(); } catch (e) {} });
    await frame.waitForFunction(() => document.querySelector('video').currentTime > 0.3, null, { timeout: 30000 });
    await frame.locator('video').first().evaluate(element => { element.currentTime = 17; });
    await frame.waitForFunction(() => document.querySelector('video').currentTime >= 17, null, { timeout: 30000 });
    await frame.locator('video').first().evaluate(element => element.pause());
    // Časový kód přes obraz a u komentáře je ve tvaru m:ss, ne SMPTE.
    const overlay = await frame.getByTestId('video-timecode').innerText();
    assert(/^0:1[78]$/.test(overlay.trim()), `Časový kód přes obraz: ${overlay}`);
    const box = frame.getByPlaceholder('Leave your comment...');
    await box.click();
    await box.fill('zkrátit');
    await frame.getByTitle('Send (Enter)').click();
    // Sent: the box is empty again and the comment is in the list with its time.
    await frame.waitForFunction(() => document.querySelector('textarea') && !document.querySelector('textarea').value, null, { timeout: 20000 });
    await frame.getByText('zkrátit', { exact: true }).first().waitFor({ state: 'visible', timeout: 20000 });
    const panelText = await frame.locator('body').innerText();
    assert(/\b0:1[78]\b/.test(panelText), 'U komentáře chybí čas ve tvaru m:ss');
    assert(!/00:00:1[78]/.test(panelText), 'Čas je ve tvaru SMPTE');
    await ella.page.screenshot({ path: path.join(shots, 'ella-comment-1440.png'), fullPage: true });
    console.log(`OK: Ella přidala komentář k času ${overlay.trim()} (m:ss)`);

    // Ella na telefonu: stejná záložka, bez vodorovného posunu.
    const mobile = await ella.context.newPage();
    await mobile.setViewportSize({ width: 390, height: 2300 });
    await mobile.goto(tab);
    await playerFrame(mobile, 2);
    await noOverflow(mobile, 'Ella 390');
    await mobile.screenshot({ path: path.join(shots, 'ella-390.png'), fullPage: true });
    const mobileFrame = await playerFrame(mobile, 2);
    await mobileFrame.getByText('zkrátit', { exact: true }).first().waitFor({ state: 'attached', timeout: 20000 });
    assert.doesNotMatch(await mobileFrame.locator('body').innerText(), /\d\d:\d\d:\d\d:\d\d/, 'Na telefonu je čas ve tvaru SMPTE');
    await mobile.close();

    // Klient nesmí označit finální verzi ani přímým požadavkem.
    const versionIds = await ella.page.locator('[data-version]:visible').evaluateAll(rows => rows.map(r => r.dataset.version));
    const csrf = (await ella.context.cookies()).find(cookie => cookie.name === 'csrftoken').value;
    for (const id of versionIds) {
      const denied = await ella.context.request.post(`${base}versions/${id}/final/`, {
        headers: { 'X-CSRFToken': csrf, Origin: origin, Referer: origin + '/' }, data: {},
      });
      assert.equal(denied.status(), 403, 'Klient nesmí označit Final (403 na serveru)');
    }
    console.log('OK: klient dostává 403 na Final for publishing');
    await ella.context.close();

    // --- tým vidí komentář a označí Final ---
    const manager = await login(browser, 'manager');
    await manager.page.goto(tab);
    frame = await playerFrame(manager.page, 2);
    await frame.getByText('zkrátit', { exact: true }).first().waitFor({ state: 'visible', timeout: 20000 });
    assert(/\b0:1[78]\b/.test(await frame.locator('body').innerText()));
    await manager.page.screenshot({ path: path.join(shots, 'team-sees-comment-1440.png'), fullPage: true });
    console.log('OK: tým vidí komentář od Elly u 0:17');
    const toggle = manager.page.locator('[data-video-channel]:visible [data-video-final-toggle]');
    await toggle.waitFor({ state: 'visible' });
    assert.equal((await toggle.innerText()).trim(), 'Mark as final for publishing');
    await toggle.click();
    await manager.page.locator('[data-video-channel]:visible [data-video-final-badge]').waitFor({ state: 'visible' });
    await manager.page.locator('[data-video-channel]:visible [data-video-final-info]').getByText(/Final for publishing: v2/).waitFor();
    await manager.page.locator('[data-video-final-mark]:visible').first().waitFor();
    await manager.page.screenshot({ path: path.join(shots, 'final-marked-1440.png'), fullPage: true });
    // Přepnutí na v1: bez odznaku, ale informace o finální verzi zůstává.
    await manager.page.locator('[data-video-channel]:visible [data-video-select]').selectOption({ label: 'v1' });
    await playerFrame(manager.page, 1);
    assert.equal(await manager.page.locator('[data-video-channel]:visible [data-video-final-badge]').isVisible(), false);
    assert.match(await manager.page.locator('[data-video-channel]:visible [data-video-final-info]').innerText(), /v2/);
    // Přepínač kanálu: druhý kanál je bez videa, první se po návratu načte znovu.
    await manager.page.getByRole('tab', { name: /LinkedIn/ }).click();
    await manager.page.getByText('No video yet.').first().waitFor();
    assert.equal(await manager.page.locator('iframe[data-video-frame]:visible').count(), 0);
    await manager.page.screenshot({ path: path.join(shots, 'channel2-empty-1440.png') });
    await manager.page.getByRole('tab', { name: /Instagram/ }).click();
    await playerFrame(manager.page, 1);
    console.log('OK: tým označil v2 jako Final, přepínač kanálu a verze funguje');
    await manager.context.close();

    // --- Ella i viewer vidí Final, viewer nemá tlačítka ---
    const ella2 = await login(browser, 'client');
    await ella2.page.goto(tab);
    await playerFrame(ella2.page, 2);
    assert(/Final for publishing/.test(await ella2.page.locator('[data-video-channel]:visible [data-video-select] option:checked').innerText()));
    assert.equal(await ella2.page.locator('[data-video-final-badge]:visible').count(), 1);
    await ella2.page.screenshot({ path: path.join(shots, 'ella-sees-final-1440.png'), fullPage: true });
    await ella2.context.close();
    const viewer = await login(browser, 'viewer');
    await viewer.page.goto(tab);
    await playerFrame(viewer.page, 2);
    assert.equal(await viewer.page.getByRole('button', { name: 'Upload version' }).count(), 0);
    assert.equal(await viewer.page.locator('[data-video-final-toggle]:visible').count(), 0);
    assert.equal(await viewer.page.getByPlaceholder('Leave your comment...').count(), 0, 'Viewer nesmí komentovat');
    await viewer.context.close();
    console.log('OK: klient i viewer vidí Final; viewer jen čte');

    // --- chyby konzole: žádné CSP/CORS ---
    const violations = errors.filter(text => /Content Security Policy|Refused to (connect|frame|load)|CORS/i.test(text));
    assert.deepEqual(violations, [], 'CSP nebo CORS blokovalo záložku');
    console.log('OK: bez chyb CSP/CORS v konzoli');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); if (errors.length) console.error('Chyby stránky:', errors.slice(0, 10)); process.exit(1); });
