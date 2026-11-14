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
(async()=>{const browser=await chromium.launch({headless:true});try{
 const {page,context}=await login(browser,'client',{width:1440,height:900});
 const frame=await (await page.locator('iframe').first().elementHandle()).contentFrame();
 await frame.locator('video').first().waitFor({timeout:90000});
 const token=await frame.evaluate(()=>localStorage.getItem('ff_access_token'));
 const data=JSON.parse(fs.readFileSync(`${out}/versions.json`));
 const share=JSON.parse(fs.readFileSync(`${out}/share.json`));
 const headers={Authorization:'Bearer '+token};
 if(process.argv[2]==='allowed'){
  const r=await context.request.get(`${root}/video-api/share/${share.token}/stream/${data.asset}?download=true&rendition=original`,{headers});
  assert.equal(r.status(),200);
  const url=(await r.json()).url;
  assert(url.includes('/_contexts/'));
  const fileResponse=await context.request.get(url);
  assert.equal(fileResponse.status(),200);
  assert.equal(sha(await fileResponse.body()),sha(fs.readFileSync(file)));
  fs.writeFileSync(`${out}/share-url.json`,JSON.stringify({url}),{mode:0o600});
  console.log('OK managed client share: original SHA-256 through bound file context');
 }else{
  const {url}=JSON.parse(fs.readFileSync(`${out}/share-url.json`));
  assert.equal((await context.request.get(url)).status(),403);
  console.log('OK share revoked: already issued context denied');
 }
 await context.close();
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1;});
