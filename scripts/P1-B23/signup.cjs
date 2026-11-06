const {chromium} = require('/opt/jrc-tools/node_modules/playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
(async () => {
 const seed = JSON.parse(fs.readFileSync('/workspace/brightbean/.p1b23.env.local'));
 const base = 'http://127.0.0.1:13653/jrc';
 const browser = await chromium.launch({headless:true,args:['--js-flags=--max-old-space-size=512','--renderer-process-limit=2']});
 try {
  for (const signup of seed.signups) {
   const width = signup.role === 'manager' ? 1440 : 390;
   const context = await browser.newContext({viewport:{width,height:900}});
   try {
    await context.route('**/*', r => new URL(r.request().url()).origin === new URL(base).origin ? r.continue() : r.abort());
    const page = await context.newPage();
    page.setDefaultTimeout(15000); page.setDefaultNavigationTimeout(20000);
    assert.equal((await page.goto(base+'/members/invite/'+signup.token+'/accept/')).status(),200);
    await page.getByRole('link',{name:'Sign Up to Join',exact:true}).click();
    assert.equal(await page.locator('input[name=email]').inputValue(),signup.email);
    await page.locator('input[name=password1]').fill(signup.password);
    if(await page.locator('input[name=password2]').count()) await page.locator('input[name=password2]').fill(signup.password);
    await Promise.all([page.waitForURL(url => !url.pathname.endsWith('/accounts/signup/')),page.getByRole('button',{name:'Create Account',exact:true}).click()]);
    // Návrat na přijatou pozvánku řeší samostatný blok P1-B24.
    await page.screenshot({path:`/workspace/ops/screenshoty/P1-B23/signup-landing-${signup.role}-${width}.png`});
    assert.equal((await page.goto(base+'/accounts/settings/')).status(),200);
    assert((await page.locator('body').innerText()).includes('in JR Content'));
    assert.equal(await page.locator('#organization-switch').count(),0);
    await page.screenshot({path:`/workspace/ops/screenshoty/P1-B23/signup-${signup.role}-${width}.png`});
    assert.equal((await page.goto(base+'/members/')).status(),403);
    assert.equal((await page.goto(base+`/workspaces/${seed.workspace}/settings/`)).status(),signup.role==='manager'?200:403);
    console.log('Invited signup passed',signup.role,width);
   } finally {await context.close();}
  }
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
