// P2-B08: živý průchod v Chromiu (desktop 1440×900, mobil 390×844); jen vlastní projekt jrc-p2b08, jen vymyšlená data.
// Spouští run-browser.sh (Docker s limitem paměti a času). Výsledky: ops/test-output/P2-B08/browser.json.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

assert.equal(process.env.JRC_PROJECT, 'jrc-p2b08');
const origin = 'http://127.0.0.1:13908';
const root = origin + '/jrc';
const output = path.resolve('ops/test-output/P2-B08');
const shots = path.resolve('ops/screenshoty/sprint-feedback/P2-B08');
fs.mkdirSync(shots, { recursive: true });
const fixture = JSON.parse(fs.readFileSync(path.join(output, 'fixture.json')));
const only = process.argv.slice(2).filter(arg => !arg.startsWith('--'));
const results = [];
const errors = [];
const detailUrl = (key, tab) => `${root}/workspace/${fixture.ella}/content/${fixture.posts[key]}/${tab ? `?tab=${tab}` : ''}`;

async function check(name, fn) {
  if (only.length && !only.some(prefix => name.startsWith(prefix))) return;
  try {
    const metrics = await fn();
    results.push({ name, pass: true, ...(metrics || {}) });
  } catch (error) {
    results.push({ name, pass: false, error: String(error && error.stack || error).slice(0, 900) });
  }
  console.log(JSON.stringify(results.at(-1)));
}

const noOverflow = page => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1);
const status = (page, selector) => page.locator(selector).first().textContent();
// A marker on window survives only while no page load happens.
const mark = page => page.evaluate(() => { window.__jrcNoReload = 'kept'; });
const marked = page => page.evaluate(() => window.__jrcNoReload === 'kept');
const waitStatus = (page, selector, pattern, timeout = 15000) => page.waitForFunction(
  ([sel, source]) => new RegExp(source).test((document.querySelector(sel) || {}).textContent || ''), [selector, pattern.source], { timeout });

(async () => {
  const browser = await chromium.launch({ headless: true });
  const open = async (role, mobile) => {
    const context = await browser.newContext({
      viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 900 },
      isMobile: mobile, hasTouch: mobile, deviceScaleFactor: 1,
    });
    await context.addCookies([{ ...fixture.people[role].cookie, url: root + '/' }]);
    await context.route('**/*', route => (new URL(route.request().url()).origin === origin ? route.continue() : route.abort()));
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    const who = `${role}${mobile ? ' mobile' : ''}`;
    page.on('pageerror', error => errors.push(`${who}: ${error.message}`));
    page.on('console', message => {
      // Expected refusals (400 over the limit, 409, 403 checks) and the offline checks are asserted in the steps.
      if (message.type() === 'error' && !/status of (400|403|409)|ERR_INTERNET_DISCONNECTED|Failed to fetch/.test(message.text())) errors.push(`${who} console: ${message.text()}`);
    });
    page.dialogs = [];
    page.on('dialog', dialog => { page.dialogs.push(dialog.message()); (page.dismissDialogs ? dialog.dismiss() : dialog.accept()); });
    return { context, page };
  };
  const shot = (page, name, full = false) => page.screenshot({ path: path.join(shots, `${name}.png`), fullPage: full, timeout: 15000 });

  for (const mobile of [false, true]) {
    const size = mobile ? 'mobil' : 'desktop';
    const { context, page } = await open('owner', mobile);

    await check(`${size} AK1 tabs without page load, history, direct address`, async () => {
      await page.goto(detailUrl('main', 'concept'));
      await page.waitForSelector('#script-panel[data-mounted]');
      await mark(page);
      await page.click('[data-tab-link="script"]');
      await page.waitForSelector('#content-tabpanel[data-tab="script"]');
      assert.match(page.url(), /\?tab=script$/);
      await page.waitForSelector('#script-panel[data-mounted]');
      await page.click('[data-tab-link="shotlist"]');
      await page.waitForSelector('#content-tabpanel[data-tab="shotlist"] [data-shotlist]');
      await page.click('[data-tab-link="history"]');
      await page.waitForSelector('#content-tabpanel[data-tab="history"]');
      assert.equal(await page.getAttribute('[data-tab-link="history"]', 'aria-selected'), 'true');
      await page.goBack();
      await page.waitForSelector('#content-tabpanel[data-tab="shotlist"]');
      assert.match(page.url(), /tab=shotlist/);
      await page.goBack();
      await page.waitForSelector('#content-tabpanel[data-tab="script"]');
      await page.goForward();
      await page.waitForSelector('#content-tabpanel[data-tab="shotlist"]');
      assert.ok(await marked(page), 'a tab switch reloaded the page');
      for (const tab of ['video', 'caption', 'approval', 'discussion', 'publish', 'concept']) {
        await page.click(`[data-tab-link="${tab}"]`);
        await page.waitForSelector(`#content-tabpanel[data-tab="${tab}"]`);
      }
      assert.ok(await marked(page));
      await page.goto(detailUrl('main', 'shotlist'));
      await page.waitForSelector('#content-tabpanel[data-tab="shotlist"] [data-shotlist]');
      assert.ok(await noOverflow(page));
      await shot(page, `${size}-zalozky-shotlist`);
    });

    await check(`${size} AK1 Copy link`, async () => {
      await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin });
      await page.goto(detailUrl('main', 'concept'));
      await page.click('[data-tab-link="caption"]');
      await page.waitForSelector('#content-tabpanel[data-tab="caption"]');
      await page.click('[data-copy-tab-link]');
      await page.waitForFunction(() => document.querySelector('[data-copy-tab-link]').dataset.copied);
      const copied = await page.getAttribute('[data-copy-tab-link]', 'data-copied');
      assert.equal(copied, detailUrl('main', 'caption'));
      await shot(page, `${size}-copy-link`);
      return { copied };
    });

    await check(`${size} AK2 concept: edit in place, one version per session, reload keeps text`, async () => {
      await page.goto(detailUrl('main', 'concept'));
      await page.waitForSelector('#script-panel[data-mounted]');
      await page.waitForFunction(() => document.querySelector('#script-editor .tiptap')?.getAttribute('contenteditable') === 'true');
      const before = await page.locator('#script-versions > li').count();
      await page.click('#script-editor .tiptap');
      await page.keyboard.press('End');
      await page.keyboard.type(` Added on ${size}.`);
      await waitStatus(page, '#script-status', /Saved as V\d+/);
      const first = await status(page, '#script-status');
      await page.keyboard.type(' And more.');
      await waitStatus(page, '#script-status', /Saved to V\d+/);
      const second = await status(page, '#script-status');
      assert.equal(first.match(/V(\d+)/)[1], second.match(/V(\d+)/)[1]);
      assert.equal(await page.locator('#script-versions > li').count(), before + 1);
      await shot(page, `${size}-concept-ulozeno`);
      await page.reload();
      await page.waitForSelector('#script-panel[data-mounted]');
      await page.waitForFunction(text => document.querySelector('#script-editor').textContent.includes(text), `Added on ${size}. And more.`);
      return { first, second };
    });

    await check(`${size} AK2 offline: text kept, saved after reconnect`, async () => {
      await page.goto(detailUrl('main', 'concept'));
      await page.waitForFunction(() => document.querySelector('#script-editor .tiptap')?.getAttribute('contenteditable') === 'true');
      await page.click('#script-editor .tiptap');
      await page.keyboard.press('End');
      await context.setOffline(true);
      await page.keyboard.type(' Offline words.');
      await waitStatus(page, '#script-status', /Offline|Not saved yet/);
      await shot(page, `${size}-concept-offline`);
      // Leaving the tab with unsaved text asks first; dismissing keeps the tab and the text.
      page.dismissDialogs = true;
      page.dialogs.length = 0;
      await page.click('[data-tab-link="history"]');
      await page.waitForFunction(() => true);
      await page.waitForTimeout(9000);
      page.dismissDialogs = false;
      assert.ok(page.dialogs.some(text => /not been saved/.test(text)), JSON.stringify(page.dialogs));
      assert.equal(await page.getAttribute('#content-tabpanel', 'data-tab'), 'concept');
      await context.setOffline(false);
      await waitStatus(page, '#script-status', /Saved/, 40000);
      await page.reload();
      await page.waitForFunction(() => document.querySelector('#script-editor').textContent.includes('Offline words.'));
    });

    await check(`${size} AK2 caption autosave and shotlist in place`, async () => {
      await page.goto(detailUrl('main', 'caption'));
      const field = page.locator('[data-caption-field]').first();
      await field.click();
      await field.press('End');
      await field.type(` ${size} caption`);
      await waitStatus(page, '[data-caption-channel] [data-save-status]', /Saved (as|to) V\d+/);
      await shot(page, `${size}-caption-ulozeno`);
      await page.click('[data-tab-link="shotlist"]');
      await page.waitForSelector('[data-shotlist-inline]');
      const text = page.locator('.shotlist-inline-text').first();
      await text.click();
      await text.press('End');
      await text.type(` (${size})`);
      await waitStatus(page, 'form:has(.shotlist-inline-text) [data-save-status]', /Saved/);
      await shot(page, `${size}-shotlist-upraveno`);
      await page.reload();
      assert.match(await page.inputValue('.shotlist-inline-text'), new RegExp(`\\(${size}\\)`));
      await page.click('[data-tab-link="caption"]');
      await page.waitForSelector('[data-caption-field]');
      assert.match(await page.inputValue('[data-caption-field]'), new RegExp(`${size} caption`));
    });

    await check(`${size} AK3 channel tags: + adds, × removes, no page load`, async () => {
      await page.goto(detailUrl('empty', 'caption'));
      await mark(page);
      await page.click('[data-open-channels]');
      await page.selectOption('#add-channel', fixture.channels.tiktok);
      await page.click('.channel-add-form button');
      await page.waitForSelector('.channel-tag-remove');
      await page.waitForSelector('#content-tabpanel[data-tab="caption"] [data-caption-field]');
      await shot(page, `${size}-kanal-pridan`);
      assert.ok(await noOverflow(page));
      await page.click('.channel-tag-remove button');
      await page.waitForSelector('.channel-tags-empty');
      assert.match(await status(page, '[data-channel-status]'), /Channel removed/);
      assert.ok(await marked(page));
      // A channel with caption history is kept: the reason is shown.
      await page.goto(detailUrl('main', 'concept'));
      await page.click('.channel-tag-remove button');
      await page.waitForSelector('[data-channel-tags] .channel-tags-error');
      await shot(page, `${size}-kanal-nelze-odebrat`);
    });

    await check(`${size} vzhled long title and channels`, async () => {
      await page.goto(detailUrl('long', 'caption'));
      await page.waitForSelector('[data-caption-field]');
      assert.ok(await noOverflow(page));
      await shot(page, `${size}-dlouhe-texty`, true);
    });
    await context.close();

    for (const role of ['client', 'viewer']) {
      const reader = await open(role, mobile);
      await check(`${size} ${role} read only`, async () => {
        await reader.page.goto(detailUrl('main', 'concept'));
        await reader.page.waitForSelector('#script-panel[data-mounted]');
        await reader.page.waitForFunction(() => /Read only/.test(document.querySelector('#script-message').textContent));
        assert.equal(await reader.page.getAttribute('#script-editor .tiptap', 'contenteditable'), 'false');
        assert.equal(await reader.page.locator('.channel-tag-remove, [data-channel-add]').count(), 0);
        await reader.page.click('[data-tab-link="caption"]');
        await reader.page.waitForSelector('#content-tabpanel[data-tab="caption"]');
        assert.equal(await reader.page.locator('[data-caption-channel][data-autosave-url]').count(), 0);
        await reader.page.click('[data-tab-link="shotlist"]');
        await reader.page.waitForSelector('[data-shotlist]');
        assert.equal(await reader.page.locator('[data-shotlist-inline]').count(), 0);
        await shot(reader.page, `${size}-${role}-shotlist`);
      });
      await reader.context.close();
    }
  }
  await browser.close();
  fs.writeFileSync(path.join(output, 'browser.json'), JSON.stringify({ results, errors }, null, 2));
  console.log(`RESULT ${results.filter(r => r.pass).length}/${results.length} passed, ${errors.length} page errors`);
  if (errors.length) console.log(errors.join('\n'));
})();
