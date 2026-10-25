// F7-B02: schválení klientem v prohlížeči. Jen Docker, vlastní projekt jrc-f7b02, vymyšlené účty.
// Scénář (dvoustupňový režim): tým nahraje a označí finální video; Ella (klient) vidí obsah v Review, ale
// rozhoduje až po týmu; tým schválí; Ella má obsah v "Waiting for you", vidí scénář/popisek/video, v přehrávači
// FreeFrame nevidí interní komentář týmu a nemá přepínač Internal; požádá o změny; tým uloží novou verzi
// scénáře a pošle znovu do Review; tým schválí; Ella schválí. 1440 i 390 px. Videa vyrábí FFmpeg lokálně.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');

assert.equal(process.env.JRC_PROJECT, 'jrc-f7b02');
assert.equal(process.env.JRC_APP_PORT, '13373');
const origin = `http://127.0.0.1:${process.env.JRC_APP_PORT}`;
const output = path.resolve('ops/test-output/F7-B02');
const shots = path.resolve('ops/screenshoty/F7-B02');
fs.mkdirSync(output, { recursive: true });
fs.mkdirSync(shots, { recursive: true });
const fixture = JSON.parse(fs.readFileSync(path.join(output, 'fixture.json'), 'utf8'));
const detail = `${origin}/workspace/${fixture.workspace}/content/${fixture.post}/`;
const approvalTab = `${detail}?tab=approval`;
const videoTab = `${detail}?tab=video`;
const errors = [];
const phase = process.argv[2] || '1';
assert(['1', '2'].includes(phase), 'Fáze 1 nebo 2');

function makeVideo(file, seconds) {
  if (fs.existsSync(file)) return;
  execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y',
    '-f', 'lavfi', '-i', `testsrc2=size=640x360:rate=25:duration=${seconds}`,
    '-f', 'lavfi', '-i', `sine=frequency=440:sample_rate=48000:duration=${seconds}`,
    '-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-g', '25',
    '-b:v', '800k', '-c:a', 'aac', file]);
}

async function login(browser, who, viewport = { width: 1440, height: 2100 }) {
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

async function noOverflow(page, label) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true,
    `${label}: vodorovné posouvání`);
}

async function playerFrame(page) {
  const handle = await page.locator('iframe[data-video-frame]:visible').elementHandle();
  const frame = await handle.contentFrame();
  await frame.locator('video').first().waitFor({ state: 'visible', timeout: 60000 });
  await frame.waitForFunction(() => { const v = document.querySelector('video'); return v && v.duration > 1; }, null, { timeout: 60000 });
  assert.equal(await frame.getByLabel('Email address').count(), 0, 'Formulář přihlášení ve FreeFrame');
  return frame;
}

async function text(page) { return page.locator('main, body').first().innerText(); }

async function main() {
  const video = path.join(output, 'reel-640x360-12s.mp4');
  makeVideo(video, 12);
  const browser = await chromium.launch({ headless: true });
  try {
    if (phase === '1') {
      // --- tým nahraje video a tým ho označí jako finální ---
      const editor = await login(browser, 'editor');
      await editor.page.goto(videoTab);
      const [chooser] = await Promise.all([
        editor.page.waitForEvent('filechooser'),
        editor.page.getByRole('button', { name: 'Upload version', exact: true }).first().click(),
      ]);
      await chooser.setFiles(video);
      await editor.page.getByText(/Upload finished/).first().waitFor({ timeout: 60000 });
      await editor.page.locator('[data-video-channel]:visible [data-version][data-status="ready"]').first().waitFor({ timeout: 240000 });
      await editor.context.close();
      const manager = await login(browser, 'manager');
      await manager.page.goto(videoTab);
      await playerFrame(manager.page);
      await manager.page.locator('[data-video-channel]:visible [data-video-final-toggle]').click();
      await manager.page.locator('[data-video-channel]:visible [data-video-final-badge]').waitFor({ state: 'visible' });
      console.log('OK: video nahráno a označeno jako Final for publishing');

      // --- Ella: obsah je v Review, ale tým rozhoduje první ---
      const ella = await login(browser, 'client');
      await ella.page.goto(`${origin}/board/`);
      assert.equal(await ella.page.locator('[data-waiting-for-you]').count(), 0, 'Ella nemá nic čekat, tým ještě neschválil');
      await ella.page.goto(approvalTab);
      let body = await text(ella.page);
      assert(body.includes('SCENE ONE: Ella waters the roses at dawn.'), 'Scénář na kartě');
      assert(body.includes('Roses at dawn. A slow morning in the garden. #garden'), 'Popisek na kartě');
      assert(/Final for publishing\s+version 1/.test(body), 'Finální video na kartě');
      assert(body.includes('The team is checking this first'), 'Stavová věta');
      assert.equal(await ella.page.locator('[data-approval-decision]').count(), 0, 'Ella nesmí rozhodovat před týmem');
      assert(!body.includes('TEAM ONLY'), 'Interní poznámka');
      await ella.page.screenshot({ path: path.join(shots, '01-ella-team-first-1440.png'), fullPage: true });
      // Přímý požadavek před týmem: 403 na serveru.
      const csrf = (await ella.context.cookies()).find(c => c.name === 'csrftoken').value;
      const early = await ella.context.request.post(`${detail}approval/`, {
        headers: { 'X-CSRFToken': csrf, Origin: origin, Referer: origin + '/' },
        form: { decision: 'approve', expected_stage: 'review' }, maxRedirects: 0,
      });
      assert.equal(early.status(), 403, 'Klient před týmem dostává 403');
      console.log('OK: Ella před týmem nemůže rozhodnout (stránka bez tlačítek, přímý požadavek 403)');

      // --- tým schválí na kartě Approval ---
      await manager.page.goto(approvalTab);
      assert((await text(manager.page)).includes('The client approves after you'), 'Tým ví, že klient je druhý');
      await manager.page.getByRole('button', { name: 'Approve', exact: true }).click();
      await manager.page.getByText('Approved by the team. It now waits for the client').first().waitFor();
      await manager.page.screenshot({ path: path.join(shots, '02-team-approved-1440.png'), fullPage: true });
      console.log('OK: tým schválil jako první krok');

      // --- interní komentář týmu ve FreeFrame: tým ho napíše, Ella ho nevidí a nemá přepínač ---
      await manager.page.goto(videoTab);
      let frame = await playerFrame(manager.page);
      const box = frame.getByPlaceholder('Leave your comment...');
      await box.click();
      await box.fill('TEAM ONLY check the music licence');
      await frame.getByRole('button', { name: /^Public$/ }).click();
      await frame.getByRole('button', { name: /^Internal$/ }).click();
      await frame.getByTitle('Send (Enter)').click();
      await frame.getByText('TEAM ONLY check the music licence').first().waitFor({ state: 'visible', timeout: 20000 });
      await manager.page.screenshot({ path: path.join(shots, '03-team-internal-comment-1440.png'), fullPage: true });
      await manager.context.close();

      // --- Ella: Waiting for you ---
      await ella.page.goto(`${origin}/board/`);
      const waiting = ella.page.locator('[data-waiting-for-you]');
      await waiting.waitFor();
      const waitingText = await waiting.innerText();
      assert(waitingText.includes('Ella garden reel'), 'Waiting for you');
      assert(!waitingText.includes('Foreign reel'), 'Cizí klient v Waiting for you');
      assert(!(await text(ella.page)).includes('Foreign reel'), 'Cizí obsah na nástěnce Elly');
      await ella.page.screenshot({ path: path.join(shots, '04-ella-waiting-1440.png'), fullPage: true });
      await waiting.getByRole('link', { name: 'Review and decide' }).click();
      await ella.page.waitForURL(/tab=approval/);
      await ella.page.locator('[data-approval-decision]').waitFor();
      assert((await text(ella.page)).includes('Waiting for your decision.'));
      await ella.page.screenshot({ path: path.join(shots, '05-ella-approval-1440.png'), fullPage: true });
      // Video v přehrávači: Ella vidí veřejné komentáře, ne interní, a nemá přepínač Internal.
      await ella.page.goto(videoTab);
      frame = await playerFrame(ella.page);
      const ellaBox = frame.getByPlaceholder('Leave your comment...');
      await ellaBox.click();
      assert.equal(await frame.getByRole('button', { name: /^(Public|Internal)$/ }).count(), 0, 'Ella nesmí vidět přepínač Internal');
      await ellaBox.fill('Lovely, but shorten the ending');
      await frame.getByTitle('Send (Enter)').click();
      await frame.getByText('Lovely, but shorten the ending').first().waitFor({ state: 'visible', timeout: 20000 });
      assert.equal(await frame.getByText('TEAM ONLY check the music licence').count(), 0, 'Interní komentář týmu Ella nesmí vidět');
      await ella.page.screenshot({ path: path.join(shots, '06-ella-video-no-internal-1440.png'), fullPage: true });
      console.log('OK: Ella v přehrávači nevidí interní komentář a nemá přepínač; svůj komentář je veřejný');

      // --- Ella na telefonu ---
      const mobile = await ella.context.newPage();
      await mobile.setViewportSize({ width: 390, height: 2200 });
      await mobile.goto(`${origin}/board/`);
      await mobile.locator('[data-waiting-for-you]').waitFor();
      await noOverflow(mobile, 'nástěnka 390');
      await mobile.screenshot({ path: path.join(shots, '07-ella-waiting-390.png'), fullPage: true });
      await mobile.goto(approvalTab);
      await mobile.locator('[data-approval-decision]').waitFor();
      await noOverflow(mobile, 'karta Approval 390');
      await mobile.screenshot({ path: path.join(shots, '08-ella-approval-390.png'), fullPage: true });
      await mobile.close();

      // --- Ella požádá o změny ---
      await ella.page.goto(approvalTab);
      await ella.page.getByLabel(/What should change/).fill('Please shorten the ending to ten seconds.');
      await ella.page.getByRole('button', { name: 'Request changes', exact: true }).click();
      await ella.page.getByText('Changes requested. The team can see your comment.').first().waitFor();
      body = await text(ella.page);
      assert(body.includes('Changes were requested'), 'Stav po žádosti');
      assert(body.includes('Please shorten the ending to ten seconds.'), 'Poslední rozhodnutí');
      assert.equal(await ella.page.locator('[data-approval-decision]').count(), 0);
      await ella.page.screenshot({ path: path.join(shots, '09-ella-changes-requested-1440.png'), fullPage: true });
      await ella.page.goto(`${origin}/board/`);
      assert.equal(await ella.page.locator('[data-waiting-for-you]').count(), 0, 'Po žádosti o změny nic nečeká');
      console.log('OK: Ella požádala o změny, nic na ni nečeká');

      await ella.context.close();
    }
    if (phase === '2') {
      const ella = await login(browser, 'client');
      let body;
      const manager2 = await login(browser, 'manager');
      await manager2.page.goto(approvalTab);
      await manager2.page.locator('[data-approval-latest]').getByText('Please shorten the ending to ten seconds.').waitFor();
      await manager2.page.getByRole('button', { name: 'Approve', exact: true }).click();
      await manager2.page.getByText('Approved by the team').first().waitFor();
      await manager2.page.goto(`${detail}?tab=history`);
      const teamHistory = await text(manager2.page);
      assert(teamHistory.includes('Please shorten the ending to ten seconds.'), 'Historie týmu: komentář Elly');
      assert(teamHistory.includes('Ella') && teamHistory.includes('Changes Requested'), 'Historie týmu: kdo');
      await manager2.page.screenshot({ path: path.join(shots, '10-team-history-1440.png'), fullPage: true });
      await manager2.context.close();

      // --- Ella schválí novou verzi ---
      await ella.page.goto(approvalTab);
      body = await text(ella.page);
      assert(body.includes('a short, quiet ending'), 'Nová verze scénáře na kartě');
      assert(body.includes('Waiting for your decision.'));
      await ella.page.screenshot({ path: path.join(shots, '11-ella-new-version-1440.png'), fullPage: true });
      await ella.page.getByRole('button', { name: 'Approve', exact: true }).click();
      await ella.page.getByText('Approved. Thank you.').first().waitFor();
      assert.equal(await ella.page.locator('[data-approval-decision]').count(), 0);
      await ella.page.screenshot({ path: path.join(shots, '12-ella-approved-1440.png'), fullPage: true });
      await ella.page.goto(`${detail}?tab=history`);
      const clientHistory = await text(ella.page);
      assert(clientHistory.includes('Please shorten the ending to ten seconds.'), 'Historie Elly: její komentář');
      assert(/Approved/.test(clientHistory) && /Changes Requested/.test(clientHistory));
      await ella.page.screenshot({ path: path.join(shots, '13-ella-history-1440.png'), fullPage: true });
      console.log('OK: Ella po nové verzi schválila, historie ukazuje kdo a kdy');
      await ella.context.close();

      // --- viewer vidí kartu bez tlačítek, přímý požadavek 403 ---
      const viewer = await login(browser, 'viewer');
      await viewer.page.goto(approvalTab);
      assert.equal(await viewer.page.locator('[data-approval-decision]').count(), 0);
      const vcsrf = (await viewer.context.cookies()).find(c => c.name === 'csrftoken').value;
      const denied = await viewer.context.request.post(`${detail}approval/`, {
        headers: { 'X-CSRFToken': vcsrf, Origin: origin, Referer: origin + '/' },
        form: { decision: 'request_changes', comment: 'x', expected_stage: 'approved' }, maxRedirects: 0,
      });
      assert.equal(denied.status(), 403, 'Viewer dostává 403');
      await viewer.context.close();
      console.log('OK: viewer jen čte, přímý požadavek 403');

    }
    const violations = errors.filter(t => /Content Security Policy|Refused to (connect|frame|load)|CORS/i.test(t));
    assert.deepEqual(violations, [], 'CSP nebo CORS blokovalo stránku');
    console.log('OK: bez chyb CSP/CORS v konzoli');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); if (errors.length) console.error('Chyby stránky:', errors.slice(0, 10)); process.exit(1); });
