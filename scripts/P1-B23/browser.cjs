const {chromium} = require('/opt/jrc-tools/node_modules/playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
(async () => {
 const mode = process.argv[2] || 'after';
 const seed = JSON.parse(fs.readFileSync('/workspace/brightbean/.p1b23.env.local'));
 const base = 'http://127.0.0.1:13653/jrc';
 const browser = await chromium.launch({headless:true,args:['--js-flags=--max-old-space-size=512','--renderer-process-limit=2']});
 try {
  for (const width of (mode === 'before' ? [1440] : [1440,390])) {
  for (const role of (mode === 'before' ? ['manager','contributor'] : Object.keys(seed.sessions))) {
   const context = await browser.newContext({viewport:{width,height:900}});
   try {
    await context.addCookies([{name:'sessionid',value:seed.sessions[role],url:base}]);
    await context.route('**/*', r => new URL(r.request().url()).origin === new URL(base).origin ? r.continue() : r.abort());
    const page = await context.newPage(); let token; page.setDefaultTimeout(15000); page.setDefaultNavigationTimeout(20000);
    for (const [name,path] of Object.entries({profile:'/accounts/settings/',organization:'/organizations/settings/',workspaces:'/organizations/workspaces/',team:'/members/',keys:'/organizations/api-keys/',settings:`/workspaces/${seed.workspace}/settings/`,approvals:`/workspaces/${seed.workspace}/settings/approvals/`,channels:`/workspaces/${seed.workspace}/profile/`})) {
     const response = await page.goto(base+path);
     const status=response.status();
     console.log(mode,role,name,status);
     if(mode==='before' && role==='manager' && name==='team') assert((await page.locator('body').innerText()).includes('My Organization'));
     if(mode==='before' && role==='contributor' && name==='settings') {
      assert.equal(status,200);
      const token=await page.locator('input[name=csrfmiddlewaretoken]').first().inputValue();
      const saved=await context.request.post(base+path,{form:{csrfmiddlewaretoken:token,name:'P1-B23 role checks'},headers:{Referer:base+path},maxRedirects:0});
      assert.equal(saved.status(),302); console.log('before contributor workspace POST',saved.status());
     }
     if(mode==='after') {
      const allowed = name==='profile' || (['settings','approvals','channels'].includes(name) ? ['owner','manager'].includes(role) : role==='owner');
      assert.equal(status,allowed?200:403,`${role} ${name}`);
      if(name==='profile') token=await page.locator('input[name=csrfmiddlewaretoken]').first().inputValue();
      if(name==='profile' && role!=='nonmember') {
       assert((await page.locator('body').innerText()).includes('JR Content'));
       assert((await page.locator('body').innerText()).includes('in JR Content'));
       assert.equal(await page.locator('a[href$="/members/"]').count(),role==='owner'?2:0);
      }
     }
     if(name==='team'||name==='settings'||name==='profile') await page.screenshot({path:`/workspace/ops/screenshoty/P1-B23/${mode}-${role}-${name}-${width}.png`});
    }
    if(mode==='after') {
     if(['owner','manager'].includes(role)) {
      const path=`/workspaces/${seed.workspace}/settings/`;
      const response=await context.request.post(base+path,{form:{csrfmiddlewaretoken:token,name:'P1-B23 role checks'},headers:{Referer:base+path},maxRedirects:0});
      assert.equal(response.status(),302);
      await page.goto(base+path);
      assert.equal(await page.locator('input#name').inputValue(),'P1-B23 role checks');
      console.log('Saved workspace settings',role,width);
     }
     for (const [path,form,allowed] of [
      ['/organizations/settings/',{action:'update_name',name:'Forbidden'},role==='owner'],
      ['/members/invite/',{email:'forbidden@example.invalid'},role==='owner'],
      ['/organizations/api-keys/issue/',{},role==='owner'],
      [`/workspaces/${seed.workspace}/settings/`,{name:'Forbidden'},['owner','manager'].includes(role)],
      [`/workspaces/${seed.workspace}/settings/approvals/`,{approval_workflow_mode:'none'},['owner','manager'].includes(role)],
      [`/workspaces/${seed.workspace}/profile/`,{platform:'custom',name:'Forbidden'},['owner','manager'].includes(role)],
     ]) {
      if(allowed) continue;
      const response=await context.request.post(base+path,{form:{...form,csrfmiddlewaretoken:token},headers:{Referer:base+'/accounts/settings/'},maxRedirects:0});
      assert.equal(response.status(),403,`${role} POST ${path}`);
     }
     if(role==='manager') {
      await page.goto(base+'/accounts/settings/');
      if(width===390) await page.getByRole('button',{name:'Open navigation'}).click();
      await page.locator('.sidebar-org-expanded > button').first().click();
      await page.screenshot({path:`/workspace/ops/screenshoty/P1-B23/organization-switch-${width}.png`});
      await page.locator('#organization-switch').selectOption(seed.personal.manager);
      await Promise.all([page.waitForURL(/calendar/),page.getByRole('button',{name:'Switch organization',exact:true}).click()]);
      let response=await page.goto(base+'/members/'); assert.equal(response.status(),200);
      assert((await page.locator('body').innerText()).includes('My Organization'));
      if(width===390) await page.getByRole('button',{name:'Open navigation'}).click();
      await page.locator('.sidebar-org-expanded > button').first().click();
      await page.locator('#organization-switch').selectOption(seed.organization);
      await Promise.all([page.waitForURL(/calendar/),page.getByRole('button',{name:'Switch organization',exact:true}).click()]);
      response=await page.goto(base+'/members/');assert.equal(response.status(),403);
      console.log('Switch organizations passed',width);
     }
    }
   } finally {await context.close();}
  }
  }
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
