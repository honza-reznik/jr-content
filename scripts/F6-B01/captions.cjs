const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const [workspaceId, postId] = process.argv.slice(2);
if (!workspaceId || !postId) throw new Error('Pass workspace and post UUIDs.');

const base = 'http://127.0.0.1:13061';
const out = '/workspace/ops/screenshoty/F6-B01';
fs.mkdirSync(out, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const { name, width, height } of [
      { name: 'desktop-1440.png', width: 1440, height: 1000 },
      { name: 'phone-390.png', width: 390, height: 1100 },
    ]) {
      const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
      await page.goto(`${base}/accounts/login/`, { waitUntil: 'networkidle' });
      await page.locator('input[name="login"]').fill(process.env.F6B01_TEST_EMAIL);
      await page.locator('input[name="password"]').fill(process.env.F6B01_TEST_PASSWORD);
      await page.locator('button[type="submit"]').click();
      await page.waitForLoadState('networkidle');
      const detail = `${base}/workspace/${workspaceId}/content/${postId}/?tab=caption`;
      const response = await page.goto(detail, { waitUntil: 'networkidle' });
      if (!response || response.status() !== 200) throw new Error(`Caption page returned ${response?.status()}`);
      await page.getByRole('heading', { name: /Channel captions/ }).waitFor();
      await page.screenshot({ path: path.join(out, name), fullPage: true });
      await page.close();
    }
    console.log('OK screenshots: desktop 1440 and phone 390');
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
