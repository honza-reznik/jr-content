const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('/opt/jrc-tools/node_modules/playwright');

const origin = 'http://127.0.0.1:13263';
const credentials = JSON.parse(fs.readFileSync('ops/test-output/F2-B14/credentials.json', 'utf8'));
const cspErrors = [];

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    page.on('console', (message) => {
      if (message.type() === 'error' && /content security policy|refused to execute inline script|tagInput/i.test(message.text())) {
        cspErrors.push(message.text());
      }
    });
    page.on('pageerror', (error) => cspErrors.push(error.message));

    await page.goto(`${origin}/accounts/login/`);
    await page.locator('input[type="email"]').fill(credentials.email);
    await page.locator('input[type="password"]').fill(credentials.password);
    await page.locator('button[type="submit"]').click();
    await page.waitForURL((url) => !url.pathname.startsWith('/accounts/login/'));

    await page.goto(`${origin}/workspace/${credentials.workspace_id}/media/`);
    await page.locator('.ml-asset-card').filter({ hasText: 'F2-B14 CSP test image.png' }).click();
    const editor = page.locator(`#tag-editor-${credentials.asset_id}`);
    const input = editor.locator('input');
    await input.waitFor({ state: 'visible' });

    const saveResponse = page.waitForResponse((response) =>
      response.url().includes(`/workspace/${credentials.workspace_id}/media/${credentials.asset_id}/tags/`)
    );
    await input.fill('csp-ok-3');
    await input.press('Enter');
    assert.equal((await saveResponse).status(), 200);
    await editor.getByText('csp-ok-3', { exact: true }).waitFor({ state: 'visible' });
    assert.deepEqual(cspErrors, []);
    const screenshot = path.join('ops/screenshoty/F2-B14', 'detail-panel.png');
    fs.mkdirSync(path.dirname(screenshot), { recursive: true });
    await page.screenshot({ path: screenshot, fullPage: false });

    assert.deepEqual(cspErrors, []);
    console.log(`OK: štítek přidán bez chyby CSP; snímek ${screenshot}`);
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
