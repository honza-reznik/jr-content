// Jen Docker jrc-f3b02; místní prohlížeč a vymyšlená data, žádné externí požadavky.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');

const fixtureText = fs.readFileSync('ops/test-output/F3-B02/fixture.log', 'utf8');
const fixture = JSON.parse(fixtureText.split('\n').find(line => line.startsWith('F3B02_FIXTURE=')).slice('F3B02_FIXTURE='.length));
const base = 'http://jrc-bb-web:8000';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const foreign = [], errors = [];
  const testTitle = `Ella browser test idea ${Date.now()}`;
  async function session(role, width = 1440) {
    const context = await browser.newContext({ viewport: { width, height: 1000 } });
    await context.addCookies([{ name: 'sessionid', value: fixture.sessions[role], url: base }]);
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin !== base) {
        foreign.push(route.request().url());
        return route.abort();
      }
      return route.continue();
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${base}/board/?client=${fixture.workspace}`, { waitUntil: 'networkidle' });
    return { page, context };
  }
  async function move(page, title, stage, date) {
    const card = page.locator('.board-card').filter({ has: page.getByRole('heading', { name: title, exact: true }) });
    await card.locator('summary').click();
    await card.getByLabel('Move to', { exact: true }).selectOption(stage);
    if (date) await card.locator('input[type="datetime-local"]').fill(date);
    await card.getByRole('button', { name: 'Confirm move' }).click();
    await page.waitForLoadState('networkidle');
  }
  try {
    const { page: admin } = await session('admin');
    assert.equal(await admin.locator('.board-column').count(), 8);
    const initialCount = await admin.locator('.board-card').count();
    assert(initialCount >= 8);
    assert.equal(await admin.locator('.board-column').evaluateAll(cols => new Set(cols.map(c => Math.round(c.getBoundingClientRect().top))).size), 1);
    await admin.screenshot({ path: 'ops/screenshoty/F3-B02/desktop-1440.png', fullPage: true });

    const { page: client, context: mobileContext } = await session('client', 390);
    assert(await client.getByText('+ New content', { exact: true }).isVisible());
    await client.getByLabel('Show stage').selectOption('review');
    assert.equal(await client.locator('.board-column:visible').count(), 1);
    assert(await client.getByRole('heading', { name: 'Three things I wish I knew', exact: true }).isVisible());
    await client.screenshot({ path: 'ops/screenshoty/F3-B02/mobile-390-review.png', fullPage: true });
    await client.getByLabel('Show stage').selectOption('idea');
    assert(await client.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await client.screenshot({ path: 'ops/screenshoty/F3-B02/mobile-390.png', fullPage: true });

    await client.getByText('+ New content', { exact: true }).click();
    await client.getByLabel('Content title', { exact: true }).fill(testTitle);
    await client.getByRole('button', { name: 'Add to Idea' }).click();
    await client.waitForLoadState('networkidle');
    await move(client, testTitle, 'script');
    await move(client, testTitle, 'shooting');
    await move(client, testTitle, 'editing');
    await move(client, testTitle, 'review');
    // V povinném dvoukrokovém režimu klient nesmí schválit interní kontrolu.
    let clientCard = client.locator('.board-card').filter({ hasText: testTitle });
    assert.equal(await clientCard.locator('summary').count(), 0);
    await admin.reload({ waitUntil: 'networkidle' });
    await move(admin, testTitle, 'approved');
    assert(await admin.locator('.board-card').filter({ hasText: testTitle }).getByText('Awaiting client sign-off').isVisible());
    await client.reload({ waitUntil: 'networkidle' });
    await move(client, testTitle, 'approved');
    clientCard = client.locator('.board-card').filter({ hasText: testTitle });
    assert.equal(await clientCard.locator('option[value="ready"]').count(), 0);
    await admin.reload({ waitUntil: 'networkidle' });
    await move(admin, testTitle, 'ready', '2030-01-05T12:30');
    assert(await admin.locator('[data-stage="ready"]').getByRole('heading', { name: testTitle, exact: true }).isVisible());
    await admin.locator('#filter-channel').selectOption({ label: 'Ella · Forendors' });
    await admin.getByRole('button', { name: 'Apply filters' }).click();
    await admin.waitForLoadState('networkidle');
    assert.equal(await admin.locator('.board-card').count(), initialCount + 1);
    await mobileContext.close();
    assert.deepEqual(foreign, [], 'Cizí požadavky');
    assert.deepEqual(errors, [], 'Chyby JavaScriptu');
    console.log('OK: 8 sloupců na desktopu, telefon 390 bez přetečení, založení klientem, výrobní fáze, interní i klientské schválení, plánování adminem, filtr kanálu, žádné externí požadavky ani chyby JS.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
