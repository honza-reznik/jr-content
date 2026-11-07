// Jen Docker s limitem paměti: spouštěč run-browser.sh omezuje také celý procesní strom.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
assert.equal(process.env.JRC_PROJECT, 'jrc-p1b31');
const before = process.argv.includes('--before');
const portrait = process.argv.includes('--portrait');
const output = path.resolve('ops/test-output/P1-B31');
const shots = path.resolve('ops/screenshoty/P1-B31', before ? 'before' : 'after');
const fixture = JSON.parse(fs.readFileSync(path.join(output, 'fixture.json')));
// Port vždy z prostředí (nastaví run-browser.sh z .env), žádná pevná hodnota.
assert(process.env.JRC_APP_PORT, 'Chybí JRC_APP_PORT');
const origin = 'http://127.0.0.1:' + process.env.JRC_APP_PORT;
const root = origin + '/jrc';
const detail = `${root}/workspace/${fixture.workspace}/content/${fixture.post}/`;
const calendar = `${root}/workspace/${fixture.workspace}/calendar/`;
fs.mkdirSync(shots, { recursive: true });
const results = [];
const errors = [];

function fits(m) { return m.scrollWidth <= m.width && !(m.mainOverflowX > 1) && !(m.containerOverflowX > 1); }
function record(name, pass, metrics) {
  results.push({ name, pass, ...metrics });
  console.log(JSON.stringify(results.at(-1)));
}
async function shot(page, name) {
  await page.screenshot({ path: path.join(shots, `${name}.png`), timeout: 15000 });
}
async function dimensions(page) {
  return page.evaluate(() => ({
    width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
    scrollY, headerTop: document.querySelector('body > div')?.getBoundingClientRect().top,
    height: innerHeight, scrollHeight: document.documentElement.scrollHeight,
    // P1-B31 AK5: přetečení se měří i u <main> a u obsahového kontejneru, ne jen u dokumentu.
    mainOverflowX: (m => m ? m.scrollWidth - m.clientWidth : 0)(document.querySelector('main')),
    containerOverflowX: (c => c ? c.scrollWidth - c.clientWidth : 0)(document.querySelector('main > div:last-child, main > div')),
  }));
}
async function player(page) {
  const handle = await page.locator('iframe[data-video-frame]:visible').elementHandle();
  const frame = await handle.contentFrame();
  await frame.waitForFunction(() => document.querySelector('video')?.duration > 1, null, { timeout: 60000 });
  return frame;
}
async function checkPlayer(page, name) {
  await page.goto(detail + '?tab=video');
  const frame = await player(page);
  const text = 'P1-B31 kontrola překryvu';
  if (!(await frame.getByText(text, { exact: true }).count())) {
    await frame.getByPlaceholder('Leave your comment...').fill(text);
    await frame.getByTitle('Send (Enter)').click();
    await frame.getByText(text, { exact: true }).first().waitFor();
    await page.reload();
    await player(page);
  }
  const activeFrame = await player(page);
  await page.locator('iframe[data-video-frame]:visible').scrollIntoViewIfNeeded();
  const play = activeFrame.getByRole('button', { name: 'Play', exact: true }).first();
  if (!(await play.count())) {
    console.log('PLAYER BUTTONS', await activeFrame.locator('button').evaluateAll(buttons => buttons.map(button => ({
      title: button.title, aria: button.getAttribute('aria-label'), text: button.textContent.slice(0, 80),
    }))));
    await shot(page, `${name}-player-diagnostic`);
  }
  const metrics = await play.evaluate(button => {
    const box = button.getBoundingClientRect();
    const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
    const ancestors = [];
    let el = button;
    for (let i = 0; i < 6 && el; i++, el = el.parentElement) {
      const style = getComputedStyle(el), box = el.getBoundingClientRect();
      ancestors.push({ tag: el.tagName, class: el.className, height: box.height,
        top: box.top, bottom: box.bottom, paddingBottom: style.paddingBottom,
        stageMax: style.getPropertyValue('--ff-stage-max'), minHeight: style.minHeight });
    }
    return { reachable: hit === button || button.contains(hit), hit: hit?.outerHTML.slice(0, 400), ancestors };
  });
  metrics.timelineReachable = await activeFrame.locator('[data-testid=progress-bar-track]').evaluate(track => {
    const box = track.getBoundingClientRect();
    const hit = document.elementFromPoint(box.x + box.width * 0.7, box.y + box.height / 2);
    return hit === track || track.contains(hit);
  });
  await activeFrame.locator('video').first().evaluate(video => { video.muted = true; });
  // Klik ve skutečném středu ovládání odhalí i průhledný překryv, bez force click.
  const box = await play.boundingBox();
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await activeFrame.locator('video').first().evaluate(video => video.paused ? new Promise(resolve => { const timer = setTimeout(resolve, 5000); video.addEventListener('playing', () => { clearTimeout(timer); resolve(); }, { once: true }); }) : null);
  metrics.playing = await activeFrame.locator('video').first().evaluate(video => !video.paused);
  const track = await activeFrame.locator('[data-testid=progress-bar-track]').boundingBox();
  await page.mouse.click(track.x + track.width * 0.7, track.y + track.height / 2);
  await activeFrame.locator('video').first().evaluate(video => video.currentTime > video.duration * 0.5 ? null : new Promise(resolve => { const timer = setTimeout(resolve, 5000); video.addEventListener('seeked', () => { clearTimeout(timer); resolve(); }, { once: true }); }));
  metrics.seeked = await activeFrame.locator('video').first().evaluate(video => video.currentTime > video.duration * 0.5);
  const comment = `P1-B31 ${name} ${Date.now()}`;
  const open = activeFrame.getByRole('button', { name: 'Open comments', exact: true });
  if (await open.count()) await open.click();
  await activeFrame.getByPlaceholder('Leave your comment...').fill(comment);
  await activeFrame.getByTitle('Send (Enter)').click();
  await activeFrame.getByText(comment, { exact: true }).waitFor();
  metrics.commentSaved = true;
  metrics.page = await dimensions(page);
  record(`${name}-player`, metrics.reachable && metrics.timelineReachable && metrics.playing && metrics.seeked && metrics.commentSaved && fits(metrics.page), metrics);
  await shot(page, `${name}-player`);
}
async function checkCalendar(page, name, view) {
  await page.goto(calendar + '?mode=calendar&view=' + view);
  await page.locator('#calendar-content').waitFor();
  const metrics = await page.locator('#calendar-content').evaluate(el => {
    const box = el.getBoundingClientRect();
    const scrollBoxes = [...el.querySelectorAll('*')].filter(node => {
      if (!node.getClientRects().length) return false;
      const style = getComputedStyle(node);
      return /auto|scroll/.test(style.overflowX) && node.scrollWidth > node.clientWidth + 1;
    }).map(node => ({ width: node.clientWidth, scrollWidth: node.scrollWidth }));
    return { height: box.height, clientHeight: el.clientHeight, scrollHeight: el.scrollHeight,
      waiting: document.querySelectorAll('.waiting-item').length, scrollBoxes,
      days: el.querySelectorAll('[data-date]').length,
      agendaDays: [...el.querySelectorAll('[data-agenda-date]')].filter(node => node.getClientRects().length).map(node => node.dataset.agendaDate),
      contentOverflow: getComputedStyle(el).overflowY };
  });
  metrics.page = await dimensions(page);
  const agenda = page.locator('#calendar-content [data-agenda-date]:visible');
  if (await agenda.count()) {
    await agenda.last().scrollIntoViewIfNeeded();
    metrics.lastDayReachable = await agenda.last().evaluate(el => {
      const box = el.getBoundingClientRect();
      return box.top >= 0 && box.bottom <= innerHeight;
    });
    await shot(page, `${name}-${view}-last-day`);
  }
  // Měsíc: jen dny jednoho měsíce (AK2), týden: sedm po sobě jdoucích dnů.
  const agendaDates = metrics.agendaDays;
  const monthKeys = new Set(agendaDates.map(date => date.slice(0, 7)));
  const monthComplete = monthKeys.size === 1 && agendaDates.length === new Date(Date.UTC(+agendaDates[0].slice(0, 4), +agendaDates[0].slice(5, 7), 0)).getUTCDate();
  const weekComplete = agendaDates.length === 7 && new Set(agendaDates.map(date => new Date(date).getUTCDay())).size === 7;
  const mobileDays = metrics.page.width >= 768 || view === 'day' || metrics.lastDayReachable && (view === 'month' ? monthComplete : weekComplete);
  record(`${name}-${view}`, !metrics.scrollBoxes.length && metrics.height >= 400 && mobileDays &&
    metrics.contentOverflow === 'visible' && metrics.page.scrollY === 0 &&
    metrics.page.scrollHeight <= metrics.page.height + 1 && fits(metrics.page), metrics);
  await page.locator('#calendar-content').scrollIntoViewIfNeeded();
  await shot(page, `${name}-${view}`);
  await checkCalendarExtras(page, name, view);
}
async function checkCalendarExtras(page, name, view) {
  // AK1 (sticky lišta), AK2 (jen dny měsíce), AK4 (region a názvy odkazů +), AK6 (filtry bez skrytého posunu).
  const m = await page.evaluate(view => {
    const tb = document.querySelector('.calendar-toolbar'), main = document.querySelector('main');
    const filters = document.querySelector('.cal-filters-scroll');
    const agenda = document.querySelector('.calendar-mobile-agenda');
    const dates = [...document.querySelectorAll('[data-agenda-date]')].map(n => n.dataset.agendaDate);
    const months = new Set(dates.map(d => d.slice(0, 7)));
    const adds = [...document.querySelectorAll('a.calendar-slot-add, a.cal-add-btn')];
    return {
      toolbarPosition: getComputedStyle(tb).position,
      filtersOverflowX: getComputedStyle(filters).overflowX,
      filtersScrollable: filters.scrollWidth - filters.clientWidth,
      filtersRight: filters.getBoundingClientRect().right, innerWidth,
      mainScrollable: main.scrollHeight - main.clientHeight,
      agendaTag: agenda?.tagName, agendaRole: agenda?.getAttribute('role'), agendaName: agenda?.getAttribute('aria-label'),
      agendaMonths: [...months], agendaCount: dates.length,
      addTotal: adds.length, addUnnamed: adds.filter(a => !(a.getAttribute('aria-label') || '').trim()).length,
    };
  }, view);
  // Lišta přilne až ve chvíli, kdy by přirozená poloha opustila okno, proto se posouvá na konec <main>.
  m.toolbarNaturalOffset = await page.evaluate(() => Math.round(document.querySelector('.calendar-toolbar').getBoundingClientRect().top -
    document.querySelector('main').getBoundingClientRect().top));
  if (m.mainScrollable > m.toolbarNaturalOffset + 20) {
    await page.evaluate(() => { const main = document.querySelector('main'); main.scrollTop = main.scrollHeight; });
    await page.waitForFunction(() => { const main = document.querySelector('main'); return main.scrollTop + main.clientHeight >= main.scrollHeight - 2; });
    m.toolbarOffset = await page.evaluate(() => Math.round(document.querySelector('.calendar-toolbar').getBoundingClientRect().top -
      document.querySelector('main').getBoundingClientRect().top));
    await page.evaluate(() => { document.querySelector('main').scrollTop = 0; });
  }
  const desktop = (await page.viewportSize()).width >= 768;
  const stickyOk = desktop ? (m.toolbarPosition === 'sticky' && m.toolbarOffset !== undefined && m.toolbarOffset >= 0 && m.toolbarOffset <= 2) : true;
  const filtersOk = desktop ? true : (m.filtersOverflowX !== 'auto' && m.filtersScrollable <= 1 && m.filtersRight <= m.innerWidth + 1);
  const monthOk = view !== 'month' || m.agendaMonths.length === 1;
  const a11yOk = view === 'day' ? m.addUnnamed === 0 : m.agendaTag === 'SECTION' && m.agendaRole === 'region' && !!m.agendaName && m.addTotal > 0 && m.addUnnamed === 0;
  record(`${name}-${view}-extras`, stickyOk && filtersOk && monthOk && a11yOk, m);
}
async function checkThumbnail(page, name, tab) {
  await page.goto(detail + '?tab=' + tab);
  for (let version = 0; version < 2; version++) {
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'load' }),
      page.locator('[data-thumbnail-input]').first().setInputFiles(path.join(output, 'thumbnail.png')),
    ]);
    await page.waitForURL(url => url.hash.startsWith('#thumbnail-'));
    await page.waitForLoadState('load');
    await page.locator('[data-thumbnail-current]').first().waitFor();
    const metrics = await dimensions(page);
    metrics.tab = tab;
    record(`${name}-${tab}-upload-${version}`, metrics.scrollY === 0 && fits(metrics) && metrics.scrollHeight <= metrics.height + 1, metrics);
    await shot(page, `${name}-${tab}-upload-${version}`);
  }
  // Skrytý vstup musí jít ovládat i klávesnicí a jeho fokus nesmí posunout dokument.
  await page.locator('[data-thumbnail-input]').first().focus();
  const focused = await dimensions(page);
  record(`${name}-${tab}-focus`, focused.scrollY === 0 && focused.scrollHeight <= focused.height + 1, focused);
}
async function checkChannelPicker(page, name) {
  for (const tab of ['caption', 'comments']) {
    await page.goto(detail + '?tab=' + tab);
    await page.locator('#content-channels summary').click();
    const metrics = await page.locator('#add-channel').evaluate(el => {
      const box = el.getBoundingClientRect(), parent = el.parentElement.getBoundingClientRect();
      return { left: box.left, right: box.right, parentRight: parent.right, width: box.width,
        options: [...el.options].map(option => option.text.length) };
    });
    metrics.page = await dimensions(page);
    record(`${name}-${tab}-channel-picker`, metrics.right <= metrics.parentRight + 1 && metrics.width > 44 &&
      metrics.options.some(length => length >= 90) && fits(metrics.page) &&
      metrics.page.scrollY === 0 && metrics.page.scrollHeight <= metrics.page.height + 1, metrics);
    await shot(page, `${name}-${tab}-channel-picker`);
  }
}
async function checkRelatedScreens(page, name) {
  for (const tab of ['concept', 'script', 'shotlist', 'approval', 'comments', 'history']) {
    await page.goto(detail + '?tab=' + tab);
    await page.getByRole('tabpanel').waitFor();
    // Focus the real comment form, so off-screen accessibility labels and
    // keyboard scrolling are covered as well as the initial page dimensions.
    if (tab === 'comments') await page.locator('#new-comment').focus();
    const metrics = await dimensions(page);
    record(`${name}-${tab}-layout`, metrics.scrollY === 0 && metrics.scrollHeight <= metrics.height + 1 && fits(metrics), metrics);
    await shot(page, `${name}-${tab}-layout`);
  }
  await page.goto(calendar + '?mode=list&view=month');
  await page.locator('#publish-body').waitFor();
  let metrics = await dimensions(page);
  record(`${name}-publish-list`, metrics.scrollY === 0 && metrics.scrollHeight <= metrics.height + 1 && fits(metrics), metrics);
  await shot(page, `${name}-publish-list`);
  // Exercise the partial swap as well as direct navigation to a calendar URL.
  await page.getByRole('button', { name: 'Calendar', exact: true }).click();
  await page.locator('#calendar-content').waitFor();
  const mobile = await page.locator('.calendar-mobile-agenda').isVisible();
  metrics = await dimensions(page);
  record(`${name}-calendar-swap`, mobile === (metrics.width < 768) && metrics.scrollHeight <= metrics.height + 1 && fits(metrics), metrics);
  await shot(page, `${name}-calendar-swap`);
}
async function checkOtherPages(page, name) {
  for (const [key, url, marker] of [
    ['board', `${root}/board/?client=${fixture.workspace}`, '.content-board-page'],
    ['profile', `${root}/workspaces/${fixture.workspace}/profile/`, '.client-profile'],
    ['notifications', `${root}/notifications/`, 'main h2'],
  ]) {
    const response = await page.goto(url);
    // Profil klienta patří vlastníkovi/správci workspace (P1-B23/B25); role client má dostat 403, ne stránku.
    if (key === 'profile' && name.startsWith('client')) {
      record(`${name}-profile-forbidden`, response.status() === 403, { status: response.status() });
      continue;
    }
    await page.locator(marker).first().waitFor();
    const metrics = await dimensions(page);
    record(`${name}-${key}-layout`, metrics.scrollY === 0 && metrics.scrollHeight <= metrics.height + 1 && fits(metrics), metrics);
    await shot(page, `${name}-${key}-layout`);
  }
}
async function session(browser, role, viewport) {
  const context = await browser.newContext({ viewport });
  await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on('pageerror', error => errors.push(error.message));
  if (fixture[role].cookie) {
    await context.addCookies([{ ...fixture[role].cookie, url: origin + '/jrc/' }]);
    await page.goto(root + '/');
    assert(!page.url().includes('/accounts/login/'), 'Testovací relace musí být platná');
    return { context, page };
  }
  await page.goto(root + '/accounts/login/');
  await page.locator('[name=login]').fill(fixture[role].email);
  await page.locator('[name=password]').fill(fixture[role].password);
  await page.locator('button[type=submit]').click();
  await page.waitForURL(url => !url.pathname.includes('/accounts/login/'));
  return { context, page };
}
async function main() {
  const browser = await chromium.launch({ headless: true, args: ['--js-flags=--max-old-space-size=2048'] });
  try {
    if (process.argv.includes('--prepare')) {
      execFileSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=orange:size=32x24',
        '-frames:v', '1', '-threads', '1', path.join(output, 'thumbnail.png')], { timeout: 30000 });
      execFileSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', `testsrc2=size=${portrait ? '180x320' : '320x180'}:rate=12:duration=8`,
        '-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-g', '24', path.join(output, 'video.mp4')], { timeout: 30000 });
      const { context, page } = await session(browser, 'admin', { width: 1440, height: 900 });
      await page.goto(detail + '?tab=video');
      const count = await page.locator('[data-version][data-status=ready]').count();
      await page.locator('[data-video-file]').setInputFiles(path.join(output, 'video.mp4'));
      await page.waitForFunction(count => document.querySelectorAll('[data-version][data-status=ready]').length > count, count, { timeout: 180000 });
      await context.close();
    }
    // --width=N a --role=R dělí dlouhý průchod na části, každá se vejde do limitu 590 s.
    const onlyWidth = (process.argv.find(a => a.startsWith('--width=')) || '').slice(8);
    const onlyRole = (process.argv.find(a => a.startsWith('--role=')) || '').slice(7);
    for (const viewport of [{ width: 390, height: 844 }, { width: 360, height: 740 }, { width: 1440, height: 900 }]) {
      if (onlyWidth && String(viewport.width) !== onlyWidth) continue;
      for (const role of ['admin', 'client']) {
        if (onlyRole && role !== onlyRole) continue;
        const name = `${role}-${viewport.width}`;
        const { context, page } = await session(browser, role, viewport);
        try {
          if (process.argv.includes('--other-pages-only')) { await checkOtherPages(page, name); continue; }
          if (role === 'admin' && !process.argv.includes('--player-only')) await checkChannelPicker(page, name);
          if (process.argv.includes('--channels-only')) continue;
          if (!process.argv.includes('--player-only')) {
            await checkOtherPages(page, name);
            await checkRelatedScreens(page, name);
            for (const view of ['month', 'week', 'day']) await checkCalendar(page, name, view);
            if (role === 'admin') for (const tab of ['caption', 'publish']) await checkThumbnail(page, name, tab);
          }
          await checkPlayer(page, name);
        } finally { await context.close(); }
      }
    }
    const part = process.argv.filter(a => /^--(width|role)=/.test(a)).map(a => a.slice(2).replace('=', '')).join('-');
    const suffix = (part ? '-' + part : '') + (process.argv.includes('--channels-only') ? '-channels' :
      process.argv.includes('--other-pages-only') ? '-other-pages' : process.argv.includes('--player-only') ? '-player' : '');
    fs.writeFileSync(path.join(output, `${before ? 'before' : 'after'}${portrait ? '-portrait' : ''}${suffix}.json`), JSON.stringify({ results, errors }, null, 2));
    assert.deepEqual(errors, []);
    if (!before) {
      assert.equal(results.filter(row => !row.pass && !row.name.endsWith('-player')).length, 0, 'Chyba rozvržení Brightbean');
      // FreeFrame needs an image rebuild forbidden by this block. Keep failures
      // visible and require an explicit flag for a Brightbean-only green run.
      if (!process.argv.includes('--allow-known-ff-blocker')) assert.equal(results.filter(row => !row.pass).length, 0, 'Překryv FreeFrame');
    }
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
