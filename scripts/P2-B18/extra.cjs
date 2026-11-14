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
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']});
 const data=JSON.parse(fs.readFileSync(`${out}/versions.json`));
 const v=data.versions.find(v=>v.number===2);
 const url=`${root}/workspace/${fixture.workspace}/content/${fixture.post}/video/versions/${v.bb}/download/`;
 const mode=process.argv[2];
 try{
  for(const viewport of [{width:1440,height:900},{width:390,height:844}]){
   const role=mode==='visual'?'owner':'client';
   const {page,context}=await login(browser,role,viewport);
   const card=page.locator('[data-video-channel]:visible').first();
   const frame=await (await card.locator('iframe').elementHandle()).contentFrame();
   await frame.locator('video').first().waitFor({timeout:90000});
   if(mode==='pending'){
    assert.equal((await context.request.get(url)).status(),403);
    assert.equal(await card.locator('[data-video-download]').isVisible(),false);
    await card.locator('[data-video-toolbar]').scrollIntoViewIfNeeded();
    await shot(page,`client-pending-${viewport.width}`);
   }else if(mode==='fallback'){
    await page.goto(url);
    await page.getByRole('heading',{name:'Original video unavailable'}).waitFor();
    const link=page.getByRole('link',{name:'Download converted MP4'});
    assert.equal(new URL(await link.getAttribute('href'),root).pathname,new URL(url).pathname);
    await link.focus();
    await shot(page,`fallback-${viewport.width}`);
    const event=page.waitForEvent('download'); await link.click(); const download=await event;
    assert(download.suggestedFilename().endsWith('.mp4'));
    const expected=JSON.parse(fs.readFileSync(`${out}/fallback.json`));
    assert.equal(sha(fs.readFileSync(await download.path())),expected.sha256);
   }else if(mode==='final'){
    const finalUrl=`${root}/workspace/${fixture.workspace}/content/${fixture.post}/video/channels/${data.variant}/final/download/`;
    const response=await context.request.get(finalUrl);
    assert.equal(response.status(),200);
    assert.equal(sha(await response.body()),sha(fs.readFileSync(file)));
    const ranged=await context.request.get(finalUrl,{headers:{Range:'bytes=-8'}});
    assert.equal(ranged.status(),206);assert.equal((await ranged.body()).length,8);
   }else{
    await page.evaluate(()=>window.scrollTo(0,0));
    await shot(page,`long-title-${viewport.width}`);
    const link=card.locator('[data-video-download]');
    await link.scrollIntoViewIfNeeded();await link.focus();
    await shot(page,`long-controls-focus-${viewport.width}`);
    await page.locator('[data-video-tab]').nth(1).click();
    await page.getByText('No video yet.',{exact:true}).waitFor();
    await page.locator('[data-video-channel]:visible').first().scrollIntoViewIfNeeded();
    await shot(page,`empty-channel-${viewport.width}`);
   }
   console.log('OK',mode,viewport.width);await context.close();
  }
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
