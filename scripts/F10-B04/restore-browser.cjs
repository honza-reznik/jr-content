// Ověření aplikace po obnovení skutečného průchodu Reel pro Ellu.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const appPort = process.env.JRC_APP_PORT;
const s3Port = process.env.JRC_S3_PORT;
assert.equal(process.env.JRC_PROJECT, process.env.JRC_EXPECTED_PROJECT);
assert.equal(appPort, '13483');
assert.equal(s3Port, '14203');
const origin = process.env.JRC_PUBLIC_ORIGIN;
const basePath = process.env.JRC_BASE_PATH ?? "/jrc";
const app = origin + basePath;
const fixture = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const screenshot = path.resolve(process.argv[3]);
const title = 'Reel pro Ellu — ranní zahrada';
const caption = 'Roses at dawn. A slow morning in Ella’s garden. 🌱 #garden New ending.';
const scenario = 'SCENE TWO: a short, quiet ending.';
const browserErrors = [];

async function login(browser, who) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1200 }, timezoneId: 'Europe/Prague' });
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (['http:', 'https:'].includes(url.protocol) && url.origin !== origin) {
      browserErrors.push(`Externí požadavek: ${url.href}`);
      return route.abort('blockedbyclient');
    }
    return route.continue();
  });
  const page = await context.newPage();
  page.on('pageerror', error => browserErrors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') browserErrors.push(message.text()); });
  await page.goto(`${app}/accounts/login/`);
  await page.locator('input[name="login"]').fill(fixture[who].email);
  await page.locator('input[name="password"]').fill(fixture[who].password);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL(url => !url.pathname.startsWith(basePath + '/accounts/login'));
  return { context, page };
}

async function assertFinalVersion(page, who) {
  const select = page.locator('[data-video-channel]:visible [data-video-select]').first();
  await select.waitFor({ state: 'visible' });
  const label = await select.locator('option:checked').textContent();
  assert(label?.startsWith('v2'), `${who} nevidí vybranou verzi v2: ${label}`);
}

async function selectVersion(page, version, who) {
  const select = page.locator('[data-video-channel]:visible [data-video-select]').first();
  await select.waitFor({ state: 'visible' });
  const value = await select.locator('option').evaluateAll((options, prefix) =>
    options.find(option => option.textContent?.startsWith(prefix))?.value || null, `v${version}`);
  assert(value, `${who} nemá verzi v${version}`);
  await select.selectOption(value);
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    const team = await login(browser, 'admin');
    await team.page.goto(`${app}/board/`);
    const publishedCard = team.page.locator('.board-column[data-stage="published"] .board-card')
      .filter({ has: team.page.getByRole('heading', { name: title, exact: true }) });
    await publishedCard.waitFor();
    const postId = await publishedCard.getAttribute('data-post');
    assert(postId, 'Obnovený obsah Published nemá identifikátor');
    const detail = `${app}/workspace/${fixture.workspace}/content/${postId}/`;

    await team.page.goto(`${detail}?tab=history`);
    const history = await team.page.locator('main').innerText();
    assert(history.includes('Changes Requested') && history.includes('Marked Instagram as published'), 'Historie E2E chybí');
    await team.page.goto(`${detail}?tab=caption`);
    assert((await team.page.locator('main').innerText()).includes(caption), 'Finální popisek se neobnovil');
    await team.page.goto(`${detail}?tab=script`);
    await team.page.waitForFunction(text => {
      const editor = document.querySelector('#script-editor [role="textbox"][aria-label="Script text"]');
      return editor && editor.innerText.includes(text);
    }, scenario, { timeout: 30000 });
    assert((await team.page.locator('[role="tabpanel"]').innerText()).includes(scenario), 'Scénář v2 se neobnovil');
    await team.page.goto(`${detail}?tab=publish`);
    await team.page.getByText('Published manually', { exact: true }).waitFor();
    const [download] = await Promise.all([
      team.page.waitForEvent('download'),
      team.page.getByRole('link', { name: 'Download final video' }).click(),
    ]);
    const downloadedPath = await download.path();
    assert(downloadedPath, 'Finální video se nestáhlo');
    const bytes = await fs.promises.readFile(downloadedPath);
    assert(bytes.length > 12 && bytes.subarray(4, 8).toString('ascii') === 'ftyp', 'Stažený soubor není MP4');
    await team.page.goto(`${detail}?tab=video`);
    await assertFinalVersion(team.page, 'Tým');
    const teamFrame = await (await team.page.locator('iframe[data-video-frame]:visible').elementHandle()).contentFrame();
    await teamFrame.locator('video').first().waitFor({ state: 'visible' });
    await teamFrame.waitForFunction(() => document.querySelector('video')?.duration > 1);
    await teamFrame.locator('video').first().evaluate(video => { video.muted = true; video.play(); });
    await teamFrame.waitForFunction(() => document.querySelector('video')?.currentTime > 0.5);
    await teamFrame.locator('video').first().evaluate(video => video.pause());
    await team.page.screenshot({ path: screenshot, fullPage: true });
    await team.context.close();

    const ella = await login(browser, 'client');
    await ella.page.goto(`${app}/board/`);
    await ella.page.locator('.board-column[data-stage="published"]')
      .getByRole('heading', { name: title, exact: true }).waitFor();
    await ella.page.goto(`${detail}?tab=video`);
    // Komentář ověřuje test databáze hned po obnově; zde ověřujeme přehrání v1.
    await selectVersion(ella.page, 1, 'Ella');
    const ellaFrame = await (await ella.page.locator('iframe[data-video-frame]:visible').elementHandle()).contentFrame();
    await ellaFrame.waitForFunction(() => document.querySelector('video')?.duration > 1);
    await ellaFrame.waitForFunction(() => document.querySelector('video')?.duration > 1);
    await ellaFrame.locator('video').first().evaluate(video => { video.muted = true; video.play(); });
    await ellaFrame.waitForFunction(() => document.querySelector('video')?.currentTime > 0.5);
    await ellaFrame.locator('video').first().evaluate(video => video.pause());
    await ella.page.screenshot({ path: screenshot.replace(/\.png$/, '-ella.png'), fullPage: true });
    assert.deepEqual(browserErrors, [], `Chyby prohlížeče: ${browserErrors.slice(0, 5).join(' | ')}`);
    await ella.context.close();
    console.log('OK obnovený E2E obsah: Published, historie, popisek, scénář v2, přehrání videa v2, stažení MP4, obnovu komentáře klientky (ověřeno v DB).');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exit(1); });
