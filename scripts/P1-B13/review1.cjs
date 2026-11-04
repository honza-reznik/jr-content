// Skutečné uložení termínu přes Publish na obou šířkách, bez composeru.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('playwright');
assert.equal(process.env.JRC_PROJECT, 'jrc-p1b13');
const fixture = JSON.parse(fs.readFileSync('ops/test-output/P1-B13/review1-fixture.json'));
const root = `http://127.0.0.1:${process.env.JRC_APP_PORT}/jrc`;
const detail = `${root}/workspace/${fixture.workspace}/content/${fixture.post}/?tab=publish`;
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    for (const width of [1440, 390]) {
      const context = await browser.newContext({viewport: {width, height: width === 390 ? 844 : 900}});
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await context.route(/^https?:/, route => new URL(route.request().url()).origin === new URL(root).origin ? route.continue() : route.abort());
      await page.goto(root + '/accounts/login/');
      await page.locator('[name=login]').fill(fixture.editor.email);
      await page.locator('[name=password]').fill(fixture.editor.password);
      await page.locator('button[type=submit]').click();
      await page.waitForURL(url => !url.pathname.includes('/accounts/login/'));
      await page.goto(detail);
      const article = page.locator('article').filter({has: page.locator(`input[value="${fixture.target}"]`)});
      await article.locator('summary').click();
      assert.equal(new URL(page.url()).pathname, new URL(detail).pathname);
      await article.getByLabel('Publication date and time (Europe/Prague)').fill(`2097-01-0${width === 390 ? 3 : 2}T14:30`);
      await page.screenshot({path: `ops/screenshoty/P1-B13/review1-date-form-${width}.png`, fullPage: true});
      const response = page.waitForResponse(r => r.request().method() === 'POST' && r.url().includes('/content/'));
      await article.getByRole('button', {name: 'Save date'}).click();
      assert.equal((await response).status(), 302);
      await page.getByText('Publication date saved.', {exact: false}).waitFor();
      await page.reload();
      const savedArticle = page.locator('article').filter({has: page.locator(`input[value="${fixture.target}"]`)});
      assert.match(await savedArticle.innerText(), new RegExp(`Jan ${width === 390 ? 3 : 2}, 2097 · 2:30 PM CET`));
      await savedArticle.getByText('Final video · v1', {exact: true}).waitFor();
      await page.screenshot({path: `ops/screenshoty/P1-B13/review1-date-saved-${width}.png`, fullPage: true});
      assert.deepEqual(errors, []);
      await context.close();
    }
    console.log('OK: Chromium 1440 a 390 px skutečně uložilo a po reloadu načetlo termín kopie v pásmu Europe/Prague; finální video zachováno.');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exit(1);});
