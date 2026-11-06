// Jen přes run-browser.sh: Docker, limit paměti, časový limit. Vymyšlená data projektu jrc-p1b29.
const { chromium } = require('/opt/jrc-tools/node_modules/playwright');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
(async () => {
  const origin = 'http://127.0.0.1:13713';
  const base = origin + '/jrc';
  const seed = JSON.parse(await fs.readFile('/workspace/ops/test-output/P1-B29/seed.json', 'utf8'));
  const shot = (page, name) => page.screenshot({path: `/workspace/ops/screenshoty/P1-B29/${name}.png`, fullPage: true});
  const browser = await chromium.launch({args: ['--js-flags=--max-old-space-size=512']});
  const timer = setTimeout(() => process.exit(124), 300000);
  const results = [];
  try {
    const context = await browser.newContext({viewport: {width: 1365, height: 900}});
    await context.addCookies([{name: 'sessionid', value: seed.session, url: origin}]);
    await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    const page = await context.newPage();
    page.setDefaultTimeout(15000); page.setDefaultNavigationTimeout(30000);
    const list = base + '/organizations/api-keys/';
    assert.equal((await page.goto(list)).status(), 200);
    await page.getByRole('button', {name: 'Issue new key'}).click();
    // AK3: nabídka workspace jen z členství
    const options = await page.locator('#workspace_id option').allInnerTexts();
    results.push({step: 'workspace options', options});
    assert(options.some(o => o.includes('Vlastni')), 'own workspace offered');
    assert(!options.some(o => o.includes('Cizi')), 'foreign workspace not offered');
    await page.selectOption('#workspace_id', {label: options.find(o => o.includes('Vlastni'))});
    await page.locator('#workspace-options-target input[name="social_account_ids"]').first().waitFor();
    const text = await page.locator('#workspace-options-target').innerText();
    assert(text.includes('kanal vlastni') && !text.includes('CIZI'));
    await shot(page, '01-formular-vlastni-workspace');
    // AK2: min u data a odmítnutí na serveru
    await page.fill('#name', 'B29 bez opravneni');
    await page.locator('#workspace-options-target input[name="social_account_ids"]').first().check();
    await page.locator('form[action$="/issue/"] button[type="submit"]').click();
    await page.getByText('Select at least one permission.').first().waitFor();
    await shot(page, '02-bez-opravneni-odmitnuto');
    results.push({step: 'no permission refused'});
    await page.getByRole('button', {name: 'Issue new key'}).click();
    await page.fill('#name', 'B29 minulost');
    await page.selectOption('#workspace_id', {label: options.find(o => o.includes('Vlastni'))});
    await page.locator('#workspace-options-target input[name="social_account_ids"]').first().check();
    await page.locator('#workspace-options-target input[name="permissions"]').first().check();
    await page.fill('#expires_at', '2020-01-01');
    const valid = await page.locator('#expires_at').evaluate(el => el.validity.rangeUnderflow);
    assert(valid, 'browser blocks a past date through min');
    results.push({step: 'browser min blocks past date', rangeUnderflow: valid});
    await page.locator('#expires_at').evaluate(el => el.removeAttribute('min'));  // obejití prohlížeče: musí zastavit server
    await page.locator('form[action$="/issue/"] button[type="submit"]').click();
    await page.getByText('The expiry date must be in the future.').first().waitFor();
    await shot(page, '03-minule-datum-odmitnuto-serverem');
    results.push({step: 'past date refused by server'});
    // platný klíč
    await page.getByRole('button', {name: 'Issue new key'}).click();
    await page.fill('#name', 'B29 platny');
    await page.selectOption('#workspace_id', {label: options.find(o => o.includes('Vlastni'))});
    await page.locator('#workspace-options-target input[name="social_account_ids"]').first().check();
    await page.locator('#workspace-options-target input[name="permissions"]').first().check();
    await page.locator('form[action$="/issue/"] button[type="submit"]').click();
    await page.locator('#reveal-token').waitFor();
    const token = (await page.locator('#reveal-token').innerText()).trim();
    assert(token.startsWith('bb_studio_'));
    await fs.writeFile('/workspace/ops/test-output/P1-B29/token-browser', token, {mode: 0o600});
    await shot(page, '04-klic-vydan');  // token je na snímku viditelný jen v ignorované složce
    // AK1: jedno volání API přes proxy; hlavičku jinak přidává Funnel/Serve
    const api = base + '/api/v1/me/';
    const call = async (headers) => page.evaluate(async ({api, headers}) => (await fetch(api, {headers, credentials: 'omit'})).status, {api, headers});
    const auth = {Authorization: 'Bearer ' + token};
    const plain = await call(auth);
    await context.setExtraHTTPHeaders({'X-Forwarded-Proto': 'https'});
    const viaProxy = await call(auth);
    await context.setExtraHTTPHeaders({});
    results.push({step: 'api', plain, withForwardedHttps: viaProxy});
    assert.equal(plain, 401); assert.equal(viaProxy, 200);
    await context.close();
    await fs.writeFile('/workspace/ops/test-output/P1-B29/browser.json', JSON.stringify(results, null, 2));
    console.log(JSON.stringify(results));
  } finally { clearTimeout(timer); await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
