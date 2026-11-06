// Jen přes run-browser.sh: Docker, limit paměti, časový limit. Vymyšlená data projektu jrc-p1b33.
const { chromium } = require('/opt/jrc-tools/node_modules/playwright');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
(async () => {
  const origin = 'http://127.0.0.1:13753';
  const base = origin + '/jrc';
  const env = Object.fromEntries((await fs.readFile('/workspace/ops/test-output/P1-B33/accounts.env', 'utf8'))
    .split('\n').filter(l => l.includes('=')).map(l => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1)]));
  const shot = (page, name) => page.screenshot({path: `/workspace/ops/screenshoty/P1-B33/${name}.png`, fullPage: true});
  const browser = await chromium.launch({args: ['--js-flags=--max-old-space-size=512']});
  const timer = setTimeout(() => process.exit(124), 500000);
  const results = [];
  const login = async (who) => {
    const context = await browser.newContext({viewport: {width: 1365, height: 900}});
    await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    const page = await context.newPage();
    page.setDefaultTimeout(15000); page.setDefaultNavigationTimeout(30000);
    await page.goto(base + '/accounts/login/');
    await page.fill('input[name="login"]', env[who + '_EMAIL']);
    await page.fill('input[name="password"]', env[who + '_PASSWORD']);
    await Promise.all([page.waitForNavigation(), page.locator('form button[type="submit"]').first().click()]);
    return page;
  };
  try {
    // 1. Klient: Request changes
    let page = await login('KLIENT');
    results.push({step: 'client logged in', url: page.url()});
    await page.goto(base + `/workspace/${env.WS}/content/${env.POST_A}/?tab=approval`);
    await shot(page, '01-klient-schvaleni');
    await page.fill('#approval-comment', 'TEST B33 prosím zkrátit konec');
    const t0 = Date.now();
    const [resp] = await Promise.all([
      page.waitForResponse(r => r.url().includes('/approval/') && r.request().method() === 'POST'),
      page.getByRole('button', {name: 'Request changes'}).click(),
    ]);
    await page.waitForLoadState('load');
    const ms = Date.now() - t0;
    results.push({step: 'client request changes', status: resp.status(), ms_to_page_loaded: ms});
    assert(ms < 1500, 'Request changes must answer in about a second: ' + ms);
    await shot(page, '02-klient-po-request-changes');
    await page.context().close();

    // 2. Owner: pozvánka člena
    page = await login('OWNER');
    await page.goto(base + '/members/');
    await shot(page, '03-owner-clenove');
    await page.getByRole('button', {name: /invite/i}).first().click();
    await page.fill('#invite-email', 'pozvany-b33@example.invalid');
    const t1 = Date.now();
    const [iresp] = await Promise.all([
      page.waitForResponse(r => r.url().includes('/members/invite/') && r.request().method() === 'POST'),
      page.locator('form[hx-post] button[type="submit"]').first().click(),
    ]);
    await page.waitForTimeout(200);
    const ims = Date.now() - t1;
    results.push({step: 'owner invite', status: iresp.status(), ms: ims});
    assert(ims < 1500, 'invite must answer in about a second: ' + ims);
    await shot(page, '04-owner-po-pozvance');

    // 3. Reset hesla: existující a neexistující adresa
    const times = {};
    for (const [label, addr] of [['known', env.KLIENT_EMAIL], ['unknown', 'nikdo-b33@example.invalid']]) {
      const ctx = await browser.newContext(); const p = await ctx.newPage();
      await p.goto(base + '/accounts/password/reset/');
      await p.fill('input[name="email"]', addr);
      const s = Date.now();
      await Promise.all([p.waitForNavigation(), p.locator('form button[type="submit"]').first().click()]);
      times[label] = Date.now() - s;
      await ctx.close();
    }
    results.push({step: 'password reset timings', times});
    console.log(JSON.stringify(results, null, 1));
  } finally {
    clearTimeout(timer);
    await browser.close();
  }
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
