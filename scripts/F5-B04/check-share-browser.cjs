// Chromium v Dockeru; jen místní jrc-f5b04 a vymyšlená data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
assert.equal(process.env.JRC_PROJECT, 'jrc-f5b04');
assert.equal(process.env.JRC_APP_PORT, '13243');
assert.equal(process.env.JRC_S3_PORT, '13963');
const fixture = JSON.parse(fs.readFileSync(process.env.F5B04_FIXTURE, 'utf8'));
const origin = 'http://127.0.0.1:13243';
const api = origin + '/video-api';
const s3 = 'http://127.0.0.1:13963';
const output = '/jrc/ops/screenshoty/F5-B04';
fs.mkdirSync(output, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  const requests = [], failures = [], errors = [], downloads = [];
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    assert([origin, s3].includes(url.origin), 'Požadavek mimo vlastní aplikaci');
    return route.continue();
  });
  await context.addInitScript(access => {
    localStorage.setItem('ff_access_token', access);
    localStorage.setItem('ff_session_version', 'browser-regression');
  }, fixture.access);
  const page = await context.newPage();
  page.setDefaultTimeout(60000);
  page.on('pageerror', error => errors.push(error.message));
  page.on('download', download => downloads.push(download));
  page.on('request', request => {
    if (request.url().startsWith(`${api}/share/`)) requests.push(request);
  });
  page.on('response', response => {
    if (response.url().startsWith(api) && response.status() >= 400) failures.push([new URL(response.url()).pathname, response.status()]);
  });
  const shareUrl = `${origin}/video/share/${fixture.share}`;
  try {
    await page.goto(shareUrl);
    await page.getByText('Photo 01.png', { exact: true }).first().waitFor();
    const nextPage = page.waitForResponse(r => new URL(r.url()).searchParams.get('page') === '2' && r.status() === 200);
    await page.getByRole('button', { name: 'Load more', exact: true }).click();
    await nextPage;
    assert(requests.some(r => new URL(r.url()).searchParams.get('page') === '2'), 'Chybí druhá stránka');
    console.log('OK Chromium: člen načetl sdílenou složku i další stránku.');
    await page.getByText('Photo 01.png', { exact: true }).first().click();
    await page.getByText('Existing member comment', { exact: true }).first().waitFor();
    await page.screenshot({ path: `${output}/share-folder.png` });
    await page.getByText('Photo 01.png', { exact: true }).first().dblclick();
    await page.getByPlaceholder('Leave your comment...').first().waitFor();
    await page.getByText('Existing member comment', { exact: true }).first().waitFor();
    const posted = page.waitForResponse(r => r.url().includes('/comment?') && r.request().method() === 'POST' && r.status() === 201);
    await page.getByPlaceholder('Leave your comment...').first().fill('Chromium member regression');
    await page.getByTitle('Send (Enter)').first().click();
    await posted;
    await page.getByText('Chromium member regression', { exact: true }).first().waitFor();
    console.log('OK Chromium: komentáře v náhledu i přehrávači a odeslání komentáře.');
    const switcher = page.getByRole('button', { name: /v2/i });
    await switcher.click();
    await page.getByRole('menuitem', { name: /v1/i }).click();
    await page.waitForFunction(() => document.querySelector('img')?.complete);
    await page.getByRole('button', { name: 'Download', exact: true }).click();
    await page.getByRole('menuitem').first().waitFor();
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('menuitem').first().click();
    const download = await downloadPromise;
    assert.equal(await download.failure(), null);
    assert(download.suggestedFilename().endsWith('.png'));
    await page.screenshot({ path: `${output}/share-review.png` });
    console.log('OK Chromium: verze obrázku, nabídka variant a skutečné stažení souboru.');
    await page.goto(shareUrl);
    await page.getByRole('button', { name: /Nested audio/ }).dblclick();
    await page.getByText('Nested tone.mp3', { exact: true }).first().waitFor();
    await page.getByText('Nested tone.mp3', { exact: true }).first().dblclick();
    await page.getByPlaceholder('Leave your comment...').first().waitFor();
    await page.getByTitle('Play (Space)').click();
    await page.getByTitle('Pause (Space)').waitFor();
    await page.screenshot({ path: `${output}/share-audio.png` });
    console.log('OK Chromium: vnořená složka a skutečné načtení audia.');
    await page.goto(shareUrl);
    await page.getByRole('button', { name: 'Download All', exact: true }).click();
    for (let i = 0; i < 150 && requests.filter(r => r.url().includes('prefer_original=true')).length < 26; i++) {
      await page.waitForTimeout(200);
    }
    // All URLs are fetched before the UI starts sequential downloads.
    assert.equal(requests.filter(r => r.url().includes('prefer_original=true')).length, 26);
    assert(requests.some(r => r.url().includes('per_page=500') && r.url().includes('folder_id=')));
    await page.waitForTimeout(23000);
    assert(downloads.length >= 27, `Staženo ${downloads.length}, očekáváno alespoň 27`);
    for (const download of downloads) assert.equal(await download.failure(), null);
    console.log('OK Chromium: Download All rekurzivně načetl podsložku a stáhl 26 souborů.');
    for (const request of requests) assert.equal(await request.headerValue('authorization'), `Bearer ${fixture.access}`);
    assert.deepEqual(failures, []);
    assert.deepEqual(errors, []);
    const revoked = await context.request.delete(`${api}/projects/${fixture.project}/members/${fixture.user}`, {
      headers: { Authorization: `Bearer ${fixture.admin}` },
    });
    assert.equal(revoked.status(), 204);
    const denied = await context.request.get(`${api}/share/${fixture.share}/assets`, {
      headers: { Authorization: `Bearer ${fixture.access}` },
    });
    assert.equal(denied.status(), 403);
    const anonymous = await browser.newContext();
    await anonymous.route('**/*', route => {
      assert([origin, s3].includes(new URL(route.request().url()).origin));
      return route.continue();
    });
    const anonymousPage = await anonymous.newPage();
    const anonymousResponse = anonymousPage.waitForResponse(r => r.url().startsWith(`${api}/share/${fixture.share}`));
    await anonymousPage.goto(shareUrl);
    assert.equal((await anonymousResponse).status(), 403);
    await anonymous.close();
    console.log(`OK Chromium ${browser.version()}: ${requests.length} share požadavků s aktuální relací, odebrání i anonymní přístup odmítnuty.`);
  } catch (error) {
    await page.screenshot({ path: `${output}/share-failure.png`, fullPage: true });
    console.error('Chybné odpovědi API:', JSON.stringify(failures));
    console.error('Chyby prohlížeče:', JSON.stringify(errors));
    console.error('Viditelný obsah:', (await page.locator('body').innerText()).slice(0, 4500));
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
