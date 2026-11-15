// Jen Docker jrc-p2b20; run-browser.sh omezuje paměť a dobu celého průchodu.
process.env.LANG = process.env.LC_ALL = 'C.UTF-8';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
assert.equal(process.env.JRC_PROJECT, 'jrc-p2b20');
const origin = 'http://127.0.0.1:13920', base = origin + '/jrc';
const out = 'ops/test-output/P2-B20', shots = 'ops/screenshoty/sprint-feedback/P2-B20';
const seed = JSON.parse(fs.readFileSync(out + '/fixture.json'));
const detail = `${base}/workspace/${seed.workspace}/content/${seed.post}/?tab=video`;
const errors = [];
(async () => {
 const browser = await chromium.launch({headless:true});
 try {
  async function session(role, width) {
   const context = await browser.newContext({viewport:{width,height:width===390?844:900},isMobile:width===390,hasTouch:width===390});
   await context.addCookies([{name:'sessionid',value:seed.sessions[role],url:origin}]);
   await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
   const page = await context.newPage(); page.setDefaultTimeout(30000); page.setDefaultNavigationTimeout(90000);
   page.on('pageerror',e=>errors.push(`${role}/${width}: ${e.message}`));
   return {page,context};
  }
  {
   const context = await browser.newContext();
   const person = JSON.parse(fs.readFileSync(out+'/nonmember.json'));
   await context.addCookies([{name:'sessionid',value:person.session,url:origin}]);
   const page=await context.newPage();
   for(const path of [`/workspace/${seed.workspace}/calendar/`,`/workspaces/${seed.workspace}/settings/`,`/workspaces/${seed.workspace}/settings/approvals/`]) {
    assert.equal((await page.goto(base+path)).status(),404);
    assert.ok(!(await page.locator('body').innerText()).includes('Ella garden'));
   }
   await context.close();
  }
  const shot = async(page,name) => {
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),name+' overflow');
   await page.screenshot({path:`${shots}/${name}.png`});
  };
  for(const width of [1440,390]) for(const role of ['viewer','client','owner']) {
   const {page,context} = await session(role,width);
   await page.goto(`${base}/board/?client=${seed.workspace}`,{waitUntil:'domcontentloaded'});
   if(role==='viewer') {
    assert.equal(await page.locator('.board-select,.board-bulk').count(),0);
    await page.locator('#quick-search').fill('Drag');
    assert.equal(await page.locator('.board-card:visible').count(),1);
    await page.locator('#quick-search').fill('');
    await page.locator('.board-card').first().locator('[data-card-menu]').click();
    assert.equal(await page.getByRole('menuitem').count(),2);
    await page.keyboard.press('Escape');
   } else if(role==='client') {
    assert.equal(await page.locator('[data-bulk-move],[data-bulk-trash]').count(),0);
    assert.equal(await page.locator('.board-select').count(),1);
    await page.locator('#select-visible').click();
    assert.equal(await page.locator('#selection-count').innerText(),'1 selected');
    assert.equal(await page.locator('[data-bulk-approve]').isEnabled(),true);
   } else {
    assert.equal(await page.locator('[data-bulk-move],[data-bulk-trash]').count(),2);
    const source=page.locator('.board-card').filter({hasText:'Drag source'});
    const sourceId=await source.getAttribute('data-post');
    // Native drag events explicitly bind the source, then target a different card after horizontal scrolling.
    const transfer=await page.evaluateHandle(()=>new DataTransfer());
    await source.locator('h3').dispatchEvent('dragstart',{dataTransfer:transfer});
    await page.locator('.board-grid').evaluate(el=>{el.scrollLeft=el.scrollWidth;});
    const target=page.locator('.board-column[data-stage=approved] .board-card').filter({hasText:'Drop target decoy'}).locator('h3');
    await target.dispatchEvent('drop',{dataTransfer:transfer});
    assert.equal(await page.locator('#board-action-dialog').isVisible(),true);
    let sent;
    await page.route('**/board/actions/',async route=>{
     sent=route.request().postDataJSON();
     await route.fulfill({json:{results:[{id:sourceId,ok:false,message:'Kontrola identity bez změny dat'}]}});
    });
    await page.locator('#board-action-form button[type=submit]').click();
    await page.waitForFunction(()=>document.querySelector('#board-results').hidden===false);
    assert.equal(sent.items.length,1);assert.equal(sent.items[0].id,sourceId);
    await source.dispatchEvent('dragend');
    console.log(`${width}: dragstart source retained after board scroll and drop`);
   }
   await shot(page,`${width}-${role}-board`);
   await page.locator('.board-card').first().scrollIntoViewIfNeeded();
   await shot(page,`${width}-${role}-cards`);
   await context.close();
  }
  {
   const {page,context}=await session('owner',1440);
   await page.goto(detail,{waitUntil:'domcontentloaded'});
   const card=page.locator('[data-video-channel]:visible');
   if(!await card.locator('[data-status=ready]').count()) {
    const file=out+'/test.mp4';
    execFileSync('ffmpeg',['-hide_banner','-loglevel','error','-y','-f','lavfi','-i','testsrc2=size=640x360:rate=25:duration=2','-c:v','libx264','-threads','2','-preset','ultrafast','-pix_fmt','yuv420p',file],{timeout:30000});
    await card.locator('[data-video-file]').setInputFiles(file);
    await card.locator('[data-status=ready]').waitFor({timeout:180000});
   }
   await card.locator('[data-transcript-file]').setInputFiles({name:'Přepis.srt',mimeType:'application/x-subrip',buffer:Buffer.from('1\n00:00:00,000 --> 00:00:01,500\nŽlutý klip\n')});
   await card.locator('[data-transcript-name]').filter({hasText:'Přepis.srt'}).waitFor();
   await context.close();
  }
  for(const width of [1440,390]) {
   const {page,context}=await session('owner',width);
   await page.goto(detail,{waitUntil:'domcontentloaded'});
   await page.locator('[data-video-tab]').filter({hasText:'YouTube · Video'}).click();
   await page.locator('[data-video-channel]:visible [data-video-reuse]').first().click();
   const dialog=page.locator('[data-video-reuse-dialog]');
   await dialog.locator('.reuse-option').filter({hasText:'YouTube · Shorts'}).click();
   await dialog.locator('input[name=keep_comments][value=no]').check();
   await shot(page,`${width}-reuse-dialog`);
   const copiedResponse=page.waitForResponse(r=>r.url().endsWith('/duplicate/')&&r.request().method()==='POST');
   await dialog.locator('[data-reuse-submit]').click();
   const copied=await (await copiedResponse).json();
   assert.ok(copied.version,JSON.stringify(copied));
   await page.locator(`[data-video-channel="${copied.target}"]`).waitFor({state:'visible'});
   const row=page.locator(`[data-version="${copied.version}"]`);
   await page.waitForFunction(id=>document.querySelector(`[data-version="${id}"] details`)?.open,copied.version);
   await row.scrollIntoViewIfNeeded(); await page.waitForTimeout(300);
   assert.equal(await row.locator('details').getAttribute('open'),'');
   await shot(page,`${width}-copied-expanded`);
   console.log(`${width}: copied version ${copied.number} expanded without reload`);
   await context.close();
  }
  for(const width of [1440,390]) for(const role of ['client','viewer']) {
   const {page,context}=await session(role,width);
   await page.goto(detail,{waitUntil:'domcontentloaded'});
   const card=page.locator('[data-video-channel]:visible');
   assert.equal(await card.locator('[data-transcript-download]:visible,[data-transcript-preview]:visible').count(),0);
   assert.equal(await card.locator('[data-transcript-download][href]').count(),0);
   const version=await card.locator('[data-version]').first().getAttribute('data-version');
   const transcript=`${base}/workspace/${seed.workspace}/content/${seed.post}/video/versions/${version}/transcript/`;
   assert.equal((await page.request.get(transcript)).status(),403);
   assert.equal((await page.request.get(transcript+'?preview=1')).status(),403);
   if(role==='viewer') {
    const frame=await (await card.locator('iframe').elementHandle()).contentFrame();
    try {await frame.getByText('No comments yet',{exact:true}).waitFor({timeout:90000});}
    catch(error) { await page.screenshot({path:`${shots}/${width}-viewer-failure.png`}); console.error('FRAME',frame.url(),await frame.locator('body').innerText()); throw error; }
    assert.equal(await frame.getByText('Leave a comment below to start the review',{exact:true}).count(),0);
   }
   await card.scrollIntoViewIfNeeded(); await shot(page,`${width}-${role}-video`);
   await context.close();
  }
  assert.deepEqual(errors,[]);
  console.log('PASS: desktop+mobile board roles, drag identity, live upload/reuse, transcript links, viewer prompt; no page errors.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
