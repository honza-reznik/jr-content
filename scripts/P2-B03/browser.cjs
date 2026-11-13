// P2-B03: živý průchod v Chromiu (desktop 1440×900, mobil 390×844); jen vlastní projekt jrc-p2b03, jen vymyšlená data.
// Spouští run-browser.sh (Docker s limitem paměti a času). Výsledky: ops/test-output/P2-B03/browser.json.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

assert.equal(process.env.JRC_PROJECT, 'jrc-p2b03');
const origin = 'http://127.0.0.1:13903';
const root = origin + '/jrc';
const output = path.resolve('ops/test-output/P2-B03');
const shots = path.resolve('ops/screenshoty/sprint-feedback/P2-B03');
fs.mkdirSync(shots, { recursive: true });
const fixture = JSON.parse(fs.readFileSync(path.join(output, 'fixture.json')));
const only = process.argv.slice(2).filter(arg => !arg.startsWith('--'));
const results = [];
const errors = [];
const run = Date.now().toString(36).slice(-4);
const detailUrl = key => `${root}/workspace/${fixture.ella}/content/${fixture.posts[key]}/`;

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

async function noOverflow(page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1);
}

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
    page.on('pageerror', error => errors.push(`${role}${mobile ? ' mobile' : ''}: ${error.message}`));
    page.on('console', message => {
      // Expected answers of the error checks (400 empty Review, 403 viewer) are asserted in the steps.
      if (message.type() === 'error' && !/status of (400|403)/.test(message.text())) errors.push(`${role}${mobile ? ' mobile' : ''} console: ${message.text()}`);
    });
    page.on('dialog', dialog => dialog.accept());
    return { context, page };
  };
  const shot = (page, name) => page.screenshot({ path: path.join(shots, `${name}.png`), timeout: 15000 });

  for (const mobile of [false, true]) {
    const size = mobile ? 'mobil' : 'desktop';
    const { context, page } = await open('owner', mobile);

    await check(`${size}: AK5 řazení sloupce Idea`, async () => {
      await page.goto(`${root}/board/?client=${fixture.ella}`);
      const titles = await page.locator('.board-column[data-stage="idea"] .board-card h3').allInnerTexts();
      const relevant = titles.filter(title => /Dated|Undated/.test(title));
      assert.deepEqual(relevant.map(title => title.split(' ')[0] + ' ' + title.split(' ')[1]),
        ['Dated tomorrow', 'Dated in', 'Undated oldest', 'Undated middle', 'Undated newest']);
      assert.equal(await page.locator('details.board-new').count(), 0, 'panel + New content zmizel');
      assert(await noOverflow(page), 'bez vodorovného přetečení');
      await shot(page, `${size}-nastenka`);
      return { titles: relevant };
    });

    await check(`${size}: AK3 okno New idea z nástěnky (klient z filtru)`, async () => {
      await page.goto(`${root}/board/?client=${fixture.ella}`);
      await page.getByRole('button', { name: '+ New idea' }).click();
      const dialog = page.locator('#jrc-new-idea');
      await dialog.waitFor({ state: 'visible' });
      assert.equal(await dialog.locator('select[name=workspace]').count(), 0, 'klient je jasný, bez nabídky');
      assert.match(await dialog.locator('.idea-client').innerText(), /Ella Studio/);
      const box = await dialog.boundingBox();
      assert(box.width <= (mobile ? 390 : 1440) && box.x >= 0, 'okno se vejde');
      await shot(page, `${size}-okno-new-idea`);
      // Prázdný název: hláška v okně, nic se neuloží.
      await dialog.getByLabel('Title').fill('   ');
      await dialog.getByRole('button', { name: 'Add idea' }).click();
      await dialog.locator('[data-idea-error]').waitFor({ state: 'visible' });
      await shot(page, `${size}-okno-new-idea-chyba`);
      const title = `Okno nápad ${size} ${run}`;
      await dialog.getByLabel('Title').fill(title);
      await dialog.getByLabel(/Description/).fill('První řádek popisu.\nDruhý řádek.\n\nDruhý odstavec <b>bez HTML</b>.');
      await dialog.locator('.idea-channel', { hasText: 'Ella Instagram' }).click();
      await Promise.all([page.waitForURL(/board/), dialog.getByRole('button', { name: 'Add idea' }).click()]);
      await page.waitForLoadState('load');
      const notice = page.locator('main .rounded-md', { hasText: title });
      await notice.waitFor();
      assert.match(await notice.innerText(), /was added to Idea\. Open it/);
      const ideas = await page.locator('.board-column[data-stage="idea"] .board-card h3').allInnerTexts();
      assert.equal(ideas.at(-1), title, 'nový nápad je ve sloupci Idea úplně dole');
      await shot(page, `${size}-okno-new-idea-hlaska`);
      await Promise.all([page.waitForURL(/content\//), notice.getByRole('link', { name: 'Open it' }).click()]);
      await page.waitForURL(/tab=|content\/[0-9a-f-]+\/$/);
      await page.locator('h1', { hasText: title }).waitFor();
      return { url: page.url() };
    });

    await check(`${size}: AK3 popis = Concept v1`, async () => {
      // Detail nového nápadu se otevírá na Conceptu (bez scriptu); popis je jeho první verze.
      await page.waitForLoadState('networkidle');
      const text = await page.locator('[role=tabpanel]').innerText();
      assert.match(text, /První řádek popisu\./);
      assert.match(text, /Druhý odstavec <b>bez HTML<\/b>\./);
      await shot(page, `${size}-concept-z-popisu`);
    });

    await check(`${size}: AK1 Content board na detailu vrací nástěnku s filtrem`, async () => {
      await page.goto(`${root}/board/?client=${fixture.ella}&channel=${fixture.channels.insta}`);
      await page.goto(detailUrl('script_one'));
      const back = page.locator('[data-back-to-board]');
      assert.match(await back.getAttribute('href'), new RegExp(`client=${fixture.ella}&channel=${fixture.channels.insta}`));
      await shot(page, `${size}-detail-zpet-na-nastenku`);
      await Promise.all([page.waitForURL(/board/), back.click()]);
      assert.equal(await page.locator('#filter-channel').inputValue(), fixture.channels.insta);
    });

    await check(`${size}: AK3 okno New idea při všech klientech nabízí klienta`, async () => {
      await page.goto(`${root}/board/?client=`);
      await page.getByRole('button', { name: '+ New idea' }).click();
      const dialog = page.locator('#jrc-new-idea');
      await dialog.waitFor({ state: 'visible' });
      const select = dialog.locator('select[name=workspace]');
      assert.equal(await select.count(), 1);
      await select.selectOption(fixture.bravo);
      assert.equal(await dialog.locator('.idea-channel:visible').count(), 1, 'jen kanály vybraného klienta');
      await shot(page, `${size}-okno-new-idea-vyber-klienta`);
      await dialog.getByRole('button', { name: 'Cancel' }).click();
      await dialog.waitFor({ state: 'hidden' });
    });

    await check(`${size}: AK4 + New content otevře prázdný detail, fáze Script`, async () => {
      await page.goto(`${root}/board/?client=${fixture.ella}`);
      await Promise.all([page.waitForURL(/board\/new/), page.getByRole('link', { name: '+ New content' }).click()]);
      await page.locator('#new-content-title').waitFor();
      const stages = await page.locator('#new-content-stage option').allInnerTexts();
      assert.deepEqual(stages, ['Idea', 'Script', 'Shooting', 'Editing', 'Review'], 'Ella: povinné schválení, bez Ready');
      assert(await noOverflow(page));
      await shot(page, `${size}-novy-obsah-prazdny`);
      const title = `Nový obsah ${size} ${run}`;
      await page.locator('#new-content-title').fill(title);
      await page.locator('#new-content-stage').selectOption('script');
      await page.locator('.channel-option', { hasText: 'TikTok' }).click();
      await shot(page, `${size}-novy-obsah-vyplneny`);
      await Promise.all([page.waitForURL(/content\/[0-9a-f-]+\/$/), page.getByRole('button', { name: 'Create content' }).click()]);
      await page.locator('h1', { hasText: title }).waitFor();
      assert.match(await page.locator('header').first().innerText(), /Script/);
      await shot(page, `${size}-novy-obsah-vytvoreny`);
    });

    await check(`${size}: AK4 fáze Review bez kanálu = chyba, nic neuloženo`, async () => {
      await page.goto(`${root}/board/new/?client=${fixture.ella}`);
      const title = `Review bez kanálu ${size} ${run}`;
      await page.locator('#new-content-title').fill(title);
      await page.locator('#new-content-stage').selectOption('review');
      await page.getByRole('button', { name: 'Create content' }).click();
      const alert = page.locator('.new-content .error');
      await alert.waitFor();
      assert.match(await alert.innerText(), /Add a channel/);
      assert.equal(await page.locator('#new-content-title').inputValue(), title, 'vyplněné hodnoty zůstanou');
      await shot(page, `${size}-novy-obsah-chyba-review`);
      await page.goto(`${root}/board/?client=${fixture.ella}`);
      assert.equal(await page.getByText(title).count(), 0, 'obsah nevznikl');
    });

    await check(`${size}: AK4 Bravo (volitelné schválení) Ready s datem`, async () => {
      await page.goto(`${root}/board/new/?client=${fixture.bravo}`);
      const stages = await page.locator('#new-content-stage option').allInnerTexts();
      assert(stages.includes('Ready to publish'), 'planner v režimu bez povinného schválení');
      const title = `Ready ${size} ${run}`;
      await page.locator('#new-content-title').fill(title);
      await page.locator('#new-content-stage').selectOption('ready');
      await page.locator('.channel-option', { hasText: 'Bravo YouTube' }).click();
      await page.locator('#new-content-date').fill('2030-05-04T10:30');
      await Promise.all([page.waitForURL(/content\//), page.getByRole('button', { name: 'Create content' }).click()]);
      const header = await page.locator('header').first().innerText();
      assert.match(header, /Ready/i);
      assert.match(header, /May 4, 2030/);
    });

    await check(`${size}: AK1 + New v menu (Idea, Content)`, async () => {
      await page.goto(detailUrl('script_one'));
      if (mobile) {
        await page.getByRole('button', { name: 'Open navigation' }).click();
        await page.waitForTimeout(400);
      }
      const newButton = page.locator('.sidebar-new-wrap > div > button').first();
      await newButton.click();
      await page.locator('[data-new-content-link]:visible').first().waitFor();
      await shot(page, `${size}-menu-new`);
      const href = await page.locator('.sidebar-new-wrap [data-new-content-link]').getAttribute('href');
      assert.match(href, new RegExp(`board/new/\\?client=${fixture.ella}`));
      await page.locator('.sidebar-new-wrap [data-open-new-idea]').click();
      await page.locator('#jrc-new-idea').waitFor({ state: 'visible' });
      assert.match(await page.locator('#jrc-new-idea .idea-client').innerText(), /Ella Studio/);
      await shot(page, `${size}-menu-new-idea`);
      await page.keyboard.press('Escape');
    });

    await check(`${size}: AK1 staré adresy editoru vedou na nový detail`, async () => {
      await page.goto(`${root}/workspace/${fixture.ella}/compose/${fixture.posts.script_one}/`);
      assert.match(page.url(), new RegExp(`content/${fixture.posts.script_one}/$`));
      await page.goto(`${root}/workspace/${fixture.ella}/compose/${fixture.posts.planned}/?account=${fixture.channels.insta}`);
      assert.match(page.url(), /tab=publish/);
      await page.goto(`${root}/workspace/${fixture.ella}/compose/?scheduled_date=2030-01-02&scheduled_time=08:15&account=${fixture.channels.insta}`);
      assert.match(page.url(), /board\/new\//);
      assert.equal(await page.locator('#new-content-date').inputValue(), '2030-01-02T08:15');
      assert(await page.locator('.channel-option', { hasText: 'Ella Instagram' }).locator('input').isChecked());
      await shot(page, `${size}-stary-compose-novy`);
    });

    await check(`${size}: AK2 kalendář/fronta otevře detail na Publish`, async () => {
      await page.goto(`${root}/workspace/${fixture.ella}/calendar/`);
      await page.waitForLoadState('networkidle');
      const link = page.locator(`a[href*="${fixture.posts.planned}"]:visible`).first();
      await link.waitFor();
      await shot(page, `${size}-kalendar`);
      await Promise.all([page.waitForURL(/tab=publish/), link.click()]);
      assert.equal((await page.locator('[role=tab][aria-selected=true]').textContent()).trim(), 'publish');
      await shot(page, `${size}-kalendar-publish`);
      const html = await page.content();
      assert(!/\/compose\//.test(html.replace(/compose\/[0-9a-f-]+\/delete\//g, '')), 'detail neodkazuje na editor');
    });

    await check(`${size}: AK1 Delete content na detailu`, async () => {
      await page.goto(`${root}/board/new/?client=${fixture.ella}`);
      const title = `Ke smazání ${size} ${run}`;
      await page.locator('#new-content-title').fill(title);
      await Promise.all([page.waitForURL(/content\//), page.getByRole('button', { name: 'Create content' }).click()]);
      await shot(page, `${size}-detail-delete`);
      await Promise.all([page.waitForURL(/board/), page.getByRole('button', { name: 'Delete content' }).click()]);
      await page.locator('main', { hasText: 'Content deleted.' }).waitFor();
      assert.equal(await page.getByText(title).count(), 0);
    });

    await context.close();

    for (const role of ['editor', 'client']) {
      const session = await open(role, mobile);
      await check(`${size}: AK6 ${role} New idea + New content`, async () => {
        const p = session.page;
        await p.goto(`${root}/board/`);
        await p.getByRole('button', { name: '+ New idea' }).click();
        const dialog = p.locator('#jrc-new-idea');
        await dialog.waitFor({ state: 'visible' });
        assert.equal(await dialog.locator('select[name=workspace]').count(), 0, 'jediný klient je jasný');
        const title = `${role} nápad ${size} ${run}`;
        await dialog.getByLabel('Title').fill(title);
        await dialog.getByLabel(/Description/).fill(`Popis od role ${role}.`);
        await Promise.all([p.waitForURL(/board/), dialog.getByRole('button', { name: 'Add idea' }).click()]);
        await p.locator('main .rounded-md', { hasText: title }).waitFor();
        await shot(p, `${size}-${role}-nastenka-po-napadu`);
        await Promise.all([p.waitForURL(/content\//), p.locator('main .rounded-md', { hasText: title }).getByRole('link').click()]);
        await p.waitForLoadState('networkidle');
        assert.match(await p.locator('[role=tabpanel]').innerText(), new RegExp(`Popis od role ${role}`));
        await shot(p, `${size}-${role}-concept`);
        await p.goto(`${root}/board/new/`);
        const stages = await p.locator('#new-content-stage option').allInnerTexts();
        await shot(p, `${size}-${role}-novy-obsah`);
        return { stages };
      });
      await session.context.close();
    }

    const viewer = await open('viewer', mobile);
    await check(`${size}: AK6 viewer bez vytváření`, async () => {
      const p = viewer.page;
      await p.goto(`${root}/board/`);
      assert.equal(await p.locator('[data-open-new-idea]').count(), 0);
      assert.equal(await p.locator('.sidebar-new-wrap').count(), 0);
      const response = await p.goto(`${root}/board/new/`);
      assert.equal(response.status(), 403);
      const old = await p.goto(`${root}/workspace/${fixture.ella}/compose/${fixture.posts.script_one}/`);
      assert.equal(old.status(), 403, 'stejné odmítnutí jako dřív');
      await p.goto(`${root}/board/`);
      await shot(p, `${size}-viewer-nastenka`);
    });
    await viewer.context.close();
  }
  await browser.close();
  fs.writeFileSync(path.join(output, 'browser.json'), JSON.stringify({ results, errors }, null, 2));
  console.log(`HOTOVO: ${results.filter(r => r.pass).length}/${results.length} prošlo; chyby stránky: ${errors.length}`);
  for (const error of errors) console.log('PAGE ERROR', error);
  process.exit(results.every(r => r.pass) && !errors.length ? 0 : 1);
})().catch(error => { console.error(error); process.exit(2); });
