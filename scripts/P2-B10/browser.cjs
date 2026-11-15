// P2-B10: Chromium pouze v Dockeru; run-browser.sh omezuje paměť i celý běh na 10 minut.
process.env.LANG = process.env.LC_ALL = 'C.UTF-8';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
assert.equal(process.env.JRC_PROJECT, 'jrc-p2b10');
const out = 'ops/test-output/P2-B10', shots = 'ops/screenshoty/sprint-feedback/P2-B10';
const fixture = JSON.parse(fs.readFileSync(`${out}/fixture.json`));
const base = 'http://127.0.0.1:13910/jrc';
const detail = `${base}/workspace/${fixture.workspace}/content/${fixture.post}/?tab=video`;
const file = `${out}/Žlutý klip – original.MOV`;
const srt = Buffer.from('1\n00:00:00,000 --> 00:00:01,500\nŽlutý klip <script>alert("x")</script>\n\n2\n00:00:01,500 --> 00:00:03,000\nDruhý řádek\n');
const results = [], errors = [];
async function login(browser, role, width=1440) {
  const context = await browser.newContext({viewport:{width,height:width===1440?900:844},acceptDownloads:true});
  await context.route(/^https?:\/\/(?!127\.0\.0\.1:13910(?:\/|$))/, r => r.abort());
  const page = await context.newPage(); page.setDefaultTimeout(20000);
  page.ticketPaths=[];
  page.on('request', r=>{if(r.url().endsWith('/video-sso/')) page.ticketPaths.push(new URLSearchParams(r.postData()).get('next'));});
  page.on('pageerror', e => errors.push(e.message));
  if (fixture[role].cookie) {
    await context.addCookies([{...fixture[role].cookie,url:base+'/'}]);
  } else {
  await page.goto(`${base}/accounts/login/`);
  await page.locator('[name=login]').fill(fixture[role].email);
  await page.locator('[name=password]').fill(fixture[role].password);
  await Promise.all([page.waitForURL(u => !u.pathname.includes('/accounts/login')),page.locator('button[type=submit]').click()]);
  }
  await page.goto(detail);
  return {page,context};
}
function card(page) { return page.locator('[data-video-channel]:visible').first(); }
async function countReady(page, n) {
  await page.waitForFunction(n => document.querySelector('[data-video-channel]:not(.hidden)').querySelectorAll('[data-status=ready]').length===n,n,{timeout:90000});
}
async function player(page) {
  const frame = await (await card(page).locator('iframe').elementHandle()).contentFrame();
  try {
    await frame.waitForFunction(() => {const v=document.querySelector('video');return v && v.videoWidth>0 && v.readyState>=2;},null,{timeout:60000});
  } catch (error) {
    await page.screenshot({path:`${shots}/player-failure-${page.viewportSize().width}.png`,fullPage:true});
    console.error('Player state:', await frame.evaluate(() => {
      const v=document.querySelector('video');
      return {text:document.body.innerText,video:v && {readyState:v.readyState,networkState:v.networkState,width:v.videoWidth,error:v.error && v.error.code}};
    }));
    throw error;
  }
  await frame.waitForFunction(async () => {const v=document.querySelector('video');try {await v.play();return !v.paused;}catch{return false;}},null,{timeout:15000});

}
async function shot(page,name,fullPage=false) {
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),name+': horizontal overflow');
  await page.screenshot({path:`${shots}/${name}.png`,fullPage});
}
async function uploadSrt(page, row, name='Přepis.srt',data=srt) {
  const response = page.waitForResponse(r => r.url().endsWith('/transcript/') && r.request().method()==='POST');
  await row.locator('[data-transcript-file]').setInputFiles({name,mimeType:'text/plain',buffer:data});
  return response;
}
(async()=> {
  const browser = await chromium.launch({headless:true,args:['--disable-dev-shm-usage','--js-flags=--max-old-space-size=2048']});
  try {
    if(process.argv[2]==='upload') {
      if(!fs.existsSync(file)) execFileSync('ffmpeg',['-hide_banner','-loglevel','error','-y','-f','lavfi','-i','testsrc2=size=640x360:rate=25:duration=2','-c:v','libx264','-threads','2','-preset','ultrafast',file],{timeout:60000});
      const {page,context}=await login(browser,'owner');
      await shot(page,'desktop-empty');
      for(let n=1;n<=2;n++) {
        await card(page).locator('[data-video-file]').setInputFiles(file);
        await countReady(page,n);
        await player(page);
        assert.equal(await card(page).locator('details[open]').count(),1);
        if(n===1) await page.reload();
      }
      await context.close(); console.log('OK: two synthetic video versions uploaded and decoded'); return;
    }
    if(process.argv[2]==='extra') {
      const {page,context}=await login(browser,'owner',390);
      await player(page);
      assert.equal(page.ticketPaths.length,1,'only the newest version loads initially');
      assert.equal(await card(page).locator('[data-version]').nth(1).locator('iframe').count(),0);
      await page.locator('[data-video-tab]').nth(1).click();
      const c=card(page), data={filename:file.split('/').pop(),size:fs.statSync(file).size,type:'video/quicktime'};
      const startUrl=await page.locator('[data-jrc-video]').getAttribute('data-start-url');
      const csrf=await page.locator('[data-jrc-video]').getAttribute('data-csrf');
      const channel=await c.getAttribute('data-video-channel');
      async function start() {
        const res=await context.request.post('http://127.0.0.1:13910'+startUrl,{data:{...data,channel},headers:{'X-CSRFToken':csrf}});
        assert.equal(res.status(),200,await res.text());
        await page.evaluate(()=>document.querySelector('[data-jrc-video]').dispatchEvent(new CustomEvent('jrc-video:refresh')));
        await c.locator('[data-status=uploading]').waitFor();
      }
      await start();
      assert.equal(await c.locator('details[open]').count(),1);
      assert.equal(await c.locator('iframe:visible').count(),0,'unfinished newest version must not load a player');
      await c.locator('[data-video-resume]').scrollIntoViewIfNeeded();
      await shot(page,'mobile-continue-upload');
      const [chooser]=await Promise.all([page.waitForEvent('filechooser'),c.locator('[data-video-resume]').click()]);
      await chooser.setFiles(file); await countReady(page,1); await player(page);
      const [download]=await Promise.all([page.waitForEvent('download'),c.locator('[data-video-download]').click()]);
      assert.deepEqual(fs.readFileSync(await download.path()),fs.readFileSync(file));
      await start();
      await c.locator('[data-status=uploading] [data-video-discard-row]').click();
      await c.locator('[data-status=uploading]').waitFor({state:'detached'});
      await countReady(page,1); await player(page);
      await context.close();
      const desktop=await login(browser,'owner',1440);
      await desktop.page.locator('[data-video-tab]').nth(1).click();
      const transfer=await desktop.page.evaluateHandle(({base64,name})=>{
        const bytes=Uint8Array.from(atob(base64),x=>x.charCodeAt(0));
        const dt=new DataTransfer(); dt.items.add(new File([bytes],name,{type:'video/quicktime'}));return dt;
      },{base64:fs.readFileSync(file).toString('base64'),name:data.filename});
      await card(desktop.page).locator('[data-video-drop]').dispatchEvent('drop',{dataTransfer:transfer});
      await countReady(desktop.page,2); await player(desktop.page);
      await shot(desktop.page,'desktop-drop-new-version',true);
      await desktop.context.close();
      console.log('OK extra: resume, original video download, discard, drag-and-drop new version');return;
    }
    if(process.argv[2]==='long') {
      for(const width of [1440,390]) {
        const {page,context}=await login(browser,'owner',width);
        await player(page);
        const row=card(page).locator('[data-version]').first();
        await row.locator('summary').click();
        await shot(page,`long-${width}-versions`,true);
        await row.locator('summary').click();
        await player(page);
        assert.equal((await uploadSrt(page,row,'Dlouhý-přepis-'+ 'x'.repeat(165)+'.srt')).status(),200);
        await row.locator('[data-transcript-preview]').click();
        await row.locator('[data-transcript-text]:visible').waitFor();
        await row.locator('[data-transcript]').scrollIntoViewIfNeeded();
        await shot(page,`long-${width}-srt`);
        await context.close();
      }
      console.log('OK long: filenames, author, focus, desktop/mobile no horizontal overflow');return;
    }
    const roles=process.argv[2]==='roles' ? process.argv.slice(3) : ['owner','manager','editor','client','viewer'];
    assert(roles.length && roles.every(role=>['owner','manager','editor','client','viewer'].includes(role)));
    for(const width of [1440,390]) for(const role of roles) {
      console.log('START',role,width);
      const {page,context}=await login(browser,role,width);
      const c=card(page), rows=c.locator('[data-version]');
      await rows.nth(1).waitFor();
      assert.equal(await c.locator('details[open]').count(),1);
      assert.match(await c.locator('details[open] summary').innerText(),/^v2/);
      await player(page);
      const v2=rows.nth(0),v1=rows.nth(1);
      assert.equal(await v1.locator('iframe').count(),0,'collapsed old version has no player');
      assert.equal(page.ticketPaths.length,1,'only the newest version is loaded initially');
      await v2.locator('summary').click();
      await page.waitForFunction(()=>!document.querySelector('[data-video-channel]:not(.hidden) details[open]'));
      assert.equal(await c.locator('iframe').getAttribute('src'),'about:blank');
      await shot(page,`${role}-${width}-versions`);
      await v1.locator('summary').focus(); await page.keyboard.press('Enter');
      await player(page);
      assert.match(await c.locator('details[open] summary').innerText(),/^v1/);
      let ticketCalls=0; page.on('request', r=>{if(r.url().endsWith('/video-sso/')) ticketCalls++;});
      await page.evaluate(()=>document.querySelector('[data-jrc-video]').dispatchEvent(new CustomEvent('jrc-video:refresh')));
      await page.waitForResponse(r=>r.url().endsWith('/video/status/'));
      await player(page);
      assert.equal(ticketCalls,0,'refresh must preserve player');
      const canWrite=['owner','manager','editor'].includes(role);
      assert.equal(await v1.locator('[data-transcript-file]').count(),canWrite?1:0);
      if(canWrite) {
        assert.equal((await uploadSrt(page,v1)).status(),200);
        await v1.locator('[data-transcript-preview]:visible').waitFor();
        assert.equal((await uploadSrt(page,v1,'invalid.srt',Buffer.from('not SRT'))).status(),400);
        await v1.locator('[data-transcript-error]:visible').waitFor();
        assert.equal((await uploadSrt(page,v1)).status(),200);
      }
      await v1.locator('[data-transcript-preview]').click();
      await v1.locator('[data-transcript-text]:visible').waitFor();
      assert.equal(await v1.locator('[data-transcript-text]').textContent(),srt.toString());
      assert.equal(await v1.locator('[data-transcript-text] script').count(),0);
      await v1.locator('[data-transcript]').scrollIntoViewIfNeeded();
      await shot(page,`${role}-${width}-srt`);
      const dl=await Promise.all([page.waitForEvent('download'),v1.locator('[data-transcript-download]').click()]);
      assert.equal(dl[0].suggestedFilename(),'Přepis.srt');
      const downloadPath=await dl[0].path(); assert.deepEqual(fs.readFileSync(downloadPath),srt);
      if(role==='owner') {
        await c.locator('[data-video-final-toggle]').click();
        await v1.locator('[data-video-final-mark]:visible').waitFor();
        await c.locator('[data-video-download]').waitFor();
        await shot(page,`owner-${width}-final`,true);
        await c.locator('[data-video-final-toggle]').click();
        await v1.locator('[data-video-final-mark]').waitFor({state:'hidden'});
        await v1.locator('[data-transcript-delete]').click();
        await v1.getByText('No transcript yet.',{exact:true}).waitFor();
        assert.equal((await uploadSrt(page,v1)).status(),200);
      }
      await v2.locator('summary').click(); await player(page);
      assert.equal(await v2.getByText('No transcript yet.',{exact:true}).count(),1);
      await page.locator('[data-video-tab]').nth(1).click();
      await page.locator('[data-video-channel]:visible [data-video-empty]').waitFor();
      await shot(page,`${role}-${width}-other-channel`);
      await page.locator('[data-video-tab]').first().click(); await player(page);
      results.push({role,width,pass:true}); await context.close();
    }
    assert.deepEqual(errors,[]);
    fs.writeFileSync(`${out}/browser.json`,JSON.stringify({results,errors},null,2));
    console.log('OK',JSON.stringify(results));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
