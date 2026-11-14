// Pouze Docker: timeout 600s node --max-old-space-size=2048 scripts/P2-B18/live.cjs <upload|check>
process.env.LANG = process.env.LC_ALL = 'C.UTF-8';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
assert.equal(process.env.JRC_PROJECT, 'jrc-p2b18');
const out = 'ops/test-output/P2-B18';
const shots = 'ops/screenshoty/sprint-feedback/P2-B18';
const fixture = JSON.parse(fs.readFileSync(`${out}/fixture.json`));
const root = 'http://127.0.0.1:13918/jrc';
const detail = `${root}/workspace/${fixture.workspace}/content/${fixture.post}/?tab=video`;
const sha = b => crypto.createHash('sha256').update(b).digest('hex');
const file = `${out}/Žlutý klip – original.MOV`;
async function login(browser, role, viewport) {
 const context = await browser.newContext({ viewport, acceptDownloads:true });
 await context.route(/^https?:\/\/(?!127\.0\.0\.1:13918(?:\/|$))/, r => r.abort());
 const page = await context.newPage(); page.setDefaultTimeout(30000);
 await page.goto(`${root}/accounts/login/`);
 await page.locator('[name=login]').fill(fixture[role].email);
 await page.locator('[name=password]').fill(fixture[role].password);
 await Promise.all([page.waitForURL(u => !u.pathname.includes('/accounts/login')), page.locator('button[type=submit]').click()]);
 await page.goto(detail);
 return { page, context };
}
async function shot(page, name) {
 assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name}: overflow`);
 await page.screenshot({path:`${shots}/${name}.png`});
}
(async () => {
 const browser = await chromium.launch({headless:true,args:['--disable-dev-shm-usage']});
 try {
  if (process.argv[2] === 'upload') {
   if (!fs.existsSync(file)) execFileSync('ffmpeg',['-hide_banner','-loglevel','error','-y','-f','lavfi','-i','testsrc2=size=640x360:rate=25:duration=2','-c:v','libx264','-threads','2','-preset','ultrafast',file],{timeout:60000});
   const {page,context} = await login(browser,'owner',{width:1440,height:900});
   await shot(page,'desktop-empty');
   for(let i=1;i<=2;i++) {
    await page.locator('[data-video-channel]:visible input[type=file]').first().setInputFiles(file);
    await page.locator(`[data-video-channel]:visible [data-version-number="${i}"]`).count();
    await page.getByText(/Upload finished/).first().waitFor({timeout:90000});
    await page.waitForFunction(n => [...document.querySelectorAll('[data-video-channel]')].some(c => c.querySelectorAll('[data-status=ready]').length >= n),i,{timeout:120000});
    if(i===1) await page.reload();
   }
   await context.close(); console.log('OK upload: two local synthetic versions');
   return;
  }
  const data = JSON.parse(fs.readFileSync(`${out}/versions.json`));
  for(const viewport of [{width:1440,height:900},{width:390,height:844}]) for(const role of ['owner','manager','editor','client','viewer'].filter(r=>!process.env.P2B18_ROLE||r===process.env.P2B18_ROLE)) {
   console.log('START',role,viewport.width);
   const {page,context} = await login(browser,role,viewport);
   const card = page.locator('[data-video-channel]:visible').first();
   const frameEl = await card.locator('iframe').elementHandle();
   const frame = await frameEl.contentFrame();
   await frame.locator('video').first().waitFor({timeout:90000});
   await frame.waitForFunction(() => document.querySelector('video')?.videoWidth > 0,null,{timeout:90000});
   await card.scrollIntoViewIfNeeded();
   await shot(page,`${role}-${viewport.width}-bb`);
   // Both embedded and standalone player paths are exercised with real session cookies.
   const player = new URL(frame.url()); player.searchParams.delete('embed');
   await page.goto(player.href,{waitUntil:'domcontentloaded'});
   await page.locator('video').first().waitFor({timeout:90000});
   assert.equal(await page.getByRole('button',{name:/Download version/i}).count(),0);
   await shot(page,`${role}-${viewport.width}-ff`);
   const apiHeaders = {Authorization:'Bearer '+await page.evaluate(() => localStorage.getItem('ff_access_token'))};
   for(const embed of ['', '&embed=1']) for(const params of ['', '&rendition=original','&rendition=transcode','&prefer_original=true']) {
    const res = await context.request.get(`${root}/video-api/assets/${data.asset}/stream?version_id=${data.versions[0].ff}&download=true${params}${embed}`, {headers:apiHeaders});
    assert.equal(res.status(),403,`${role} direct ${params}${embed}`);
   }
   const options = await context.request.get(`${root}/video-api/assets/${data.asset}/downloads`, {headers:apiHeaders});
   assert.equal(options.status(),403);
   for(const path of data.keys) for(const verb of ['get','head']) {
    const response = await context.request[verb](`${root}/files/video/${path}?filename=clip.mp4`);
    assert.equal(response.status(),403,`${role} direct bytes ${path}`);
   }
   for(const version of data.versions) {
    const url = `${root}/workspace/${fixture.workspace}/content/${fixture.post}/video/versions/${version.bb}/download/`;
    const allowed = ['owner','manager'].includes(role) || (role==='client' && version.number===2);
    const response = await context.request.get(url);
    assert.equal(response.status(),allowed?200:403,`${role} BB version ${version.number}`);
    if(allowed) {
     assert.equal(sha(await response.body()),sha(fs.readFileSync(file)));
     assert(/attachment/.test(response.headers()['content-disposition']));
     if(version.number===2) {
      const event = page.waitForEvent('download');
      const navigation = page.goto(url).catch(e => {if(!/Download is starting|ERR_ABORTED/.test(e.message)) throw e;});
      const download = await event; await navigation;
      assert.equal(download.suggestedFilename(),'Žlutý klip – original.MOV');
      assert.equal(sha(fs.readFileSync(await download.path())),sha(fs.readFileSync(file)));
      const ranged = await context.request.get(url,{headers:{Range:'bytes=2-5'}});
      assert.equal(ranged.status(),206);assert.equal((await ranged.body()).length,4);
     }
    }
   }
   console.log('PROJECT',role,viewport.width);
   await page.goto(`${root}/video/projects/${fixture.project}`,{waitUntil:'domcontentloaded',timeout:90000});
   await page.locator('[draggable="true"]').first().waitFor({timeout:90000});
   assert.equal(await page.getByRole('button',{name:/Download/i}).count(),0);
   // Context menu, selected side panel, and bulk selection.
   const asset = page.locator('[draggable="true"]').first();
   assert.equal(await asset.count(),1);
   if(await asset.count()) {
    await asset.click({button:'right'});
    assert.equal(await page.getByRole('menuitem',{name:/Download/i}).count(),0);
    await page.keyboard.press('Escape');
    await asset.click();
    assert.equal(await page.getByRole('button',{name:/Download/i}).count(),0);
    await asset.locator('button').first().click({force:true});
    assert.equal(await page.getByRole('button',{name:/Download/i}).count(),0);
   }
   await shot(page,`${role}-${viewport.width}-project`);
   await context.close(); console.log(`OK ${role} ${viewport.width}: embed/player/project/API/direct bytes + BB policy`);
  }
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
