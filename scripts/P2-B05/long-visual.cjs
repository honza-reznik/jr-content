process.env.LANG='C.UTF-8';process.env.LC_ALL='C.UTF-8';
const fs=require('node:fs'),assert=require('node:assert/strict');const {chromium}=require('playwright');
assert.equal(process.env.JRC_PROJECT,'jrc-p2b05');
const data=JSON.parse(fs.readFileSync('ops/test-output/P2-B05/fixture.json'));
const final=JSON.parse(fs.readFileSync('ops/test-output/P2-B05/final.json'));
const base='http://127.0.0.1:13905/jrc';
(async()=>{const browser=await chromium.launch({headless:true});try{
 for(const viewport of [{width:1440,height:900},{width:390,height:844}]){
  const context=await browser.newContext({viewport});const page=await context.newPage();
  await page.goto(`${base}/accounts/login/`);await page.locator('[name=login]').fill(data.editor.email);await page.locator('[name=password]').fill(data.editor.password);
  await Promise.all([page.waitForURL(u=>!u.pathname.includes('/accounts/login')),page.locator('button[type=submit]').click()]);
  await page.goto(`${base}/workspace/${data.workspace}/content/${data.post}/?tab=video`);
  await page.locator(`[data-video-tab="${final.variant}"]`).click();
  const row=page.locator('[data-video-channel]:visible [data-video-versions] [data-version]').first();await row.scrollIntoViewIfNeeded();
  assert((await row.textContent()).includes('VeryLongClip'));
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert(await row.evaluate(el=>el.scrollWidth<=el.clientWidth+1));
  await page.screenshot({path:`ops/screenshoty/sprint-feedback/P2-B05/long-${viewport.width}.png`});
  await context.close();
 }
 console.log('OK long title, channel and filename: desktop/mobile, no row overflow');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
