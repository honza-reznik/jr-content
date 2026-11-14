// P2-B14: živý průchod výstupů (kanál + formát) v Chromiu, desktop 1440×900 a mobil 390×844.
// Jen vlastní projekt jrc-p2b14 a vymyšlená data; spouští run-browser.sh. Přihlašovací údaje z prostředí
// (OWNER_EMAIL/OWNER_PASSWORD, CLIENT_EMAIL/CLIENT_PASSWORD), ID z B14_WS a B14_POSTS (hlavní,druhý,dlouhý název).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

assert.equal(process.env.JRC_PROJECT, 'jrc-p2b14');
const root = 'http://127.0.0.1:13914/jrc';
const shots = path.resolve('ops/screenshoty/sprint-feedback/P2-B14');
fs.mkdirSync(shots, { recursive: true });
const ws = process.env.B14_WS;
const [mainPost, secondPost, longPost] = process.env.B14_POSTS.split(',');
const detail = (post, tab, extra = '') => `${root}/workspace/${ws}/content/${post}/?tab=${tab}${extra}`;
const results = [];

async function check(name, fn) {
  try {
    const metrics = await fn();
    results.push({ name, pass: true, ...(metrics || {}) });
  } catch (error) {
    results.push({ name, pass: false, error: String((error && error.stack) || error).slice(0, 700) });
  }
  console.log(JSON.stringify(results.at(-1)));
}

const overflow = page => page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
// Elements whose text spills out of their box horizontally (clipped labels, tags pushed out of cards).
const spills = (page, selector) => page.evaluate(sel => [...document.querySelectorAll(sel)]
  .filter(el => el.offsetParent !== null)
  .filter(el => {
    const box = el.getBoundingClientRect();
    const parent = el.parentElement.getBoundingClientRect();
    return box.right > parent.right + 1 || box.left < parent.left - 1;
  }).map(el => el.textContent.trim().slice(0, 60)), selector);
const mark = page => page.evaluate(() => { window.__noReload = 'kept'; });
const marked = page => page.evaluate(() => window.__noReload === 'kept');

async function login(context, email, password) {
  const page = await context.newPage();
  await page.goto(`${root}/accounts/login/`);
  await page.fill('input[name=login]', email);
  await page.fill('input[name=password]', password);
  await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);
  return page;
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  for (const [device, viewport, mobile] of [['desktop', { width: 1440, height: 900 }, false], ['mobile', { width: 390, height: 844 }, true]]) {
    const context = await browser.newContext({ viewport, isMobile: mobile, hasTouch: mobile });
    const page = await login(context, process.env.OWNER_EMAIL, process.env.OWNER_PASSWORD);
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(String(error)));
    const shot = (name, options = {}) => page.screenshot({ path: `${shots}/${device}-${name}.png`, ...options });

    await check(`${device}: output tags in the header`, async () => {
      await page.goto(detail(mainPost, 'caption'), { waitUntil: 'networkidle' });
      const tags = await page.locator('.channel-tag').allTextContents();
      const names = tags.map(text => text.replace(/\s+/g, ' ').trim());
      assert.ok(names.some(text => text.includes('Ella na YouTube · Video')), names.join(' | '));
      assert.ok(names.some(text => text.includes('Ella na YouTube · Shorts')), names.join(' | '));
      assert.ok(names.some(text => text.includes('· Story')), names.join(' | '));
      await page.locator('#content-channels-label').scrollIntoViewIfNeeded().catch(() => {});
      await shot('01-tags', { fullPage: false });
      return { tags: names.length, overflow: await overflow(page), spilled: await spills(page, '.channel-tag') };
    });

    await check(`${device}: caption per output`, async () => {
      const titles = (await page.locator('[data-caption-channel] h3').allTextContents()).map(text => text.replace(/\s+/g, ' ').trim());
      assert.ok(titles.some(t => t.endsWith('YouTube · Shorts · Ella Šamánková')), titles.join(' | '));
      assert.ok(titles.some(t => t.startsWith('Instagram · Story')), titles.join(' | '));
      await shot('02-caption', { fullPage: true });
      return { outputs: titles.length, overflow: await overflow(page) };
    });

    await check(`${device}: publish per output (date, format)`, async () => {
      await page.goto(detail(mainPost, 'publish'), { waitUntil: 'networkidle' });
      const titles = (await page.locator('[id^=publish-channel-]').allTextContents()).map(text => text.trim());
      assert.equal(titles.length, 5, titles.join(' | '));
      await shot('03-publish', { fullPage: true });
      return { titles, overflow: await overflow(page) };
    });

    await check(`${device}: video switcher by output`, async () => {
      await page.goto(detail(mainPost, 'video'), { waitUntil: 'networkidle' });
      const tabs = (await page.locator('[data-video-tab]').allTextContents()).map(text => text.trim());
      assert.equal(tabs.length, 5, tabs.join(' | '));
      await page.locator('[data-video-tab]', { hasText: 'Shorts' }).click();
      const heading = await page.locator('[data-video-channel]:not(.hidden) h3').first().textContent();
      assert.match(heading, /Shorts/);
      await shot('04-video');
      return { tabs, overflow: await overflow(page) };
    });

    await check(`${device}: approval lists outputs`, async () => {
      await page.goto(detail(mainPost, 'approval'), { waitUntil: 'networkidle' });
      const text = await page.locator('#content-tabpanel').textContent();
      assert.match(text, /YouTube · Shorts/);
      await shot('05-approval', { fullPage: true });
      return { overflow: await overflow(page) };
    });

    await check(`${device}: shotlist sub-tabs per format without reload`, async () => {
      await page.goto(detail(mainPost, 'shotlist'), { waitUntil: 'networkidle' });
      const formats = (await page.locator('[data-shotlist-format]').allTextContents()).map(text => text.trim());
      assert.deepEqual(formats, ['Long form', 'Short form', 'Story', 'Post']);
      await shot('06-shotlist-long');
      await mark(page);
      await page.locator('[data-shotlist-format="short_form"]').click();
      await page.waitForFunction(() => /Short form/.test(document.querySelector('.shotlist-title')?.textContent || ''));
      assert.ok(await marked(page), 'the page reloaded');
      assert.match(page.url(), /format=short_form/);
      const items = await page.locator('.shotlist-row').count();
      await page.fill('[data-shotlist-new]', `Close-up ${device}`);
      await page.keyboard.press('Enter');
      await page.waitForFunction(n => document.querySelectorAll('.shotlist-row').length === n + 1, items);
      assert.match(await page.locator('.shotlist-title').textContent(), /Short form/);
      await shot('07-shotlist-short');
      return { formats, overflow: await overflow(page) };
    });

    await check(`${device}: script sections per format`, async () => {
      await page.goto(detail(mainPost, 'script'), { waitUntil: 'networkidle' });
      await page.waitForSelector('#script-sections:not([hidden])', { timeout: 15000 });
      const buttons = (await page.locator('[data-script-section]').allTextContents()).map(text => text.trim());
      assert.deepEqual(buttons.map(text => text.slice(2)), ['Long form', 'Short form', 'Story', 'Post']);
      const section = mobile ? 'Story' : 'Short form';
      await page.locator(`[data-script-section="${section}"]`).click();
      await page.waitForFunction(name => [...document.querySelectorAll('#script-editor h2')].some(h => h.textContent.trim() === name), section);
      assert.ok(await page.locator(`[data-script-section="${section}"]`).isDisabled());
      await page.waitForFunction(() => /Saved/.test(document.querySelector('#script-status')?.textContent || ''), null, { timeout: 20000 });
      await shot('08-script-sections');
      return { buttons, overflow: await overflow(page) };
    });

    await check(`${device}: add an output with "+" without reload`, async () => {
      await page.goto(detail(secondPost, 'caption'), { waitUntil: 'networkidle' });
      const wanted = mobile ? 'Instagram · Post' : 'Instagram · Story';
      const before = await page.locator('[data-caption-channel]').count();
      await mark(page);
      await page.locator('[data-channel-add] summary').click();
      const groups = await page.locator('#add-channel optgroup').evaluateAll(list => list.map(g => g.label));
      const options = await page.locator('#add-channel option').allTextContents();
      assert.ok(options.includes(wanted), options.join(' | '));
      assert.ok(!options.includes('Instagram · Reel'), 'existing output offered again');
      await shot('09-add-output');
      const value = await page.locator('#add-channel option', { hasText: wanted }).getAttribute('value');
      await page.selectOption('#add-channel', value);
      await page.locator('.channel-add-form button[type=submit]').click();
      const format = wanted.split(' · ')[1];
      await page.waitForFunction(name => [...document.querySelectorAll('.channel-tag-format')].some(t => t.textContent.trim() === name), format);
      await page.waitForFunction(n => document.querySelectorAll('[data-caption-channel]').length === n + 1, before, { timeout: 15000 });
      assert.ok(await marked(page), 'the page reloaded');
      await shot('10-output-added');
      return { groups, options };
    });

    await check(`${device}: long title and many outputs do not overflow`, async () => {
      await page.goto(detail(longPost, 'publish'), { waitUntil: 'networkidle' });
      await shot('11-long-title', { fullPage: true });
      const spilled = await spills(page, '.channel-tag');
      assert.deepEqual(spilled, []);
      const over = await overflow(page);
      assert.ok(over <= 1, `overflow ${over}px`);
      return { overflow: over };
    });

    await check(`${device}: board shows the content as a whole`, async () => {
      await page.goto(`${root}/board/?client=${ws}`, { waitUntil: 'networkidle' });
      const card = page.locator(`.board-card[data-post="${mainPost}"]`);
      assert.equal(await card.count(), 1);
      const channels = (await card.locator('.board-channel').allTextContents()).map(text => text.replace(/\s+/g, ' ').trim());
      assert.ok(channels.some(text => /Video, Shorts · Ella na YouTube/.test(text)), channels.join(' | '));
      await card.scrollIntoViewIfNeeded();
      await shot('12-board');
      return { channels, overflow: await overflow(page) };
    });

    await check(`${device}: calendar shows outputs separately`, async () => {
      await page.goto(`${root}/workspace/${ws}/calendar/?mode=calendar&view=week`, { waitUntil: 'networkidle' });
      let labels = await page.locator('.calendar-format-label').allTextContents();
      if (!labels.length) {
        // The outputs are three days ahead: open the week that holds them.
        await page.goto(`${root}/workspace/${ws}/calendar/?mode=calendar&view=week&date=${new Date(Date.now() + 3 * 864e5).toISOString().slice(0, 10)}`, { waitUntil: 'networkidle' });
        labels = await page.locator('.calendar-format-label').allTextContents();
      }
      const visible = await page.locator('.calendar-format-label').evaluateAll(list => list.filter(el => el.offsetParent !== null).map(el => el.textContent.trim()));
      assert.ok(visible.includes('Shorts') && visible.includes('Video') && visible.includes('Story'), visible.join(' | '));
      await page.locator('.calendar-format-label >> visible=true').first().scrollIntoViewIfNeeded();
      await page.evaluate(() => window.scrollBy(0, -120));
      await shot('13-calendar-week');
      return { visible, overflow: await overflow(page) };
    });

    if (!mobile) {
      await check(`${device}: calendar month and list`, async () => {
        await page.goto(`${root}/workspace/${ws}/calendar/?mode=calendar&view=month&date=${new Date(Date.now() + 3 * 864e5).toISOString().slice(0, 10)}`, { waitUntil: 'networkidle' });
        await shot('14-calendar-month');
        await page.goto(`${root}/workspace/${ws}/calendar/?mode=list`, { waitUntil: 'networkidle' });
        await page.waitForTimeout(800);
        const text = await page.locator('body').textContent();
        assert.match(text, /YouTube · Shorts/);
        await shot('15-publish-queue');
        return { overflow: await overflow(page) };
      });
    }

    results.push({ name: `${device}: page errors`, pass: pageErrors.length === 0, pageErrors });
    console.log(JSON.stringify(results.at(-1)));
    await context.close();

    // The client sees the outputs but cannot add any.
    const clientContext = await browser.newContext({ viewport, isMobile: mobile, hasTouch: mobile });
    const clientPage = await login(clientContext, process.env.CLIENT_EMAIL, process.env.CLIENT_PASSWORD);
    await check(`${device}: client reads outputs and shotlists`, async () => {
      await clientPage.goto(detail(mainPost, 'shotlist', '&format=story'), { waitUntil: 'networkidle' });
      assert.equal(await clientPage.locator('[data-channel-add]').count(), 0);
      assert.equal(await clientPage.locator('.channel-tag-remove').count(), 0);
      assert.match(await clientPage.locator('.shotlist-title').textContent(), /Story/);
      assert.equal(await clientPage.locator('#shotlist-add').count(), 0);
      await clientPage.screenshot({ path: `${shots}/${device}-16-client-shotlist.png` });
      return { overflow: await overflow(clientPage) };
    });
    await clientContext.close();
  }
  await browser.close();
  fs.mkdirSync('ops/test-output/P2-B14', { recursive: true });
  fs.writeFileSync('ops/test-output/P2-B14/browser.json', JSON.stringify(results, null, 2));
  const failed = results.filter(result => !result.pass);
  console.log(`${results.length - failed.length}/${results.length} passed`);
  process.exitCode = failed.length ? 1 : 0;
})();
