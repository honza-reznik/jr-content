// Run only in jrc-p2b09-dev: timeout 600s node --max-old-space-size=2048 scripts/P2-B09/board.cjs
const { chromium } = require('playwright');
const http = require('node:http');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
assert.equal(process.env.JRC_PROJECT, 'jrc-p2b09');
const origin = 'http://127.0.0.1:13909';
const base = origin + '/jrc';
const shots = 'ops/screenshoty/sprint-feedback/P2-B09';
const errors = [], checks = [];
const note = name => { checks.push(name); console.log(name); };
(async () => {
  const seed = JSON.parse(await fs.readFile('brightbean/output/P2-B09/seed.json', 'utf8'));
  const server = http.createServer((req, res) => {
    const video = req.url.startsWith('/jrc/video-api/');
    const path = req.url.replace(video ? /^\/jrc\/video-api/ : /^\/jrc/, '') || '/';
    const upstream = http.request({ hostname: video ? 'jrc-ff-api' : 'jrc-bb-web', port: 8000, path, method: req.method, headers: req.headers }, response => {
      res.writeHead(response.statusCode, response.headers); response.pipe(res);
    });
    res.on('close', () => upstream.destroy());
    upstream.on('error', () => { res.writeHead(502); res.end('Local upstream unavailable'); });
    req.pipe(upstream);
  });
  await new Promise(resolve => server.listen(13909, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true });
  try {
    const width = Number(process.env.BOARD_WIDTH || '1440');
    const mobile = width === 390;
    async function session(role) {
      const context = await browser.newContext({ viewport: { width, height: mobile ? 844 : 900 }, isMobile: mobile, hasTouch: mobile });
      await context.addCookies([{ name: 'sessionid', value: seed.sessions[role], url: origin }]);
      await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      const page = await context.newPage();
      page.setDefaultTimeout(15000);
      page.on('pageerror', error => errors.push(`${role}: ${error.message}`));
      page.on('dialog', dialog => dialog.accept());
      return { page, context };
    }
    const { page, context } = await session('owner');
    const boardUrl = `${base}/board/?client=${seed.workspace}`;
    await page.goto(boardUrl, { waitUntil: 'networkidle' });
    assert.equal(await page.locator('.board-card').count(), 10);
    const card = name => page.locator(`.board-card[data-post="${seed.posts[name]}"]`);
    async function shot(name, targetPage = page) { await targetPage.screenshot({ path: `${shots}/${name}-${width}.png`, fullPage: false }); }
    async function noOverflow(targetPage = page) { assert(await targetPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `no horizontal page overflow at ${width}`); }
    await noOverflow(); await shot('board');
    const longTitle = card('Long title').locator('h3');
    const clamp = await longTitle.evaluate(node => ({ lines: node.clientHeight / parseFloat(getComputedStyle(node).lineHeight), clipped: node.scrollHeight > node.clientHeight }));
    assert(clamp.lines <= 3.1 && clamp.clipped);
    note(`${width}: board, long title, readable controls, no page overflow`);
    await page.locator('#quick-search').fill('Drag idea');
    assert.equal(await page.locator('.board-card:visible').count(), 1);
    await page.locator('#quick-search').fill('nonexistent title');
    assert(await page.locator('#board-no-results').isVisible()); await shot('empty-search');
    await page.locator('#quick-search').fill('');
    async function menu(item, targetPage = page) {
      if (mobile) await item.locator('[data-card-menu]').click();
      else await item.locator('h3').click({ button: 'right' });
      await targetPage.locator('#card-action-menu').waitFor({ state: 'visible' });
    }
    await menu(card('Drag idea')); await shot('context-menu');
    assert(await page.getByRole('menuitem', { name: 'Move to Script…', exact: true }).isVisible());
    await page.keyboard.press('Escape');
    async function drag(item, target) {
      const header = page.locator(`.board-column[data-stage="${target}"] > h2`);
      if (!mobile) {
        const title = item.locator('h3');
        await title.scrollIntoViewIfNeeded();
        const source = await title.boundingBox();
        await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
        await page.mouse.down();
        await page.mouse.move(source.x + source.width / 2 + 10, source.y + source.height / 2 + 10, { steps: 6 });
        await header.scrollIntoViewIfNeeded();
        const dest = await header.boundingBox();
        await page.mouse.move(dest.x + dest.width / 2, dest.y + dest.height / 2, { steps: 8 });
        await page.mouse.up();
        return;
      }
      const handle = item.locator('[data-drag-handle]');
      await handle.scrollIntoViewIfNeeded();
      const source = await handle.boundingBox();
      const cdp = await context.newCDPSession(page);
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: source.x + source.width / 2, y: source.y + source.height / 2 }] });
      await header.scrollIntoViewIfNeeded();
      const dest = await header.boundingBox();
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: dest.x + dest.width / 2, y: dest.y + dest.height / 2 }] });
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
      await cdp.detach();
    }
    await Promise.all([page.waitForNavigation(), drag(card('Drag idea'), 'script')]);
    assert.equal(await card('Drag idea').getAttribute('data-stage'), 'script');
    note(`${width}: ${mobile ? 'real touch pointer' : 'mouse drag'} moved card through server`);
    await card('Bulk idea one').locator('.board-select').check();
    await card('Bulk idea two').locator('.board-select').check({ modifiers: ['Shift'] });
    assert.equal(await page.locator('#selection-count').textContent(), '2 selected');
    await page.locator('[data-bulk-move]').click();
    await page.locator('#action-stage').selectOption('shooting');
    await Promise.all([page.waitForNavigation(), page.locator('#board-action-form button[type=submit]').click()]);
    assert.equal(await card('Bulk idea one').getAttribute('data-stage'), 'shooting');
    assert.equal(await card('Bulk idea two').getAttribute('data-stage'), 'shooting');
    await shot('bulk-result'); note(`${width}: Shift selection and bulk move`);
    await menu(card('Trash and restore'));
    await Promise.all([page.waitForNavigation(), page.getByRole('menuitem', { name: 'Move to Trash', exact: true }).click()]);
    assert.equal(await card('Trash and restore').count(), 0);
    await page.goto(`${base}/board/trash/`, { waitUntil: 'networkidle' });
    await noOverflow(); await shot('trash');
    await Promise.all([page.waitForNavigation(), page.locator('[data-restore]').click()]);
    assert.equal(await page.locator('[data-restore]').count(), 0);
    await page.goto(boardUrl, { waitUntil: 'networkidle' });
    assert.equal(await card('Trash and restore').count(), 1);
    note(`${width}: Trash, restore and empty state`);
    await drag(card('Team review'), 'approved');
    await page.locator('#board-action-dialog').waitFor({ state: 'visible' });
    await page.locator('#action-comment').fill('Approved from the board with a comment.');
    await shot('approval-dialog');
    await Promise.all([page.waitForNavigation(), page.locator('#board-action-form button[type=submit]').click()]);
    assert.equal(await card('Team review').getAttribute('data-stage'), 'review');
    assert.match(await page.locator('#board-results').innerText(), /waits for the client's approval/);
    note(`${width}: team approval preserves the client step and records comment`);
    async function search(targetPage, needle, count) {
      await targetPage.goto(`${boardUrl}&q=${encodeURIComponent(needle)}`, { waitUntil: 'networkidle' });
      assert.equal(await targetPage.locator('.board-card').count(), count);
    }
    await search(page, 'sapphire lighthouse', 1); await shot('full-search');
    await search(page, 'obsidian lantern', 1);
    const client = await session('client');
    await client.page.goto(boardUrl, { waitUntil: 'networkidle' });
    for (const name of ['Client review one', 'Client review two']) await client.page.locator(`[data-post="${seed.posts[name]}"] .board-select`).check();
    await client.page.locator('[data-bulk-approve]').click();
    await client.page.locator('#action-comment').fill('Client bulk approval.');
    await Promise.all([client.page.waitForNavigation(), client.page.locator('#board-action-form button[type=submit]').click()]);
    for (const name of ['Client review one', 'Client review two']) assert.equal(await client.page.locator(`.board-card[data-post="${seed.posts[name]}"]`).getAttribute('data-stage'), 'approved');
    await shot('client-bulk-approval', client.page);
    await search(client.page, 'obsidian lantern', 0);
    await search(client.page, 'amber telescope', 1);
    await noOverflow(client.page);
    note(`${width}: client bulk approval; public/private discussion search permissions`);
    await client.context.close();
    const editor = await session('editor');
    await editor.page.goto(boardUrl, { waitUntil: 'networkidle' });
    const requestCard = editor.page.locator(`.board-card[data-post="${seed.posts['Approval request']}"]`);
    await menu(requestCard, editor.page);
    await editor.page.getByRole('menuitem', { name: 'Request approval…', exact: true }).click();
    await Promise.all([editor.page.waitForNavigation(), editor.page.locator('#board-action-form button[type=submit]').click()]);
    assert.match(await editor.page.locator('#board-results').innerText(), /Approval requested/);
    assert.equal(await editor.page.locator(`.board-card[data-post="${seed.posts['Approval request']}"]`).getAttribute('data-stage'), 'review');
    await shot('request-approval', editor.page); await editor.context.close();
    const viewer = await session('viewer');
    await viewer.page.goto(boardUrl, { waitUntil: 'networkidle' });
    await menu(viewer.page.locator('.board-card').first(), viewer.page);
    assert.deepEqual(await viewer.page.locator('#card-action-menu [role=menuitem]').allTextContents(), ['Open content', 'Copy link']);
    await viewer.page.keyboard.press('Escape');
    await viewer.page.locator('.board-select').first().check();
    assert(await viewer.page.locator('[data-bulk-approve]').isDisabled());
    assert(await viewer.page.locator('[data-bulk-trash]').isDisabled());
    await noOverflow(viewer.page); await shot('viewer', viewer.page);
    await viewer.context.close(); note(`${width}: viewer actions reflect permissions`);
    await page.goto(boardUrl, { waitUntil: 'networkidle' });
    // Server refusal leaves the card where it was, with an explanation in the result panel.
    await Promise.all([page.waitForResponse(response => response.url().endsWith('/board/actions/')), drag(card('Drag idea'), 'published')]);
    await page.locator('#board-results').waitFor({ state: 'visible' });
    assert.match(await page.locator('#board-results').innerText(), /1 failed/);
    assert.equal(await card('Drag idea').getAttribute('data-stage'), 'script');
    await shot('forbidden-move');
    // Permanent deletion is explicit and applies to all eligible trash rows.
    await menu(card('Trash and restore'));
    await Promise.all([page.waitForNavigation(), page.getByRole('menuitem', { name: 'Move to Trash', exact: true }).click()]);
    await page.goto(`${base}/board/trash/`, { waitUntil: 'networkidle' });
    await Promise.all([page.waitForNavigation(), page.locator('#empty-trash').click()]);
    assert.match(await page.locator('.board-trash-list').innerText(), /Trash is empty/);
    await shot('trash-empty');
    assert.deepEqual(errors, []);
    await fs.writeFile(`ops/test-output/P2-B09/browser-${width}.json`, JSON.stringify({ checks, errors }, null, 2));
    note(`${width}: forbidden move stays in place; Empty trash; no JavaScript errors`);
    await context.close();
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
