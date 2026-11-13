// P2-B03 oprava po kontrole: živě body 1 (okno New idea na stránce New content) a 2 (+ v kalendáři organizace před 10:00).
// Spouští run-browser.sh s argumentem --review; jen vlastní projekt jrc-p2b03, vymyšlená data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

assert.equal(process.env.JRC_PROJECT, 'jrc-p2b03');
const origin = 'http://127.0.0.1:13903';
const root = origin + '/jrc';
const output = path.resolve('ops/test-output/P2-B03');
const shots = path.resolve('ops/screenshoty/sprint-feedback/P2-B03');
const fixture = JSON.parse(fs.readFileSync(path.join(output, 'fixture.json')));
const run = Date.now().toString(36).slice(-4);
const results = [];

(async () => {
  const browser = await chromium.launch({ headless: true });
  for (const mobile of [false, true]) {
    const size = mobile ? 'mobil' : 'desktop';
    const context = await browser.newContext({
      viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 900 }, isMobile: mobile, hasTouch: mobile,
    });
    await context.addCookies([{ ...fixture.people.owner.cookie, url: root + '/' }]);
    await context.route('**/*', route => (new URL(route.request().url()).origin === origin ? route.continue() : route.abort()));
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    const check = async (name, fn) => {
      try { await fn(); results.push({ name, pass: true }); } catch (error) { results.push({ name, pass: false, error: String(error).slice(0, 600) }); }
      console.log(JSON.stringify(results.at(-1)));
    };

    await check(`${size}: 1 okno New idea na New content pro klienta stránky`, async () => {
      // Naposledy otevřený workspace je Ella; stránka ukazuje Bravo.
      await page.goto(`${root}/workspace/${fixture.ella}/content/${fixture.posts.script_one}/`);
      await page.goto(`${root}/board/new/?client=${fixture.bravo}`);
      if (mobile) { await page.getByRole('button', { name: 'Open navigation' }).click(); await page.waitForTimeout(400); }
      await page.locator('.sidebar-new-wrap > div > button').first().click();
      await page.locator('.sidebar-new-wrap [data-open-new-idea]').click();
      const dialog = page.locator('#jrc-new-idea');
      await dialog.waitFor({ state: 'visible' });
      assert.match(await dialog.locator('.idea-client').innerText(), /Bravo Bakery/);
      assert.equal(await dialog.locator('input[name=workspace]').inputValue(), fixture.bravo);
      await page.screenshot({ path: path.join(shots, `${size}-oprava-okno-na-new-content.png`) });
      const title = `Oprava okno ${size} ${run}`;
      await dialog.getByLabel('Title').fill(title);
      await Promise.all([page.waitForURL(/board\/new/), dialog.getByRole('button', { name: 'Add idea' }).click()]);
      await page.locator('main .rounded-md', { hasText: title }).waitFor();
      await page.goto(`${root}/board/?client=${fixture.bravo}`);
      await page.locator('.board-card h3', { hasText: title }).waitFor();
    });

    await check(`${size}: 2 + v kalendáři organizace v 8:00 předvyplní datum`, async () => {
      await page.goto(`${root}/organizations/calendar/?view=day&date=2030-01-02`);
      const add = page.locator('a[href*="board/new/"][href*="time=08:00"]').first();
      await add.waitFor({ state: 'attached' });
      await page.goto(new URL(await add.getAttribute('href'), page.url()).toString());
      assert.equal(await page.locator('#new-content-date').inputValue(), '2030-01-02T08:00');
      await page.screenshot({ path: path.join(shots, `${size}-oprava-kalendar-organizace-8-00.png`) });
      await page.goto(`${root}/board/new/?client=${fixture.ella}&date=2030-01-02&time=8:00`);
      assert.equal(await page.locator('#new-content-date').inputValue(), '2030-01-02T08:00');
    });
    await context.close();
  }
  await browser.close();
  console.log(`HOTOVO: ${results.filter(r => r.pass).length}/${results.length}`);
  process.exit(results.every(r => r.pass) ? 0 : 1);
})().catch(error => { console.error(error); process.exit(2); });
