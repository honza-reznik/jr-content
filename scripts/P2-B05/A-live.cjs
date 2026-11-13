// P2-B05 oprava po review (R1–R5): živé ověření v Chromiu, jen vlastní projekt jrc-p2b05.
// Spouštět v Dockeru: timeout 900s node --max-old-space-size=2048 scripts/P2-B05/A-live.cjs <upload|check>
process.env.LANG = 'C.UTF-8';
process.env.LC_ALL = 'C.UTF-8';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { chromium } = require('playwright');

assert.equal(process.env.JRC_PROJECT, 'jrc-p2b05');
const phase = process.argv[2];
const origin = 'http://127.0.0.1:13905';
const root = `${origin}/jrc`;
const out = path.resolve('ops/test-output/P2-B05/A');
const shots = path.resolve('ops/screenshoty/sprint-feedback/P2-B05/A');
fs.mkdirSync(shots, { recursive: true });
const fixture = JSON.parse(fs.readFileSync(path.join(out, 'fixture.json'), 'utf8'));
const small = path.resolve('ops/test-output/P2-B05/Žlutý klip – original.MOV');
const big = path.join(out, 'Velký originál.MOV');
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const detail = tab => `${root}/workspace/${fixture.workspace}/content/${fixture.post}/?tab=${tab}`;
const DESKTOP = { width: 1440, height: 900 };
const MOBILE = { width: 390, height: 844 };
const results = [];
const ok = line => { results.push(line); console.log('OK', line); };

async function login(browser, who, viewport, tab = 'video') {
  const context = await browser.newContext({ viewport, acceptDownloads: true, isMobile: viewport === MOBILE, hasTouch: viewport === MOBILE });
  await context.route(/^https?:\/\/(?!127\.0\.0\.1:13905(?:\/|$))/, route => route.abort());
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  await page.goto(`${root}/accounts/login/`);
  await page.locator('[name=login]').fill(fixture[who].email);
  await page.locator('[name=password]').fill(fixture[who].password);
  await Promise.all([page.waitForURL(u => !u.pathname.includes('/accounts/login')), page.locator('button[type=submit]').click()]);
  await page.goto(detail(tab));
  return { page, context };
}

async function status(page) {
  const url = await page.locator('#jrc-video').getAttribute('data-status-url');
  const response = await page.request.get(origin + url);
  assert.equal(response.status(), 200);
  return (await response.json()).channels[0];
}

const noOverflow = page => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
const card = page => page.locator('[data-video-channel]:visible').first();

async function selectVersion(page, number) {
  const select = card(page).locator('[data-video-select]');
  const value = await select.evaluate((el, n) =>
    [...el.options].find(o => o.textContent === `v${n}` || o.textContent.startsWith(`v${n} `)).value, number);
  await select.selectOption(value);
  await page.waitForTimeout(300);
}

async function downloadVia(page, linkName) {
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 120000 }),
    card(page).getByRole('link', { name: linkName, exact: true }).click(),
  ]);
  const file = await download.path();
  return { name: download.suggestedFilename(), sha: crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex') };
}

async function upload(browser) {
  const { page, context } = await login(browser, 'editor', DESKTOP);
  const zone = card(page).locator('[data-video-drop]');
  assert(await zone.isVisible(), 'desktop shows the drop area');
  await zone.scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(shots, 'desktop-owner-drop-area.png') });
  // v1: a real drag of the small MOV, v2: the large MOV through Upload version.
  const base64 = fs.readFileSync(small).toString('base64');
  await card(page).evaluate((element, { base64, name }) => {
    const bytes = Uint8Array.from(atob(base64), c => c.charCodeAt(0));
    const transfer = new DataTransfer();
    transfer.items.add(new File([bytes], name, { type: 'video/quicktime' }));
    element.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer }));
  }, { base64, name: path.basename(small) });
  for (let i = 0; i < 120 && !(await status(page)).versions.some(v => v.number === 1 && v.status === 'ready'); i++) await page.waitForTimeout(2000);
  await card(page).locator('[data-video-file]').setInputFiles(big);
  for (let i = 0; i < 240 && !(await status(page)).versions.some(v => v.number === 2 && v.status === 'ready'); i++) await page.waitForTimeout(2500);
  const channel = await status(page);
  assert.deepEqual(channel.versions.map(v => [v.number, v.status]).sort(), [[1, 'ready'], [2, 'ready']]);
  fs.writeFileSync(path.join(out, 'versions.json'), JSON.stringify(channel.versions.map(v => ({ number: v.number, id: v.id, download_url: v.download_url, filename: v.filename })), null, 2));
  const state = await context.storageState();
  const session = state.cookies.find(c => c.name.endsWith('sessionid'));
  fs.writeFileSync(path.join(out, 'owner-cookie.txt'), `${session.name}=${session.value}`, { mode: 0o600 });
  ok('owner desktop: drop area visible, v1 by drag and v2 (120 MB) by Upload version are ready');
  await context.close();
}

async function check(browser) {
  const versions = JSON.parse(fs.readFileSync(path.join(out, 'versions.json'), 'utf8'));
  const url = n => origin + versions.find(v => v.number === n).download_url;

  // Owner (team with the download right): every version, link and download, desktop and mobile.
  {
    const { page, context } = await login(browser, 'editor', DESKTOP);
    await selectVersion(page, 1);
    const got = await downloadVia(page, 'Download version');
    assert.equal(got.name, path.basename(small));
    assert.equal(got.sha, sha(small));
    await card(page).locator('[data-video-toolbar]').scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(shots, 'desktop-owner-v1-download.png') });
    await selectVersion(page, 2);
    assert(await card(page).getByRole('link', { name: 'Download version', exact: true }).isVisible());
    assert(await noOverflow(page));
    ok(`owner desktop: Download version v1 = original "${got.name}", SHA-256 ${got.sha}; v2 link shown`);
    await context.close();
  }
  {
    const { page, context } = await login(browser, 'editor', MOBILE);
    assert(!(await card(page).locator('[data-video-drop]').isVisible()), 'phone hides the drop area');
    assert(await card(page).getByRole('button', { name: 'Upload version' }).isVisible());
    await selectVersion(page, 1);
    assert(await card(page).getByRole('link', { name: 'Download version', exact: true }).isVisible());
    assert(await noOverflow(page));
    await page.screenshot({ path: path.join(shots, 'mobile-owner-video.png'), fullPage: true });
    ok('owner mobile: no drop area, Upload version and Download version visible, no horizontal overflow');
    await context.close();
  }

  // Client: only the approved final (v2), never the working v1.
  for (const viewport of [DESKTOP, MOBILE]) {
    const { page, context } = await login(browser, 'client', viewport);
    const link = card(page).getByRole('link', { name: 'Download version', exact: true });
    await selectVersion(page, 1);
    assert(!(await link.isVisible()), 'client: no link for a working version');
    assert.equal((await page.request.get(url(1), { maxRedirects: 0 })).status(), 403);
    await selectVersion(page, 2);
    assert(await link.isVisible(), 'client: link for the approved final');
    assert(await noOverflow(page));
    const tag = viewport === DESKTOP ? 'desktop' : 'mobile';
    await card(page).locator('[data-video-toolbar]').scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(shots, `${tag}-client-final.png`) });
    if (viewport === DESKTOP) {
      const got = await downloadVia(page, 'Download version');
      assert.equal(got.name, path.basename(big));
      assert.equal(got.sha, sha(big));
      ok(`client desktop: v1 hidden + 403, final v2 = original "${got.name}" (120 MB), SHA-256 ${got.sha}`);
    } else {
      ok('client mobile: v1 hidden + 403, final v2 link shown, no horizontal overflow');
    }
    await context.close();
  }

  // Viewer: nothing, as with Download final video before.
  {
    const { page, context } = await login(browser, 'viewer', DESKTOP);
    for (const n of [1, 2]) {
      await selectVersion(page, n);
      assert(!(await card(page).getByRole('link', { name: 'Download version', exact: true }).isVisible()));
      assert.equal((await page.request.get(url(n), { maxRedirects: 0 })).status(), 403);
    }
    await page.screenshot({ path: path.join(shots, 'desktop-viewer-no-download.png') });
    ok('viewer desktop: no Download version link, v1 and v2 endpoint 403');
    await context.close();
  }

  // Comment visibility names (R3), owner and client, desktop and mobile.
  {
    const { page, context } = await login(browser, 'editor', DESKTOP, 'comments');
    for (const [visibility, body] of [['internal', 'Team only: check the colour grade before Friday.'],
      ['external', 'For Ella: the second cut has the new intro, please have a look when you can.']]) {
      await page.locator('#new-comment').fill(body);
      await page.locator('select[name=visibility]').selectOption(visibility);
      await Promise.all([page.waitForURL(/tab=comments/), page.getByRole('button', { name: 'Add comment' }).click()]);
    }
    const text = await page.locator('main').innerText();
    assert(text.includes('Team only comments are not visible to the client.'));
    assert.equal(await page.locator('span', { hasText: /^Team and client$/ }).count(), 1);
    assert.equal(await page.locator('span', { hasText: /^Team only$/ }).count(), 1);
    assert(!/\bShared\b|Internal comments/.test(text));
    await page.screenshot({ path: path.join(shots, 'desktop-owner-comments.png'), fullPage: true });
    await page.setViewportSize(MOBILE);
    assert(await noOverflow(page));
    await page.screenshot({ path: path.join(shots, 'mobile-owner-comments.png'), fullPage: true });
    ok('owner comments desktop + mobile: Team only / Team and client labels and hint, no Shared/Internal');
    await context.close();
  }
  {
    const { page, context } = await login(browser, 'client', MOBILE, 'comments');
    const text = await page.locator('main').innerText();
    assert(!text.includes('check the colour grade'), 'client never sees a team-only comment');
    assert.equal(await page.locator('span', { hasText: /^Team and client$/ }).count(), 1);
    assert(await noOverflow(page));
    await page.screenshot({ path: path.join(shots, 'mobile-client-comments.png'), fullPage: true });
    ok('client comments mobile: only the Team and client comment, labelled so');
    await context.close();
  }
  fs.writeFileSync(path.join(out, 'live-check.json'), JSON.stringify(results, null, 2));
}

// Screens of scrolled areas (the app scrolls inside its main panel, so fullPage stops at the top).
async function shotsOnly(browser) {
  for (const [who, viewport, tab, name, target] of [
    ['editor', MOBILE, 'video', 'mobile-owner-video-card.png', '[data-video-channel]:visible'],
    ['editor', DESKTOP, 'comments', 'desktop-owner-comment-list.png', 'ol li'],
    ['editor', MOBILE, 'comments', 'mobile-owner-comment-list.png', 'ol li'],
    ['client', MOBILE, 'comments', 'mobile-client-comment-list.png', 'ol li'],
    ['client', DESKTOP, 'comments', 'desktop-client-comment-list.png', 'ol li'],
  ]) {
    const { page, context } = await login(browser, who, viewport, tab);
    await page.locator(target).first().scrollIntoViewIfNeeded();
    await page.locator(target).first().evaluate(el => el.scrollIntoView({ block: 'start' }));
    assert(await noOverflow(page));
    await page.screenshot({ path: path.join(shots, name) });
    await context.close();
  }
  ok('screens of the video card and comment lists, desktop and mobile');
}

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--disable-dev-shm-usage'] });
  try {
    if (phase === 'upload') await upload(browser);
    else if (phase === 'check') await check(browser);
    else if (phase === 'shots') await shotsOnly(browser);
    else throw new Error('phase: upload | check');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
