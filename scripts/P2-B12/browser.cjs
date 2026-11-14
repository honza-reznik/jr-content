// Docker only: timeout 580s node --max-old-space-size=2048 scripts/P2-B12/browser.cjs
const { chromium } = require('/opt/jrc-tools/node_modules/playwright');
const fs = require('node:fs');
const http = require('node:http');
const assert = require('node:assert/strict');
const data = JSON.parse(fs.readFileSync('brightbean/output/p2b12-browser.env.local', 'utf8'));
const base = 'http://127.0.0.1:13912';
const out = 'ops/screenshoty/sprint-feedback/P2-B12';
fs.mkdirSync(out, {recursive:true});
(async () => {
  const proxy = http.createServer((req, res) => {
    const upstream = http.request({hostname:'jrc-proxy',port:8080,path:req.url,method:req.method,headers:req.headers}, response => {
      res.writeHead(response.statusCode, response.headers); response.pipe(res);
    });
    upstream.setTimeout(15000, () => upstream.destroy());
    upstream.on('error', () => res.destroy()); req.pipe(upstream);
  });
  await new Promise(resolve => proxy.listen(13912, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless:true, args:['--no-sandbox', '--disable-dev-shm-usage']});
  try {
    for (const [device, viewport, colour, rgb, foreground] of [
      ['desktop', {width:1440,height:900}, '#FFFF00', 'rgb(255, 255, 0)', 'rgb(0, 0, 0)'],
      ['mobile', {width:390,height:844}, '#6D28D9', 'rgb(109, 40, 217)', 'rgb(255, 255, 255)'],
    ]) {
      const context = await browser.newContext({viewport, isMobile:device === 'mobile', hasTouch:device === 'mobile'});
      const page = await context.newPage();
      page.setDefaultTimeout(12000);
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      const shot = async name => page.screenshot({path:`${out}/${device}-${name}.png`, fullPage:true});
      const fit = async () => assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${device}: overflow`);
      const goto = async path => {const response = await page.goto(base + path);assert.equal(response.status(),200,path);await fit();};
      const colorOf = locator => locator.evaluate(el => ({background:getComputedStyle(el).backgroundColor, color:getComputedStyle(el).color}));
      const expectColour = async locator => assert.deepEqual(await colorOf(locator), {background:rgb,color:foreground});
      await goto('/jrc/accounts/login/');
      await page.locator('[name=login]').fill(data.email);
      await page.locator('[name=password]').fill(data.password);
      await Promise.all([page.waitForURL(url => !url.pathname.includes('/accounts/login')), page.getByRole('button', {name:'Log in',exact:true}).click()]);
      await goto(data.profile);
      await shot('settings-top');
      const ig = data.channels.Instagram;
      const igCard = page.locator(`article[aria-labelledby="title-${ig.id}"]`);
      const colourDetails = igCard.locator('details').filter({has:page.locator('[data-colour-control]')});
      await colourDetails.locator('summary').click();
      const hex = colourDetails.locator('input[name=color]');
      await hex.fill(colour);
      const preview = colourDetails.locator('[data-colour-preview]');
      await expectColour(preview);
      await hex.focus();
      await shot('colour-preview-focus');
      await Promise.all([page.waitForNavigation({waitUntil:'domcontentloaded'}), colourDetails.getByRole('button',{name:'Save colour',exact:true}).click()]);
      await page.reload();
      const savedCard = page.locator(`article[aria-labelledby="title-${ig.id}"]`);
      assert.equal(await savedCard.evaluate(el => getComputedStyle(el).borderLeftColor), rgb);
      // Invalid server input keeps the form and the previous colour.
      await savedCard.getByText('Channel colour', {exact:false}).first().click();
      await savedCard.locator('input[name=color]').fill('#GGGGGG');
      await savedCard.getByRole('button',{name:'Save colour',exact:true}).click();
      await savedCard.locator('.errorlist').filter({hasText:'Color must be'}).waitFor();
      await shot('invalid-colour');
      await goto(data.profile);
      const custom = data.channels.Forendors;
      const customCard = page.locator(`article[aria-labelledby="title-${custom.id}"]`);
      const icon = customCard.locator('img[data-channel-icon]').first();
      assert(await icon.evaluate(el => el.complete && el.naturalWidth > 0), 'Stored favicon loads');
      const faviconSource = await icon.getAttribute('src');
      await customCard.scrollIntoViewIfNeeded();
      await shot('website-icon');
      // Upload uses a generated fixture already stored by the isolated seed.
      const localIcon = fs.readFileSync('brightbean/output/p2b12-upload.png');
      await customCard.locator('input[type=file]').setInputFiles({name:'own.png',mimeType:'image/png',buffer:localIcon});
      await customCard.getByRole('button',{name:'Upload icon',exact:true}).click();
      await page.waitForLoadState('domcontentloaded');
      assert.notEqual(await customCard.locator('img[data-channel-icon]').first().getAttribute('src'), faviconSource);
      await customCard.getByRole('button',{name:'Use website icon',exact:true}).click();
      await page.waitForLoadState('domcontentloaded');
      assert.equal(await customCard.locator('img[data-channel-icon]').first().getAttribute('src'), faviconSource);
      // Invalid public destination exercises the real relay, preserving the existing favicon.
      await customCard.getByRole('button',{name:'Refresh website icon',exact:true}).click();
      await page.getByText('Website icon unavailable.', {exact:false}).first().waitFor();
      assert.equal(await customCard.locator('img[data-channel-icon]').first().getAttribute('src'), faviconSource);
      await customCard.scrollIntoViewIfNeeded();
      await shot('refresh-unavailable');
      await goto('/jrc/board/');
      const badge = page.locator('.board-channel').filter({hasText:'Instagram'}).first();
      await expectColour(badge);
      await badge.locator("xpath=ancestor::article").hover();
      await shot('board-hover');
      if(device==='mobile') {
        await page.getByRole('button',{name:'Open navigation',exact:true}).click();
        await page.waitForFunction(() => Math.abs(document.querySelector('#jrc-navigation').getBoundingClientRect().x) < 1);
        await shot('navigation');
        await page.getByRole('button',{name:'Open navigation',exact:true}).click({force:true});
      } else {
        const menu = page.getByRole('link',{name:'View Instagram content',exact:true});
        await menu.focus();
        await shot('navigation-focus');
      }
      await goto(ig.detail);
      await expectColour(page.locator('.channel-tag').first());
      await shot('detail');
      const calendar = `/jrc/workspace/${data.workspace}/calendar/`;
      for(const view of ['month','week','day','list']) {
        await goto(calendar + `?mode=calendar&view=${view}`);
        const chip = view==='list' ? page.locator('tbody span').filter({hasText:'Instagram'}).last() : page.locator('a.post-chip:visible').filter({hasText:'Instagram'}).first();
        await expectColour(chip);
        await chip.scrollIntoViewIfNeeded();
        await shot(`publish-${view}`);
      }
      for(const view of ['month','week','day']) {
        await goto(`/jrc/organizations/calendar/?view=${view}`);
        const chip = page.locator('a.cross-post-chip').filter({has:page.locator('.pi-instagram')}).first();
        await expectColour(chip);
        await chip.scrollIntoViewIfNeeded();
        await shot(`overview-${view}`);
      }
      await goto(data.empty_profile);
      await page.getByRole('heading',{name:'No channels yet',exact:true}).waitFor();
      await shot('empty-settings');
      assert.deepEqual(errors, [], `${device}: page errors`);
      console.log(`${device}: colour save/preview/validation, menu, board, detail, month/week/day/list, organization overview, favicon/load/upload/remove/failure OK`);
      await context.close();
    }
  } finally {await browser.close();proxy.close();}
})().catch(error => {console.error(error);process.exitCode=1;});
