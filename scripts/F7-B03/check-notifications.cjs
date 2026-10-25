// F7-B03: seznam upozornění v prohlížeči. Jen Docker, vlastní projekt jrc-f7b03, vymyšlené účty.
// Tým (manažer) vidí všechna upozornění s odkazy na správné záložky; Ella (klient) jen žádost o schválení
// a verzi z fáze Review, nic z fáze Idea; klik na odkaz otevře detail obsahu na správné záložce. 1440 i 390 px.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

assert.equal(process.env.JRC_PROJECT, 'jrc-f7b03');
assert.equal(process.env.JRC_APP_PORT, '13403');
const origin = `http://127.0.0.1:${process.env.JRC_APP_PORT}`;
const output = path.resolve('ops/test-output/F7-B03');
const shots = path.resolve('ops/screenshoty/F7-B03');
fs.mkdirSync(output, { recursive: true });
fs.mkdirSync(shots, { recursive: true });
const fixture = JSON.parse(fs.readFileSync(path.join(output, 'fixture.json'), 'utf8'));
const errors = [];

async function login(browser, who) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(`${who}: ${error.message}`));
  page.on('console', message => { if (message.type() === 'error') errors.push(`${who}: ${message.text()}`); });
  await page.goto(`${origin}/accounts/login/`);
  await page.locator('input[name="login"]').fill(fixture[who].email);
  await page.locator('input[name="password"]').fill(fixture[who].password);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL(url => !url.pathname.startsWith('/accounts/login'));
  return page;
}

async function shoot(page, who, name) {
  for (const [label, width, height] of [['1440', 1440, 1100], ['390', 390, 1000]]) {
    await page.setViewportSize({ width, height });
    await page.goto(`${origin}/notifications/`);
    await page.getByText('Notifications', { exact: false }).first().waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true,
      `${who} ${label}: vodorovné posouvání`);
    await page.screenshot({ path: path.join(shots, `${name}-${label}.png`), fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 1100 });
}

(async () => {
  const browser = await chromium.launch();
  try {
    const manager = await login(browser, 'manager');
    await manager.goto(`${origin}/notifications/`);
    const team = await manager.locator('body').innerText();
    // Názvy typů jsou i ve filtru, proto hledáme věty z těl upozornění.
    for (const text of ['of the script for "Ella garden reel"', 'caption (Ella Instagram)', 'uploaded video version 1',
      'Client requested changes', 'Please cut the ending at 0:17.']) assert(team.includes(text), `Tým: chybí "${text}"`);
    const hrefs = await manager.locator('a[href*="/content/"]').evaluateAll(links => links.map(a => a.getAttribute('href')));
    for (const tab of ['script', 'caption', 'video', 'approval']) {
      assert(hrefs.some(h => h.includes(`/workspace/${fixture.workspace}/content/${fixture.post}/?tab=${tab}`)),
        `Tým: chybí odkaz na záložku ${tab}`);
    }
    await shoot(manager, 'manager', 'team-list');
    await manager.goto(`${origin}/notifications/`);
    await manager.locator('a', { hasText: 'New script version' }).first().click();
    await manager.waitForURL(/tab=script/);
    assert(await manager.locator('body').innerText().then(t => t.includes('Ella garden reel')));
    await manager.goto(`${origin}/notifications/`);
    await manager.locator('a', { hasText: 'Client requested changes' }).first().click();
    await manager.waitForURL(/tab=approval/);

    const ella = await login(browser, 'client');
    await ella.goto(`${origin}/notifications/`);
    const client = await ella.locator('body').innerText();
    assert(client.includes('Posts ready for your review'), 'Klient: chybí žádost o schválení');
    assert.equal((client.match(/saved version/g) || []).length, 1, 'Klient: jen verze z fáze Review');
    assert(client.includes('saved version 3 of the script'), 'Klient: chybí verze scénáře z fáze Review');
    assert(!client.includes('caption (Ella Instagram)'), 'Klient: popisek byl uložen ve fázi Idea');
    assert(!client.includes('Client requested changes'), 'Klient: cizí rozhodnutí');
    await shoot(ella, 'client', 'client-list');
    await ella.goto(`${origin}/notifications/`);
    await ella.locator('a', { hasText: 'Posts ready for your review' }).first().click();
    await ella.waitForURL(/tab=approval/);

    const viewer = await login(browser, 'viewer');
    await viewer.goto(`${origin}/notifications/`);
    const reader = await viewer.locator('body').innerText();
    assert(!reader.includes('Posts ready for your review'), 'Čtenář nerozhoduje');
    assert(reader.includes('saved version 3 of the script'), 'Čtenář: verze z fáze Review');
  } finally {
    await browser.close();
  }
  assert.deepEqual(errors, [], `Chyby konzole: ${errors.join('; ')}`);
  console.log('OK: seznam upozornění pro tým, klienta a čtenáře, odkazy na záložky, 1440 i 390 px');
})().catch(error => { console.error(error); process.exit(1); });
