// AK7: po odmítnutí souboru nesmí v ukazateli zůstat „Checking the video…“. Jen Docker projektu jrc-p1b31.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
assert.equal(process.env.JRC_PROJECT, 'jrc-p1b31');
const output = path.resolve('ops/test-output/P1-B31');
const fixture = JSON.parse(fs.readFileSync(path.join(output, 'fixture.json')));
const origin = 'http://127.0.0.1:' + process.env.JRC_APP_PORT;
const detail = `${origin}/jrc/workspace/${fixture.workspace}/content/${fixture.post}/?tab=video`;
const results = [];
async function state(page) {
  return page.evaluate(() => {
    const card = document.querySelector('[data-video-channel]');
    const progress = card.querySelector('[data-video-progress]');
    const error = card.querySelector('[data-video-error]');
    const visible = el => !!el && !el.classList.contains('hidden') && el.getClientRects().length > 0;
    return { progressVisible: visible(progress), label: card.querySelector('[data-video-progress-label]').textContent,
      errorVisible: visible(error), error: error.textContent,
      versions: [...card.querySelectorAll('[data-version]')].map(li => li.textContent.slice(0, 40)) };
  });
}
async function main() {
  fs.writeFileSync(path.join(output, 'neplatne.mp4'), 'to neni video, jen text '.repeat(2000));
  fs.writeFileSync(path.join(output, 'text.png'), 'not a png');
  const browser = await chromium.launch({ headless: true, args: ['--js-flags=--max-old-space-size=2048'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addCookies([{ ...fixture.admin.cookie, url: origin + '/jrc/' }]);
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(detail);
  await page.locator('[data-video-channel]').first().waitFor();
  const baseline = (await state(page)).versions.length; // už nahrané verze z jiných testů zůstávají
  const labels = [];
  await page.exposeFunction('noteLabel', t => labels.push(t));
  await page.evaluate(() => new MutationObserver(() => noteLabel(document.querySelector('[data-video-progress-label]').textContent + '|' +
    document.querySelector('[data-video-progress]').className)).observe(document.querySelector('[data-video-progress]'), { subtree: true, attributes: true, childList: true, characterData: true }));
  for (const [name, file, expected] of [['mp4', 'neplatne.mp4', /not a playable video/], ['png', 'text.png', /Unsupported file type/], ['mp4-again', 'neplatne.mp4', /not a playable video/]]) {
    await page.locator('[data-video-file]').first().setInputFiles(path.join(output, file));
    await page.waitForFunction(() => {
      const e = document.querySelector('[data-video-error]'); return e && !e.classList.contains('hidden') && e.textContent.trim();
    });
    // Čekat na ustálený stav: žádná aktivní nahrávka a po dokončení obnovy seznamu.
    await page.waitForFunction(() => !document.querySelector('[data-video-progress]:not(.hidden)') ||
      !/Checking|Preparing/.test(document.querySelector('[data-video-progress-label]').textContent), null, { timeout: 15000 }).catch(() => {});
    // Seznam verzí se po odmítnutí obnoví až po zobrazení hlášky; čekat na stav, ne na čas.
    await page.waitForFunction(n => document.querySelectorAll('[data-video-versions] [data-version]').length <= n, null, { timeout: 20000 }).catch(() => {});
    const s = await state(page);
    const pass = expected.test(s.error) && !s.progressVisible && s.label === '' && s.versions.length === baseline;
    results.push({ name, pass, ...s });
    console.log(JSON.stringify(results.at(-1)));
  }
  await page.reload(); await page.locator('[data-video-channel]').first().waitFor();
  console.log('AFTER RELOAD', JSON.stringify(await state(page)));
  console.log('LABEL TRACE', JSON.stringify(labels.slice(-12)));
  await browser.close();
  fs.writeFileSync(path.join(output, 'upload-check.json'), JSON.stringify({ results, errors, labels }, null, 2));
  assert.deepEqual(errors, []);
  assert.ok(results.every(r => r.pass), 'AK7 selhalo');
}
main().catch(e => { console.error(e); process.exitCode = 1; });
