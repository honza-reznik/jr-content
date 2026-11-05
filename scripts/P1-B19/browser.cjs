// P1-B19: spouštět přes check-browser.sh (Docker, 3 GB, nejvýš 10 minut).
const { chromium } = require('/opt/jrc-tools/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const { execFileSync } = require('node:child_process');

(async () => {
  const origin = 'http://127.0.0.1:13623';
  const base = origin + '/jrc';
  const output = '/workspace/ops/test-output/P1-B19';
  const shots = '/workspace/ops/screenshoty/P1-B19';
  const seed = JSON.parse(await fs.readFile(`${output}/session.json`, 'utf8'));
  execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i',
    'testsrc2=size=320x180:rate=25:duration=2', '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p', `${output}/garden.mp4`],
    {timeout: 20000, maxBuffer: 65536});
  const browser = await chromium.launch({headless: true, args: ['--js-flags=--max-old-space-size=512']});
  const deadline = setTimeout(() => { browser.close().finally(() => process.exit(124)); }, 480000);
  const errors = [], badResponses = [], external = [];
  async function session(role, width) {
    const context = await browser.newContext({viewport: {width, height: width === 390 ? 844 : 1000},
      isMobile: width === 390, hasTouch: width === 390});
    await context.addCookies([{name: 'sessionid', value: seed.sessions[role], url: origin}]);
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin === origin) return route.continue();
      external.push(route.request().url()); return route.abort();
    });
    const page = await context.newPage();
    page.setDefaultTimeout(20000); page.setDefaultNavigationTimeout(30000);
    page.on('pageerror', e => errors.push(e.message));
    page.on('response', r => { if (r.status() >= 400) badResponses.push(`${r.status()} ${r.url()}`); });
    return {context, page};
  }
  async function snap(page, name, width) {
    if (name.includes('publish-')) {
      await page.locator('[data-channel-thumbnail]').scrollIntoViewIfNeeded();
      await page.locator('[data-thumbnail-image]').evaluate(img => img.decode());
    }
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Přetečení ${name}/${width}`);
    await page.screenshot({path: `${shots}/${name}-${width}.png`});
  }
  async function eventually(check, ms) {
    const end = Date.now() + ms;
    while (!await check()) { assert(Date.now() < end, 'Čekání vypršelo'); await new Promise(r => setTimeout(r, 1000)); }
  }
  try {
    for (const width of [1440, 390]) {
      const post = seed.posts[width];
      const detail = `${base}/workspace/${seed.workspace}/content/${post.id}/`;
      const owner = await session('owner', width);
      const client = await session('client', width);
      for (const [role, tab] of [['owner', owner], ['client', client]]) {
        await tab.page.goto(detail + '?tab=publish');
        assert(await tab.page.getByRole('button', {name: 'Download thumbnail', exact: true}).isDisabled());
        const reason = await tab.page.locator('[data-thumbnail-blocked]').getAttribute('aria-describedby');
        assert.match(await tab.page.locator(`#${reason}`).innerText(), /Requires channel approval/);
        assert.equal(await tab.page.getByRole('link', {name: 'Download thumbnail', exact: true}).count(), 0);
        await snap(tab.page, `${role}-publish-blocked`, width);
      }
      await owner.page.goto(detail + '?tab=video');
      const [chooser] = await Promise.all([owner.page.waitForEvent('filechooser'), owner.page.getByRole('button', {name: 'Upload version', exact: true}).click()]);
      await chooser.setFiles(`${output}/garden.mp4`);
      await owner.page.locator('[data-version][data-status="ready"]').waitFor({timeout: 120000});
      await owner.page.reload();
      await owner.page.locator('[data-video-final-toggle]:visible').click();
      await owner.page.locator('[data-video-final-badge]:visible').waitFor();

      await client.page.goto(detail + '?tab=comments');
      const comment = `Please keep the garden shot ${width}`;
      await client.page.locator('textarea[name="body"]').first().fill(comment);
      await client.page.getByRole('button', {name: 'Add comment', exact: true}).click();
      await client.page.getByText(comment, {exact: true}).waitFor();
      await snap(client.page, 'client-comments', width);
      await owner.page.goto(base + '/notifications/');
      const commentAlert = owner.page.locator(`a[href="${new URL(detail).pathname}?tab=comments"]`, {hasText: 'New client comment'});
      assert.equal(await commentAlert.count(), 1);
      await commentAlert.click();
      await owner.page.getByText(comment, {exact: true}).waitFor();

      await client.page.goto(detail + '?tab=video');
      const frame = client.page.frameLocator('iframe[data-video-frame]:visible');
      const video = frame.locator('video').first();
      await video.waitFor({timeout: 60000});
      await eventually(async () => await video.evaluate(v => v.duration > 1), 30000);
      await video.evaluate(v => {v.currentTime = 1;});
      const videoComment = `Shorten the garden video ${width}`;
      // Mobile player has a collapsed Comments panel.
      const input = frame.getByPlaceholder('Leave your comment...');
      if (!await input.isVisible()) await frame.getByRole('button', {name: /Comments/}).first().click();
      await input.fill(videoComment);
      await frame.getByTitle('Send (Enter)').click();
      await frame.getByText(videoComment, {exact: true}).first().waitFor();
      await frame.getByText(videoComment, {exact: true}).first().scrollIntoViewIfNeeded();
      await snap(client.page, 'client-video-comment', width);
      // No manual worker invocation: the regular 30-second sweep delivers the event.
      const videoAlert = owner.page.locator(`a[href="${new URL(detail).pathname}?tab=video"]`, {hasText: 'New client comment'});
      await eventually(async () => {
        await owner.page.goto(base + '/notifications/');
        return await videoAlert.count() === 1;
      }, 90000);
      await snap(owner.page, 'owner-notifications', width);
      await videoAlert.click();
      await owner.page.frameLocator('iframe[data-video-frame]:visible').getByText(videoComment, {exact: true}).first().waitFor({timeout: 60000});

      await client.page.goto(detail + '?tab=approval');
      const feedback = `Please shorten the beginning ${width}`;
      await client.page.locator('#approval-comment').fill(feedback);
      await client.page.getByRole('button', {name: 'Request changes', exact: true}).click();
      await client.page.locator('[data-approval-latest]').getByText(feedback, {exact: true}).waitFor();
      for (const [role, tab] of [['owner', owner], ['client', client]]) {
        await tab.page.goto(detail + '?tab=comments');
        assert.equal(await tab.page.getByText(feedback, {exact: true}).count(), 1);
        await tab.page.getByText(feedback, {exact: true}).scrollIntoViewIfNeeded();
        await snap(tab.page, `${role}-shared-request`, width);
        await tab.page.goto(detail + '?tab=history');
        await tab.page.getByText(feedback, {exact: true}).waitFor();
      }
      await owner.page.goto(base + '/notifications/');
      const decision = owner.page.locator('p').filter({hasText: feedback});
      assert.equal(await decision.count(), 1);
      assert.equal(await owner.page.locator(`a[href="${new URL(detail).pathname}?tab=comments"]`, {hasText: 'New client comment'}).count(), 1);
      // Resubmit through the ordinary server endpoint, then approve as client.
      await owner.page.goto(detail + '?tab=approval');
      const status = await owner.page.evaluate(async ({url}) => {
        const csrf = document.querySelector('input[name="csrfmiddlewaretoken"]').value;
        return (await fetch(url, {method: 'POST', headers: {'X-CSRFToken': csrf},
          body: new URLSearchParams({stage: 'review', expected_stage: 'review'})})).status;
      }, {url: `${base}/workspace/${seed.workspace}/board/${post.id}/move/`});
      assert.equal(status, 200);
      await client.page.goto(detail + '?tab=approval');
      await client.page.getByRole('button', {name: 'Approve', exact: true}).click();
      for (const [role, tab] of [['owner', owner], ['client', client]]) {
        await tab.page.goto(detail + '?tab=publish');
        const [download] = await Promise.all([tab.page.waitForEvent('download'), tab.page.getByRole('link', {name: 'Download thumbnail', exact: true}).click()]);
        assert.equal(await download.failure(), null);
        assert.deepEqual(await fs.readFile(await download.path()), Buffer.from(seed.image, 'base64'));
        await snap(tab.page, `${role}-publish-download`, width);
      }
      await owner.context.close(); await client.context.close();
      console.log(`OK ${width}: owner/client, disabled/enabled thumbnail + skutečné bajty, Comments + automatický worker videa, sdílený Request changes bez duplicit.`);
    }
    assert.deepEqual(errors, []); assert.deepEqual(badResponses, []); assert.deepEqual(external, []);
    console.log('OK: bez chyb konzole, HTTP >=400, externích požadavků a vodorovného přetečení.');
  } finally { clearTimeout(deadline); await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
