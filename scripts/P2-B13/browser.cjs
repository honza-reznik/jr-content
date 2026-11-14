// P2-B13: živý průchod v Chromiu (desktop 1440×900, mobil 390×844); jen vlastní projekt jrc-p2b13, jen vymyšlená data.
// Spouští run-browser.sh (Docker s limitem paměti a času). Výsledky: ops/test-output/P2-B13/browser.json.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

assert.equal(process.env.JRC_PROJECT, 'jrc-p2b13');
const origin = 'http://127.0.0.1:13913';
const root = origin + '/jrc';
const output = path.resolve('ops/test-output/P2-B13');
const shots = path.resolve('ops/screenshoty/sprint-feedback/P2-B13');
fs.mkdirSync(shots, { recursive: true });
const fixture = JSON.parse(fs.readFileSync(path.join(output, 'fixture.json')));
const only = process.argv.slice(2).filter(arg => !arg.startsWith('--'));
const results = [];
const errors = [];
const ws = fixture.workspaces;
const overviewContent = id => `${root}/overview/content/${id}/`;

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
// Text of the elements that stick out of their own box horizontally (cards, chips, headings).
const clipped = (page, selector) => page.$$eval(selector, nodes => nodes
  .filter(node => node.scrollWidth > node.clientWidth + 1 && getComputedStyle(node).overflowX === 'visible')
  .map(node => node.textContent.trim().slice(0, 60)));

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
      // Expected refusals (404 for the overview, 409 for a stale form) are asserted in the steps.
      if (message.type() === 'error' && !/status of (404|409)/.test(message.text())) errors.push(`${who} console: ${message.text()}`);
    });
    return { context, page };
  };
  const shot = (page, name, full = false) => page.screenshot({ path: path.join(shots, `${name}.png`), fullPage: full, timeout: 15000 });
  const openMenu = async (page, mobile) => {
    if (mobile) {
      await page.click('button[aria-controls="jrc-navigation"]');
      await page.waitForTimeout(300);
    }
  };

  for (const mobile of [false, true]) {
    const size = mobile ? 'mobil' : 'desktop';

    // ---- editor in two clients -------------------------------------------------------------
    {
      const { context, page } = await open('editor', mobile);
      await check(`${size} AK1 editor: board of the current client only, no client filter`, async () => {
        // The current client is stored per user: start every round in Ella, as the seed does.
        await page.goto(`${root}/board/?client=${ws.ella}`);
        const response = await page.goto(`${root}/board/`);
        assert.equal(response.status(), 200);
        const text = await page.textContent('.content-board-page');
        assert.match(text, /Ella morning idea/);
        assert.doesNotMatch(text, /Bruno|Secret/);
        assert.equal(await page.locator('#filter-client').count(), 0);
        assert.equal(await page.locator('#idea-client').count(), 0);
        assert.match(await page.textContent('.board-workspace'), /Ella Studio/);
        assert.ok(await noOverflow(page), 'page overflows horizontally');
        await shot(page, `${size}-editor-board-ella`);
      });
      await check(`${size} AK1 editor: switching the client in the menu keeps the board`, async () => {
        await openMenu(page, mobile);
        await page.click('.sidebar-ws-btn');
        await shot(page, `${size}-editor-switcher`);
        await Promise.all([page.waitForNavigation(), page.click(`a[href$="/board/?client=${ws.bruno}"]`)]);
        assert.match(page.url(), /\/board\/\?client=/);
        const text = await page.textContent('.content-board-page');
        assert.match(text, /Bruno morning idea/);
        assert.doesNotMatch(text, /Ella morning idea/);
        await page.goto(`${root}/board/`);
        assert.match(await page.textContent('.board-workspace'), /Bruno Bakery/);
      });
      await check(`${size} AK2 editor: New idea goes to the current client, no client choice`, async () => {
        await page.click('.board-create [data-open-new-idea]');
        await page.waitForSelector('#jrc-new-idea[open]');
        assert.equal(await page.locator('#new-idea-client').count(), 0);
        assert.match(await page.textContent('#jrc-new-idea .idea-client'), /Bruno Bakery/);
        await shot(page, `${size}-editor-new-idea-bruno`);
        await page.fill('#new-idea-title', 'Idea from the live test');
        await Promise.all([page.waitForNavigation(), page.click('#jrc-new-idea .idea-submit')]);
        assert.match(await page.textContent('.content-board-page'), /Idea from the live test/);
      });
      await check(`${size} AK2 editor: a stale form of another client is refused`, async () => {
        // The board still shows Bruno; a request naming Ella (another tab) must be refused.
        const status = await page.evaluate(async ella => {
          const token = document.querySelector('[name=csrfmiddlewaretoken]').value;
          const body = new FormData();
          body.append('csrfmiddlewaretoken', token); body.append('workspace', ella); body.append('title', 'Stale tab idea');
          const response = await fetch(location.pathname.replace(/board\/.*$/, 'board/idea/'), { method: 'POST', body, headers: { 'X-Requested-With': 'XMLHttpRequest' } });
          return [response.status, await response.text()];
        }, ws.ella);
        assert.equal(status[0], 409);
        assert.match(status[1], /Nothing was saved/);
      });
      await check(`${size} AK3 editor: no Admin item, overview refused`, async () => {
        await page.goto(`${root}/board/`);
        assert.equal(await page.locator('a.sidebar-nav-item[href$="/overview/"]').count(), 0);
        for (const url of [`${root}/overview/`, `${root}/organizations/calendar/`, overviewContent(fixture.posts.ella.idea)]) {
          const response = await page.goto(url);
          assert.equal(response.status(), 404, url);
        }
      });
      await context.close();
    }

    // ---- client of Ella ----------------------------------------------------------------------
    {
      const { context, page } = await open('client', mobile);
      await check(`${size} AK1 client: own client board, quick idea, no Admin`, async () => {
        await page.goto(`${root}/board/`);
        const text = await page.textContent('.content-board-page');
        assert.match(text, /Ella/);
        assert.doesNotMatch(text, /Bruno|Secret/);
        assert.equal(await page.locator('#quick-idea select').count(), 0);
        assert.equal(await page.locator('a.sidebar-nav-item[href$="/overview/"]').count(), 0);
        assert.equal((await page.goto(`${root}/overview/`)).status(), 404);
        await page.goto(`${root}/board/`);
        assert.ok(await noOverflow(page));
        await shot(page, `${size}-client-board`);
      });
      await context.close();
    }

    // ---- organization owner (member of Ella only) --------------------------------------------
    {
      const { context, page } = await open('owner', mobile);
      await check(`${size} AK3 owner: Admin overview board with every client`, async () => {
        await page.goto(`${root}/board/`);
        await openMenu(page, mobile);
        await Promise.all([page.waitForNavigation(), page.click('a.sidebar-nav-item[href$="/overview/"]')]);
        const text = await page.textContent('.ov-page');
        for (const title of ['Ella morning idea', 'Bruno morning idea', 'Secret morning idea']) assert.match(text, new RegExp(title));
        assert.equal(await page.locator('.ov-page form[method="post"]').count(), 0);
        assert.equal(await page.locator('.ov-card').count() >= 10, true);
        assert.ok(await noOverflow(page), 'page overflows horizontally');
        const sticking = await clipped(page, '.ov-card h3, .ov-client, .overview-header h1');
        assert.deepEqual(sticking, []);
        await shot(page, `${size}-owner-overview-board`);
        if (mobile) {
          await page.selectOption('#overview-stage', 'idea');
          await shot(page, `${size}-owner-overview-board-idea`);
        }
      });
      await check(`${size} AK3 owner: client filter`, async () => {
        await page.selectOption('#overview-client', ws.secret);
        await Promise.all([page.waitForNavigation(), page.click('.ov-tools button[type=submit]')]);
        const text = await page.textContent('.ov-grid');
        assert.match(text, /Secret/);
        assert.doesNotMatch(text, /Ella morning|Bruno morning/);
        await shot(page, `${size}-owner-overview-secret`);
      });
      await check(`${size} AK3 owner: read-only summary of a client without membership`, async () => {
        await Promise.all([page.waitForNavigation(), page.click(`a[href$="/overview/content/${fixture.posts.secret.planned}/"]`)]);
        const text = await page.textContent('.ovc');
        assert.match(text, /Secret launch reel/);
        assert.match(text, /Team note for Secret/);
        assert.match(text, /You are not a member of this client/);
        assert.equal(await page.locator('.ovc form').count(), 0);
        assert.equal(await page.locator('.ovc-open').count(), 0);
        assert.ok(await noOverflow(page));
        await shot(page, `${size}-owner-overview-content-secret`, true);
        const response = await page.goto(`${root}/workspace/${ws.secret}/content/${fixture.posts.secret.planned}/`);
        assert.equal(response.status(), 404);
      });
      await check(`${size} AK3 owner: calendar of all clients`, async () => {
        await page.goto(`${root}/overview/`);
        await Promise.all([page.waitForNavigation(), page.click('.overview-tabs a:has-text("Calendar")')]);
        await page.goto(`${root}/organizations/calendar/?view=week&date=${fixture.date}`);
        const chips = await page.locator('.cross-post-chip').count();
        assert.ok(chips >= 3, `chips ${chips}`);
        assert.equal(await page.locator('.cal-add-btn').count(), 0);
        assert.match(await page.textContent('.cross-calendar-main'), /Secret launch reel/);
        await shot(page, `${size}-owner-overview-calendar-week`);
        await page.goto(`${root}/organizations/calendar/?date=${fixture.date}`);
        assert.ok(await noOverflow(page), 'page overflows horizontally');
        await shot(page, `${size}-owner-overview-calendar-month`);
        await Promise.all([page.waitForNavigation(), page.locator('.cross-post-chip').first().click()]);
        assert.match(page.url(), /\/overview\/content\//);
      });
      await context.close();
    }

    // ---- organization admin (manager in Bruno only) ------------------------------------------
    {
      const { context, page } = await open('admin', mobile);
      await check(`${size} AK3 admin: summary links to the detail only where a member`, async () => {
        await page.goto(overviewContent(fixture.posts.bruno.planned));
        assert.equal(await page.locator('.ovc-open').count(), 1);
        await shot(page, `${size}-admin-overview-content-bruno`);
        await Promise.all([page.waitForNavigation(), page.click('.ovc-open')]);
        assert.match(page.url(), new RegExp(`/workspace/${ws.bruno}/content/`));
        await page.goto(overviewContent(fixture.posts.ella.planned));
        assert.equal(await page.locator('.ovc-open').count(), 0);
        assert.match(await page.textContent('.ovc'), /Team note for Ella/);
      });
      await context.close();
    }
  }

  await browser.close();
  fs.writeFileSync(path.join(output, 'browser.json'), JSON.stringify({ results, errors }, null, 2));
  const failed = results.filter(result => !result.pass);
  console.log(`SOUHRN ${results.length - failed.length}/${results.length} prošlo, chyb stránky: ${errors.length}`);
  if (errors.length) console.log(errors.join('\n'));
  process.exit(failed.length || errors.length ? 1 : 0);
})();
