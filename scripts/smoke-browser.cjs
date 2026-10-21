const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 960, height: 540 } });
    await page.goto('data:text/html;charset=utf-8,' + encodeURIComponent(
      '<!doctype html><html lang="cs"><meta charset="utf-8">' +
      '<title>JR Content – test prostředí</title>' +
      '<body style="font:24px sans-serif;padding:48px;background:#f0f5fa">' +
      '<h1>JR Content</h1><p>Chromium běží uvnitř Dockeru.</p></body></html>'
    ));
    assert.equal(await page.title(), 'JR Content – test prostředí');
    assert.equal(await page.locator('h1').textContent(), 'JR Content');
    const path = 'ops/screenshoty/F0-B03/chromium.png';
    await page.screenshot({ path, fullPage: true });
    const screenshot = fs.readFileSync(path);
    assert.deepEqual(screenshot.subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
    console.log(`OK Playwright Chromium ${browser.version()}: stránka a screenshot ${path}`);
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
