// P1-B37: živé ověření v režimu "Internal review + client approval" – úprava naplánovaného a na klienta čekajícího
// obsahu (AK1: detail JR, editor/composer) a nová Final verze videa a nový náhled po schválení (AK2). Skutečná
// klepnutí, skutečné nahrání lokálního testovacího videa. Docker runner dává cgroup, limit paměti Node a tvrdý strop.
const {chromium} = require('/opt/jrc-tools/node_modules/playwright');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
const phase = process.argv[2];
const out = '/workspace/ops/test-output/P1-B37', shots = '/workspace/ops/screenshoty/P1-B37';
const media = '/media';
(async () => {
  await fs.mkdir(shots, {recursive: true});
  const seed = JSON.parse(await fs.readFile(`${out}/session.json`, 'utf8'));
  const origin = 'http://127.0.0.1:13793', s3 = 'http://127.0.0.1:14513', base = origin + '/jrc';
  const browser = await chromium.launch({args: ['--js-flags=--max-old-space-size=512']});
  const deadline = setTimeout(() => process.exit(124), 500000);
  const results = [];
  const check = (name, actual, expected) => {
    const ok = JSON.stringify(actual) === JSON.stringify(expected);
    results.push({name, actual, expected, ok});
    console.log((ok ? 'ok   ' : 'FAIL ') + name + (ok ? '' : ` :: ${JSON.stringify(actual)}`));
    if (phase === 'after') assert.deepEqual(actual, expected, name);
  };
  const ws = seed.workspace;
  const detail = post => `${base}/workspace/${ws}/content/${post}/`;
  async function session(role) {
    const ctx = await browser.newContext({viewport: {width: 1365, height: 900}});
    await ctx.addCookies([{name: 'sessionid', value: seed.sessions[role], url: origin}]);
    // Only the app and its own S3 (video upload parts); nothing leaves the machine.
    await ctx.route('**/*', r => [origin, s3].includes(new URL(r.request().url()).origin) ? r.continue() : r.abort());
    const page = await ctx.newPage();
    page.setDefaultTimeout(20000); page.setDefaultNavigationTimeout(30000);
    const errors = [];
    page.on('console', m => { if (m.type() === 'error') errors.push(page.url() + ' :: ' + m.text().slice(0, 200)); });
    page.on('response', r => { if (r.status() >= 500) errors.push(`${r.status()} ${r.url()}`); });
    return {ctx, page, errors};
  }
  const owner = await session('owner');
  const page = owner.page;
  const body = async () => (await page.locator('body').innerText()).replace(/\s+/g, ' ');

  // AK1 a) naplánovaný kanál, záložka Caption: nápověda, uložení, hláška.
  {
    await page.goto(`${detail(seed.planned.post)}?tab=caption`);
    const card = page.locator('article').filter({hasText: 'B37 Instagram'});
    check('planned: hint before saving', (await card.locator('[data-reopens-review]').innerText()).trim(),
      'Planned · saving a changed caption takes it out of the plan and sends it back for re-approval');
    await page.screenshot({path: `${shots}/${phase}-1-planned-caption-before.png`, fullPage: true});
    await card.locator('textarea[name="caption"]').fill('Schválený popisek – opravený překlep');
    await Promise.all([page.waitForNavigation(), card.getByRole('button', {name: 'Save version'}).click()]);
    const text = await body();
    const shown = text.match(/Caption saved[^.]*\.[^]*?approved\./);
    results.push({name: 'planned: message as shown', actual: shown ? shown[0] : text.slice(0, 600)});
    check('planned: message names the plan', text.includes(
      'Caption saved as a new version. Custom channel · B37 Instagram was taken out of the plan because the caption changed. ' +
      'It goes back to review for approval; plan it again once it is approved.'), true);
    await page.screenshot({path: `${shots}/${phase}-2-planned-caption-after.png`, fullPage: true});
    const tiktok = page.locator('article').filter({hasText: 'B37 TikTok'});
    check('planned: other channel still shows planned hint', await tiktok.locator('[data-reopens-review]').count(), 1);
  }

  // AK1 b) obsah čekající na klienta.
  {
    await page.goto(`${detail(seed.client_wait.post)}?tab=caption`);
    const card = page.locator('article').filter({hasText: 'B37 Instagram'});
    check('client wait: hint', (await card.locator('[data-reopens-review]').innerText()).trim(),
      'Waiting for the client · saving a changed caption sends it back for re-approval');
    await card.locator('textarea[name="caption"]').fill('Klient ještě neschválil – úprava týmu');
    await Promise.all([page.waitForNavigation(), card.getByRole('button', {name: 'Save version'}).click()]);
    check('client wait: message', (await body()).includes(
      'Custom channel · B37 Instagram goes back to review for approval because the caption changed.'), true);
    await page.screenshot({path: `${shots}/${phase}-3-client-wait-after.png`, fullPage: true});
  }

  // AK1 c) editor (composer): naplánovaný obsah, úprava textu a "Save draft".
  {
    await page.goto(`${base}/workspace/${ws}/compose/${seed.composer.post}/`);
    check('composer: planned hint', (await page.locator('[data-planned-edit-hint]').innerText()).replace(/\s+/g, ' ').trim(),
      'This post is scheduled. Saving changes takes it out of the plan and sends it back for re-approval.');
    const caption = page.locator('textarea[name="caption"]').first();
    await caption.fill('Úprava v editoru po naplánování');
    const [saved] = await Promise.all([
      page.waitForResponse(r => r.url().includes(`/compose/${seed.composer.post}/save/`) && r.request().method() === 'POST'),
      page.locator('button[name="action"][value="save_draft"]').first().click(),
    ]);
    check('composer: save status', saved.status(), 204);
    const notice = page.locator('[data-review-notice]');
    await notice.waitFor({state: 'visible'});
    check('composer: notice', (await notice.innerText()).replace(/\s+/g, ' ').replace(' Dismiss', '').trim(),
      'Custom channel · B37 Instagram was taken out of the plan because the content changed. ' +
      'It goes back to review for approval; plan it again once it is approved.');
    await page.screenshot({path: `${shots}/${phase}-4-composer-notice.png`, fullPage: false});
  }

  // AK2 a) nový náhled naplánovaného kanálu.
  {
    await page.goto(`${detail(seed.thumb.post)}?tab=caption`);
    const form = page.locator('[data-thumbnail-form]').first();
    // Choosing the image uploads it right away (thumbnail.js submits the form).
    await Promise.all([page.waitForNavigation(), form.locator('input[name="image"]').setInputFiles(`${media}/nahled.png`)]);
    check('thumbnail: message', (await body()).includes(
      'Thumbnail uploaded as version 1. Custom channel · B37 Instagram was taken out of the plan because the thumbnail changed.'), true);
    await page.screenshot({path: `${shots}/${phase}-5-thumbnail-after.png`, fullPage: true});
  }

  // AK2 b) video: nahrát v1 a v2 (lokální testovací video), v1 Final, schválit tým + klient, pak v2 Final.
  {
    const tab = `${detail(seed.video.post)}?tab=video`;
    await page.goto(tab);
    for (const n of [1, 2]) {
      const [chooser] = await Promise.all([
        page.waitForEvent('filechooser'),
        page.getByRole('button', {name: 'Upload version', exact: true}).first().click(),
      ]);
      await chooser.setFiles(`${media}/kratke-1s.mp4`);
      // The next version starts only after this upload finished (one unfinished upload per channel and person).
      await page.waitForFunction(count => document.querySelectorAll('li[data-version]').length >= count
        && !document.querySelector('li[data-version][data-status="uploading"]'), n, {timeout: 120000, polling: 500})
        .catch(async error => {
          await page.screenshot({path: `${shots}/${phase}-video-upload-failed.png`, fullPage: true});
          console.error('video upload:', owner.errors, await page.locator('[data-video-notice], [data-video-error]').allInnerTexts());
          throw error;
        });
    }
    await page.waitForFunction(() => document.querySelectorAll('li[data-version][data-status="ready"]').length >= 2,
      null, {timeout: 240000, polling: 2000});
    const versions = await page.$$eval('li[data-version]', items => items.map(li => ({
      id: li.dataset.version, number: Number(li.querySelector('p').textContent.trim().match(/^v(\d+)/)[1]),
    })));
    const id = n => versions.find(v => v.number === n).id;
    const markFinal = async n => {
      await page.locator('[data-video-select]').first().selectOption(id(n));
      const toggle = page.locator('[data-video-final-toggle]').first();
      await toggle.waitFor({state: 'visible'});
      check(`video: toggle offers marking v${n}`, (await toggle.innerText()).trim(), 'Mark as final for publishing');
      const [resp] = await Promise.all([
        page.waitForResponse(r => r.url().includes(`/versions/${id(n)}/final/`)),
        toggle.click(),
      ]);
      return resp.json();
    };
    const first = await markFinal(1);
    check('video: first final before approval', first.review_notice || '', '');
    await page.screenshot({path: `${shots}/${phase}-6-video-v1-final.png`, fullPage: true});

    // Schválení týmem (owner) a klientem skutečnými klepnutími.
    await page.goto(`${detail(seed.video.post)}?tab=approval`);
    await Promise.all([page.waitForNavigation(), page.getByRole('button', {name: 'Approve', exact: true}).click()]);
    const client = await session('client');
    await client.page.goto(`${detail(seed.video.post)}?tab=approval`);
    await Promise.all([client.page.waitForNavigation(), client.page.getByRole('button', {name: 'Approve', exact: true}).click()]);
    await client.page.screenshot({path: `${shots}/${phase}-7-client-approved.png`, fullPage: true});
    check('client: no console errors or 5xx', client.errors, []);
    await client.ctx.close();

    await page.goto(tab);
    await page.locator('li[data-version]').first().waitFor();
    const second = await markFinal(2);
    check('video: new final after approval', second.review_notice,
      'Custom channel · B37 Instagram goes back to review for approval because the final video changed.');
    const notice = page.locator('[data-video-final-notice]').first();
    await notice.waitFor({state: 'visible'});
    check('video: notice shown', (await notice.innerText()).trim(), second.review_notice);
    await page.waitForFunction(() => /Final for publishing: v2/.test(document.querySelector('[data-video-final-info]')?.textContent || ''));
    await page.screenshot({path: `${shots}/${phase}-8-video-v2-final.png`, fullPage: true});
    await page.goto(detail(seed.video.post));
    check('video: content back in review after reload', (await body()).includes('B37 Instagram · Pending Review'), true);
  }
  // The FreeFrame player inside the iframe is a separate origin page; only the app page's own errors count.
  check('owner: no console errors or 5xx', owner.errors, []);
  await fs.writeFile(`${out}/browser-${phase}.json`, JSON.stringify(results, null, 2));
  clearTimeout(deadline);
  await browser.close();
  console.log(JSON.stringify(results.map(r => [r.ok === false ? 'FAIL' : 'ok', r.name])));
})().catch(async e => { console.error(e); process.exit(1); });
