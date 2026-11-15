// Skutečné přetažení myší a záznam identity událostí; jen Docker a jrc-p2b20.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
assert.equal(process.env.JRC_PROJECT,'jrc-p2b20');
(async()=>{
 const origin='http://127.0.0.1:13920';
 const seed=JSON.parse(fs.readFileSync('ops/test-output/P2-B20/fixture.json'));
 const browser=await chromium.launch({headless:true});
 try {
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  await context.addCookies([{name:'sessionid',value:seed.sessions.owner,url:origin}]);
  const page=await context.newPage();page.setDefaultTimeout(20000);
  await page.goto(`${origin}/jrc/board/?client=${seed.workspace}`,{waitUntil:'domcontentloaded'});
  const source=page.locator('.board-card').filter({hasText:'Drag source'});
  const target=page.locator('.board-card').filter({hasText:'Drop target decoy'});
  const intended=await source.getAttribute('data-post');
  await page.evaluate(()=>{
   window.dragEvents=[];
   for(const type of ['dragstart','drop']) document.addEventListener(type,e=>window.dragEvents.push({type,card:e.target.closest('.board-card')?.dataset.post,column:e.target.closest('.board-column')?.dataset.stage}),true);
  });
  let sent;
  await page.route('**/board/actions/',async route=>{sent=route.request().postDataJSON();await route.fulfill({json:{results:[{id:sent.items[0].id,ok:false,message:'Kontrola identity bez změny dat'}]}});});
  await source.locator('h3').dragTo(target.locator('h3'));
  const events=await page.evaluate(()=>window.dragEvents);
  const started=events.find(e=>e.type==='dragstart');
  assert.ok(started,JSON.stringify(events));
  assert.equal(await page.locator('#board-action-dialog').isVisible(),true);
  await page.locator('#board-action-form button[type=submit]').click();
  await page.waitForFunction(()=>!document.querySelector('#board-results').hidden);
  assert.equal(sent.items[0].id,started.card);
  console.log(JSON.stringify({intended,events,sent:sent.items,sourceMatchesIntention:started.card===intended}));
  await page.screenshot({path:'ops/screenshoty/sprint-feedback/P2-B20/1440-native-drag.png'});
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
