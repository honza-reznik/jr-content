/* JR Content: F2-B10, prohlížeč spouštět pouze v Dockeru. */
const { chromium } = require('playwright');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');

(async () => {
  const base = 'http://jrc-bb-web:8000';
  const seed = JSON.parse(await fs.readFile('/seed/f2b10-ui-session.json', 'utf8'));
  const out = '/workspace/ops/screenshoty/F2-B10';
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const requests = [], external = [], failures = [], errors = [];
  try {
    for (const width of [1440, 390]) {
      const context = await browser.newContext({ viewport: {width, height: width === 390 ? 844 : 1000} });
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        requests.push(url.href);
        if (url.origin !== base) {
          external.push(url.href);
          return route.abort('blockedbyclient');
        }
        return route.continue();
      });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(String(error)));
      page.on('response', response => { if (response.status() >= 400) failures.push(`${response.status()} ${response.url()}`); });
      let response = await page.goto(`${base}/accounts/login/`, {waitUntil: 'networkidle'});
      assert.equal(response.status(), 200);
      assert.match(await page.title(), /JR Content/);
      assert.equal(await page.locator('form[action*="/accounts/google/"]').count(), 0);
      await page.screenshot({path: `${out}/login-${width}.png`, fullPage: true});
      await context.addCookies([{name:'sessionid', value:seed.session, url:base}]);
      const pages = {
        calendar: `/workspace/${seed.workspace}/calendar/`,
        compose: `/workspace/${seed.workspace}/compose/`,
        media: `/workspace/${seed.workspace}/media/`,
        settings: `/workspaces/${seed.workspace}/settings/`,
        about: '/about/',
        'api-docs': '/api/v1/docs',
      };
      for (const [name, path] of Object.entries(pages)) {
        response = await page.goto(`${base}${path}`, {waitUntil:'networkidle'});
        assert.equal(response.status(), 200, `${name} ${width}`);
        assert.doesNotMatch(await page.title(), /Bright[Bb]ean/, `${name} title`);
        if (name !== 'api-docs') {
          assert.equal(await page.locator('a[href*="/connect/"], a[href*="/inbox/"], a[href*="/analytics/"], a[href*="/ideas/"], a[href*="/settings/clients/"]').count(), 0, `${name} hidden links`);
          assert.equal(await page.locator('#onboarding-checklist').count(), 0);
        }
        await page.screenshot({path: `${out}/${name}-${width}.png`, fullPage:true});
        if (name === 'calendar' && width === 390) {
          await page.getByRole('button', {name:'Open navigation', exact:true}).click();
          await page.waitForFunction(() => Math.abs(document.querySelector('aside').getBoundingClientRect().left) < 1);
          await page.screenshot({path: `${out}/navigation-${width}.png`, fullPage:true});
          await page.keyboard.press('Escape');
        }
        if (name === 'calendar') {
          assert.equal(await page.evaluate(() => typeof flatpickr), 'function');
          assert.equal(await page.evaluate(() => typeof Chart), 'function');
          assert.equal(await page.evaluate(() => typeof Sortable), 'function');
          await page.getByRole('link', {name:'Create a post', exact:true}).click();
          await page.waitForLoadState('networkidle');
          assert.match(page.url(), /\/compose\/$/);
        }
      }
      await context.close();
    }
    assert.deepEqual(external, [], 'External requests');
    assert.deepEqual(failures, [], 'HTTP failures');
    assert.deepEqual(errors, [], 'JavaScript errors');
    const report = {requests: [...new Set(requests)], external, failures, errors, viewports:[1440,390]};
    await fs.writeFile('/workspace/ops/test-output/F2-B10/browser-report.json', JSON.stringify(report, null, 2));
    console.log(`OK: oba rozměry, ${requests.length} místních požadavků, žádná cizí doména, HTTP ani JS chyba.`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
