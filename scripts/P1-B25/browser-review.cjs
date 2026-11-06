// Docker runner supplies the cgroup, Node memory limit and hard deadline.
const {chromium} = require('/opt/jrc-tools/node_modules/playwright');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
(async () => {
  const seed = JSON.parse(await fs.readFile('/workspace/ops/test-output/P1-B25/session.json', 'utf8'));
  const origin = 'http://127.0.0.1:13673', base = origin+'/jrc';
  const browser = await chromium.launch({args:['--js-flags=--max-old-space-size=512']});
  const deadline = setTimeout(() => process.exit(124), 240000);
  const results = [];
  try {
    for (const role of ['contributor','owner','client','viewer']) {
      const ctx = await browser.newContext({viewport: role==='viewer' ? {width:390,height:844} : {width:1365,height:900}});
      await ctx.addCookies([{name:'sessionid',value:seed.sessions[role],url:origin}]);
      await ctx.route('**/*', route => new URL(route.request().url()).origin===origin ? route.continue() : route.abort());
      const page = await ctx.newPage(); page.setDefaultTimeout(15000); page.setDefaultNavigationTimeout(30000);
      const root = `${base}/workspace/${seed.workspace}`;
      await page.goto(`${root}/content/${seed.review.team_review}/?tab=comments`);
      const csrf = (await ctx.cookies()).find(c=>c.name==='csrftoken').value;
      async function post(action,path,data,expected) {
        const status = await page.evaluate(async ({url,data,csrf}) => (await fetch(url,{method:'POST',body:new URLSearchParams(data),headers:{'X-CSRFToken':csrf,'HX-Request':'true'}})).status,{url:root+path,data,csrf});
        assert(expected.includes(status), `${role}/${action}: ${status}`);
        results.push({role,action,status});
      }
      if(role==='contributor') {
        await post('body target foreign','/compose/autosave/',{_autosave_post_id:seed.review.foreign,title:'Intrusion',selected_accounts:''},[403]);
        for(const state of ['approved','published']) await post('body target '+state,'/compose/autosave/',{_autosave_post_id:seed.review[state],title:'Changed',selected_accounts:seed.account},[200]);
      }
      if(role==='owner') {
        // The team must not skip the client's sign-off (two-stage workspace).
        await post('team bulk at client stage','/approvals/bulk/',{action:'approve',post_ids:seed.review.client_review},[403]);
        await post('team approve at client stage',`/approvals/${seed.review.client_review}/approve/`,{},[403]);
      }
      if(role==='client') {
        // E-08-ex6b-01: in a team-only workspace the client never decides, on any path.
        const teamOnly = `${base}/workspace/${seed.review.team_only_workspace}`;
        for (const name of ['team_only_review','team_only_client']) {
          for (const path of [`/approvals/${seed.review[name]}/approve/`, `/content/${seed.review[name]}/approval/`]) {
            const status = await page.evaluate(async ({url,csrf}) => (await fetch(url,{method:'POST',redirect:'manual',body:new URLSearchParams({decision:'approve',expected_stage:'review'}),headers:{'X-CSRFToken':csrf}})).status,{url:teamOnly+path,csrf});
            assert.equal(status, 403, `client/team-only ${name} ${path}: ${status}`);
            results.push({role,action:`team-only ${name} ${path.split('/')[1]}`,status});
          }
        }
        await page.goto(`${teamOnly}/content/${seed.review.team_only_client}/?tab=approval`);
        assert.equal(await page.getByRole('button',{name:/approve/i}).count(),0);
        await page.screenshot({path:'/workspace/ops/screenshoty/P1-B25/review-client-team-only.png'});
        await page.goto(`${root}/content/${seed.review.team_review}/?tab=comments`);
        for(const action of ['approve','request-changes','reject']) await post('team stage '+action,`/approvals/${seed.review.team_review}/${action}/`,{comment:'Please revise'},[403]);
        await post('bulk team stage','/approvals/bulk/',{action:'approve',post_ids:seed.review.team_review},[403]);
        await post('delete foreign comment',`/approvals/${seed.review.team_review}/comments/${seed.review.comment}/delete/`,{},[403]);
        await post('client stage approve',`/approvals/${seed.review.client_review}/approve/`,{},[204]);
      }
      if(role==='viewer') {
        assert.equal(await page.locator('textarea[name="body"]').count(),0);
        await post('content comment',`/content/${seed.review.team_review}/comments/`,{body:'Denied'},[403]);
        await post('upstream comment',`/approvals/${seed.review.team_review}/comments/`,{body:'Denied'},[403]);
      }
      await page.screenshot({path:`/workspace/ops/screenshoty/P1-B25/review-${role}.png`});
      await ctx.close();
    }
    await fs.writeFile('/workspace/ops/test-output/P1-B25/browser-review.json',JSON.stringify(results,null,2));
    console.log(JSON.stringify(results));
  } finally {await browser.close();clearTimeout(deadline);}
})().catch(e=>{console.error(e);process.exit(1)});
