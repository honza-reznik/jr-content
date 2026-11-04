// P1-B13: skutečné klikání v Chromiu, výhradně vlastní lokální Docker projekt.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');

assert.equal(process.env.JRC_PROJECT, 'jrc-p1b13');
const origin = `http://127.0.0.1:${process.env.JRC_APP_PORT}`;
const root = origin + '/jrc';
const output = path.resolve('ops/test-output/P1-B13');
const shots = path.resolve('ops/screenshoty/P1-B13');
fs.mkdirSync(shots, {recursive: true});
const fixture = JSON.parse(fs.readFileSync(path.join(output, 'fixture.json')));
const detail = `${root}/workspace/${fixture.workspace}/content/${fixture.post}/`;
const errors = [];
const runId = Date.now();
async function login(browser, role, width = 1440) {
  const context = await browser.newContext({viewport: {width, height: width === 390 ? 844 : 900}});
  await context.route(/^https?:/, route => {
    const url = new URL(route.request().url());
    return url.hostname === '127.0.0.1' && [process.env.JRC_APP_PORT, process.env.JRC_S3_PORT].includes(url.port)
      ? route.continue() : route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  page.on('pageerror', error => errors.push(`${role}: ${error.message}`));
  page.on('console', message => { if (message.type() === 'error') console.error(role, message.text()); });
  page.on('response', response => { if (response.status() >= 400) console.error(response.status(), new URL(response.url()).pathname); });
  page.on('requestfailed', request => {
    if (request.failure()?.errorText !== 'net::ERR_ABORTED') console.error('Požadavek selhal:', new URL(request.url()).pathname, request.failure()?.errorText);
  });
  if (process.env.JRC_HLS_DIAGNOSTIC === '1') page.on('response', async response => {
    if (new URL(response.url()).pathname.endsWith('.m3u8')) {
      try { console.error('HLS:', response.status(), response.headers()['content-type'], (await response.text()).split('\n').map(line => line.split('?')[0]).join('\n')); } catch {}
    }
  });
  await page.goto(root + '/accounts/login/');
  await page.locator('[name=login]').fill(fixture[role].email);
  await page.locator('[name=password]').fill(fixture[role].password);
  await page.locator('button[type=submit]').click();
  await page.waitForURL(url => !url.pathname.includes('/accounts/login/'));
  return {context, page};
}
async function snap(page, name) {
  await page.screenshot({path: path.join(shots, name + '.png'), fullPage: true});
}
async function openTab(page, tab) {
  await page.goto(detail + '?tab=' + tab);
  await page.locator(`[aria-label="Content sections"] [role=tab][aria-selected=true]`).waitFor();
}
async function frame(page) {
  const handle = await page.locator('iframe[data-video-frame]:visible').elementHandle();
  const view = await handle.contentFrame();
  await view.locator('video').first().waitFor({timeout: 90000});
  try {
    await view.waitForFunction(() => document.querySelector('video')?.duration > 1, null, {timeout: 45000});
  } catch (error) {
    await snap(page, 'diagnostic-video');
    console.error(await view.locator('body').innerText());
    console.error(await view.locator('video').first().evaluate(video => ({network: video.networkState, ready: video.readyState, error: video.error?.message})));
    throw error;
  }
  return view;
}
async function history(page, name) {
  await openTab(page, 'history');
  for (const label of ['Uploaded video v1', 'Uploaded video v2', 'Marked video as final', 'Removed final video mark']) {
    await page.getByText(label, {exact: false}).first().waitFor();
  }
  await snap(page, name);
}
async function navigation(page, role) {
  await page.setViewportSize({width: 390, height: 844});
  await page.evaluate(() => localStorage.setItem('sidebarCollapsed', 'true'));
  for (const label of ['Content board', 'Publish', 'Notifications']) {
    await openTab(page, 'history');
    const menu = page.getByRole('button', {name: 'Open navigation', exact: true});
    await menu.click();
    assert.equal(await menu.getAttribute('aria-expanded'), 'true');
    const link = page.locator('#jrc-navigation').getByRole('link', {name: label, exact: true});
    await snap(page, `${role}-menu-${label.replaceAll(' ', '-')}-390`);
    await link.click();
    await page.waitForFunction(() => document.querySelector('[aria-label="Open navigation"]')?.getAttribute('aria-expanded') === 'false');
    assert.equal(await page.getByRole('button', {name: 'Open navigation'}).getAttribute('aria-expanded'), 'false');
  }
  await openTab(page, 'history');
  const menu = page.getByRole('button', {name: 'Open navigation'});
  await menu.click();
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => document.querySelector('[aria-label="Open navigation"]')?.getAttribute('aria-expanded') === 'false');
  assert.equal(await menu.getAttribute('aria-expanded'), 'false');
  assert.equal(await page.evaluate(() => localStorage.getItem('sidebarCollapsed')), 'true', 'AK2 zachová desktopovou volbu navigace');
  const active = page.locator('[data-tabs-scroll] [aria-selected=true]');
  const tabBox = await active.boundingBox();
  const navBox = await page.locator('[data-tabs-scroll]').boundingBox();
  assert(tabBox.x >= navBox.x && tabBox.x + tabBox.width <= navBox.x + navBox.width + 1, 'AK7 aktivní záložka je vidět');
  assert(await page.getByRole('button', {name: 'Previous content sections'}).isVisible(), 'AK7 šipka doleva');
  await page.getByRole('button', {name: 'Previous content sections'}).click();
  await page.waitForFunction(() => document.querySelector('[data-tabs-scroll]').scrollLeft < 50);
  assert(await page.getByRole('button', {name: 'More content sections'}).isVisible(), 'AK7 šipka doprava');
  await snap(page, `${role}-tabs-390`);
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Stránka nepřetéká vodorovně');
  console.log(`AK2, AK7: ${role}, menu, tři odkazy, Escape, posuv záložek 390 px OK`);
}
async function mobileVideo(page, role) {
  await page.setViewportSize({width: 390, height: 844});
  await openTab(page, 'video');
  const view = await frame(page);
  await view.getByPlaceholder('Leave your comment...').waitFor();
  // Keep the same transport control while its label changes to Pause and back.
  const play = view.locator('button[aria-label="Play"], button[aria-label="Pause"]').first();
  await page.locator('iframe[data-video-frame]:visible').scrollIntoViewIfNeeded();
  await view.waitForFunction(() => {
    const control = document.querySelector('button[aria-label="Play"], button[aria-label="Pause"]');
    const sheet = document.querySelector('[data-testid="mobile-comment-sheet"]');
    return control && sheet && control.getBoundingClientRect().bottom <= sheet.getBoundingClientRect().top + 1;
  });
  await play.click(); // Skutečný stisk, žádné volání video.play() v testu.
  await view.waitForFunction(() => document.querySelector('video').currentTime > 0.2);
  await view.locator('video').first().evaluate(video => video.pause());
  await snap(page, `${role}-video-comments-390`);
  console.log(`AK3: ${role}, Play lze stisknout s otevřeným panelem Comments 390 px OK`);
  return view;
}

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    if (process.argv[2] === 'publish') {
      const owner = await login(browser, 'editor');
      for (const width of [1440, 390]) {
        await owner.page.setViewportSize({width, height: width === 390 ? 844 : 900});
        await openTab(owner.page, 'publish');
        const article = owner.page.locator('article').filter({has: owner.page.locator('input[name=action][value=set_date]')}).first();
        await article.locator('summary').click();
        await article.getByLabel('Publication date and time', {exact: false}).fill(`2097-01-0${width === 390 ? 3 : 2}T14:30`);
        await snap(owner.page, `owner-set-date-${width}`);
        await article.getByRole('button', {name: 'Save date'}).click();
        await owner.page.getByText('Publication date saved.', {exact: false}).waitFor();
        await owner.page.reload();
        await owner.page.getByText(`Jan ${width === 390 ? 3 : 2}, 2097`, {exact: false}).first().waitFor();
      }
      await owner.page.setViewportSize({width: 1440, height: 900});
      await openTab(owner.page, 'publish');
      owner.page.once('dialog', dialog => dialog.dismiss());
      await owner.page.getByRole('button', {name: 'Mark as published', exact: true}).first().click();
      assert.equal(await owner.page.getByText('Published manually', {exact: true}).count(), 0);
      await snap(owner.page, 'owner-confirm-cancel-1440');
      const ella = await login(browser, 'client', 390);
      await openTab(ella.page, 'publish');
      let dialogs = 0;
      ella.page.once('dialog', async dialog => { dialogs++; assert.match(dialog.message(), /Confirm that you have published/); await dialog.accept(); });
      await ella.page.getByRole('button', {name: 'Mark as published', exact: true}).first().click();
      await ella.page.getByText('Published manually', {exact: true}).waitFor();
      assert.equal(dialogs, 1, 'AK6 právě jedno potvrzení');
      for (const [role, person] of [['owner', owner], ['client', ella]]) {
        for (const width of [1440, 390]) {
          await person.page.setViewportSize({width, height: width === 390 ? 844 : 900});
          await openTab(person.page, 'publish');
          await person.page.getByText('Published manually', {exact: true}).waitFor();
          await person.page.getByText('Final video · v2', {exact: true}).waitFor();
          const [download] = await Promise.all([
            person.page.waitForEvent('download'),
            person.page.getByRole('link', {name: 'Download final video'}).click(),
          ]);
          assert.equal(await download.failure(), null);
          assert.match(download.suggestedFilename(), /v2\.mp4$/);
          await snap(person.page, `${role}-published-${width}`);
        }
      }
      console.log('AK1, AK6: owner i klient, 1440 a 390 px, Published + skutečné stažení v2, potvrzení i zrušení OK');
    } else {
      const owner = await login(browser, 'editor');
      await openTab(owner.page, 'video');
      for (const version of process.argv[2] ? [] : [1, 2]) {
        const file = path.join(output, `version-${version}.mp4`);
        execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
          `testsrc2=size=360x640:rate=25:duration=${20 + version}`, '-f', 'lavfi', '-i',
          `sine=frequency=440:sample_rate=48000:duration=${20 + version}`,
          '-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-g', '25', '-c:a', 'aac', file]);
        const [chooser] = await Promise.all([
          owner.page.waitForEvent('filechooser'), owner.page.getByRole('button', {name: 'Upload version', exact: true}).first().click(),
        ]);
        await chooser.setFiles(file);
        await owner.page.locator('[data-video-channel]:visible [data-video-versions] > li').filter({hasText: `v${version} `})
          .getByText('Ready', {exact: true}).waitFor({timeout: 240000});
      }
      if (process.argv[2] !== 'comments') {
        await frame(owner.page);
        const initialToggle = owner.page.locator('[data-video-channel]:visible [data-video-final-toggle]');
        if ((await initialToggle.innerText()).trim() === 'Remove final mark') {
          await initialToggle.click();
          await initialToggle.filter({hasText: 'Mark as final for publishing'}).waitFor();
        }
        for (const label of ['Mark as final for publishing', 'Remove final mark', 'Mark as final for publishing']) {
          const toggle = owner.page.locator('[data-video-channel]:visible [data-video-final-toggle]');
          assert.equal((await toggle.innerText()).trim(), label);
          await toggle.click();
          await owner.page.waitForFunction(previous => {
            const button = document.querySelector('[data-video-channel]:not(.hidden) [data-video-final-toggle]');
            return button && button.textContent.trim() !== previous;
          }, label);
        }
        await history(owner.page, 'owner-history-1440');
        await navigation(owner.page, 'owner');
        await mobileVideo(owner.page, 'owner');
      }
      const ella = await login(browser, 'client');
      await openTab(ella.page, 'comments');
      await ella.page.getByPlaceholder('Write a comment…').fill(`P1-B13 client feedback ${runId}`);
      await ella.page.getByRole('button', {name: 'Add comment', exact: true}).click();
      if (process.argv[2] !== 'comments') {
        await history(ella.page, 'client-history-1440');
        await navigation(ella.page, 'client');
      }
      const view = await mobileVideo(ella.page, 'client');
      await view.locator('video').first().evaluate(video => { video.currentTime = 17; });
      await view.waitForFunction(() => document.querySelector('[data-testid="video-timecode"]')?.textContent.trim() === '0:17');
      const commentText = `P1-B13 time comment 0:17 ${runId}`;
      await view.getByPlaceholder('Leave your comment...').fill(commentText);
      const [savedComment] = await Promise.all([
        ella.page.waitForResponse(response => response.request().method() === 'POST' && /\/video-api\/assets\/[^/]+\/comments$/.test(new URL(response.url()).pathname)),
        view.getByTitle('Send (Enter)').click(),
      ]);
      assert.equal(savedComment.status(), 201);
      assert(Math.abs((await savedComment.json()).timecode_start - 17) < 0.5);
      await view.getByText(commentText, {exact: true}).waitFor();
      await snap(ella.page, 'client-time-comment-390');
      await owner.page.setViewportSize({width: 1440, height: 900});
      await owner.page.goto(root + '/notifications/');
      const alerts = owner.page.getByRole('link').filter({hasText: 'New client comment'});
      assert((await alerts.count()) >= 2, 'AK4 oba druhy klientského komentáře');
      const hrefs = await alerts.evaluateAll(links => links.map(link => link.getAttribute('href')));
      assert(hrefs.some(href => href.endsWith('?tab=comments')) && hrefs.some(href => href.endsWith('?tab=video')));
      await snap(owner.page, 'owner-notifications-1440');
      await owner.page.locator('a[href$="?tab=video"]').first().click();
      await frame(owner.page);
      console.log('AK4, AK5: oba komentáře vyvolaly upozornění s odkazem; owner i klient vidí nahrání, Final a zrušení v History OK');
    }
    assert.deepEqual(errors, [], 'Chyby JavaScriptu');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
