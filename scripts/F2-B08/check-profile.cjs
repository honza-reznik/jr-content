/* JR Content F2-B08: run Chromium only inside jrc-f2b08 Docker. */
const { chromium } = require('playwright');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');

(async () => {
  const base = 'http://jrc-bb-web:8000';
  const seed = JSON.parse(await fs.readFile('/seed/f2b08-session.json', 'utf8'));
  const output = '/workspace/ops/screenshoty/F2-B08';
  await fs.mkdir(output, {recursive:true});
  const browser = await chromium.launch({headless:true, args:['--no-sandbox']});
  const external = [], failures = [], errors = [], checks = [];
  try {
    for (const width of [1440, 390]) {
      const context = await browser.newContext({viewport:{width, height:width === 390 ? 844 : 1000}});
      await context.route('**/*', route => {
        if (new URL(route.request().url()).origin !== base) {
          external.push(route.request().url());
          return route.abort('blockedbyclient');
        }
        return route.continue();
      });
      await context.addCookies([{name:'sessionid', value:seed.session, url:base}]);
      const page = await context.newPage();
      const capture = async name => {
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `horizontal overflow ${name} ${width}`);
        await page.screenshot({path:`${output}/${name}-${width}.png`});
      };
      page.on('pageerror', error => errors.push(String(error)));
      page.on('response', response => {if(response.status()>=400) failures.push(`${response.status()} ${response.url()}`);});
      const response = await page.goto(`${base}/workspaces/${seed.workspace}/profile/`, {waitUntil:'networkidle'});
      assert.equal(response.status(), 200);
      await page.getByRole('heading', {name:'Ella', exact:true}).waitFor();
      const custom = page.locator('[data-channel="custom"]').first();
      assert.equal(await custom.count(), 1);
      assert.equal(await page.locator('a[href*="/connect/"]').count(), 0);
      await capture('profile');
      await custom.getByText('Edit channel', {exact:false}).click();
      await custom.getByLabel('Channel name', {exact:true}).fill('Ella members');
      await custom.getByLabel('Profile link (optional)', {exact:true}).fill('https://example.invalid/ella/members');
      await custom.getByRole('button', {name:'Save channel', exact:true}).scrollIntoViewIfNeeded();
      await capture('edit');
      await custom.getByRole('button', {name:'Save channel', exact:true}).click();
      await page.waitForLoadState('networkidle');
      await custom.getByRole('heading', {name:'Ella members', exact:true}).waitFor();
      const link = custom.getByRole('link', {name:/Open profile/});
      assert.equal(await link.getAttribute('href'), 'https://example.invalid/ella/members');
      assert.equal(await link.getAttribute('rel'), 'noopener noreferrer');
      await custom.getByRole('button', {name:'Turn off Ella members', exact:true}).click();
      await page.waitForLoadState('networkidle');
      await custom.getByText('Off', {exact:true}).waitFor();
      await custom.getByText('Hidden from new post selections.', {exact:false}).waitFor();
      await custom.getByRole('button', {name:'Turn on Ella members', exact:true}).scrollIntoViewIfNeeded();
      await capture('channel-off');
      await custom.getByRole('button', {name:'Turn on Ella members', exact:true}).click();
      await page.waitForLoadState('networkidle');
      await page.getByText('Add a channel', {exact:true}).click();
      const add = page.locator('.add-channel');
      await add.getByLabel('Channel type', {exact:true}).selectOption('custom');
      await add.getByLabel('Channel name', {exact:true}).fill(`Newsletter ${width}`);
      await add.getByRole('button', {name:'Add channel', exact:true}).scrollIntoViewIfNeeded();
      await capture('add');
      await add.getByRole('button', {name:'Add channel', exact:true}).click();
      await page.waitForLoadState('networkidle');
      await page.getByRole('heading', {name:`Newsletter ${width}`, exact:true}).waitFor();
      // Exercise server-side validation and preservation of submitted values.
      const original = page.locator('[data-channel="custom"]').filter({has:page.getByRole('heading', {name:'Ella members', exact:true})});
      await original.getByText('Edit channel', {exact:false}).click();
      await original.getByLabel('Profile link (optional)', {exact:true}).fill('broken link');
      await page.evaluate(() => document.querySelector('.channel-editor[open] form').noValidate = true);
      await original.getByRole('button', {name:'Save channel', exact:true}).click();
      await page.waitForLoadState('networkidle');
      await original.getByText('Enter a valid URL.', {exact:true}).scrollIntoViewIfNeeded();
      assert.equal(await original.getByLabel('Channel name', {exact:true}).inputValue(), 'Ella members');
      await capture('validation');
      // Restore the seeded channel; all test data is removed when jrc-f2b08 is cleaned up.
      await original.getByLabel('Profile link (optional)', {exact:true}).fill('https://example.invalid/ella/forendors');
      await original.getByLabel('Channel name', {exact:true}).fill('Forendors');
      await original.getByRole('button', {name:'Save channel', exact:true}).click();
      await page.waitForLoadState('networkidle');
      await page.goto(`${base}/workspace/${seed.workspace}/calendar/`, {waitUntil:'networkidle'});
      if (width === 390) {
        await page.getByRole('button', {name:'Open navigation', exact:true}).click();
        await page.waitForFunction(() => Math.abs(document.querySelector('aside').getBoundingClientRect().left)<1);
      }
      await page.getByRole('link', {name:'Client profile', exact:true}).click();
      await page.waitForLoadState('networkidle');
      assert.match(page.url(), /\/profile\/$/);
      checks.push({width, edit:true, toggle:true, create:true, validation:true, navigation:true, horizontalOverflow:false});
      await context.close();
    }
    assert.deepEqual(external, [], 'external requests');
    assert.deepEqual(failures, [], 'HTTP errors');
    assert.deepEqual(errors, [], 'JavaScript errors');
    await fs.writeFile('/workspace/ops/test-output/F2-B08/browser-report.json', JSON.stringify({checks,external,failures,errors}, null, 2));
    console.log('OK: profil, úprava, vypnutí/zapnutí, založení, validace a navigace při 1440 i 390 px; žádný internet, HTTP ani JS chyba.');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exit(1);});
