const http = require('http');
const fs = require('fs');
const { chromium } = require('playwright');
(async () => {
  const links = JSON.parse(fs.readFileSync('/workspace/ops/test-output/P1-B24/links.json'));
  const fixture = JSON.parse(fs.readFileSync('/workspace/ops/test-output/P1-B24/fixture.json'));
  const password = require('crypto').randomBytes(24).toString('base64url');
  const proxy = http.createServer((req, res) => {
    const target = http.request({ hostname:'jrc-proxy', port:8080, path:req.url, method:req.method, headers:req.headers }, up => {
      res.writeHead(up.statusCode, up.headers); up.pipe(res);
    });
    target.on('error', () => res.destroy()); req.pipe(target);
  });
  await new Promise(resolve => proxy.listen(13663, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless:true, args:['--disable-dev-shm-usage','--js-flags=--max-old-space-size=2048']});
  try {
    for (const viewport of [{width:1280,height:900},{width:390,height:844}]) {
      const context = await browser.newContext({viewport, baseURL:'http://127.0.0.1:13663'});
      const page = await context.newPage(); page.setDefaultTimeout(15000);
      await page.goto('/jrc/accounts/password/reset/');
      await page.locator('[name=email]').fill('p1b24-nobody@example.invalid');
      await page.getByRole('button', {name:'Send Reset Link'}).click();
      await page.getByText('Check your inbox for the next steps.', {exact:false}).waitFor();
      await page.screenshot({path:`/workspace/ops/screenshoty/P1-B24/reset-unknown-${viewport.width}.png`,fullPage:true});
      if (await page.locator('a[href*="signup"]').count()) throw Error('Closed signup advertised');
      await context.close();
    }
    const context = await browser.newContext({baseURL:'http://127.0.0.1:13663'});
    const page = await context.newPage(); page.setDefaultTimeout(15000);
    await page.goto(links.confirm_url);
    await page.getByRole('button', {name:'Confirm Email',exact:true}).click();
    await page.waitForLoadState('networkidle');
    await page.screenshot({path:'/workspace/ops/screenshoty/P1-B24/confirmed.png',fullPage:true});
    await page.goto(links.reset_url);
    await page.locator('[name=password1]').fill(password);
    await page.locator('[name=password2]').fill(password);
    await page.getByRole('button', {name:'Set New Password',exact:true}).click();
    await page.getByText('Password changed', {exact:false}).first().waitFor();
    await page.screenshot({path:'/workspace/ops/screenshoty/P1-B24/password-changed.png',fullPage:true});
    await page.goto('/jrc/accounts/login/');
    await page.locator('[name=login]').fill(fixture.email);
    await page.locator('[name=password]').fill(password);
    await page.getByRole('button',{name:'Log in',exact:true}).click();
    await page.waitForURL('**/calendar/');
    console.log('Chromium: desktop/mobile unknown reset, confirmation, password reset and login passed');
  } finally { await browser.close(); proxy.close(); }
})().catch(error => {console.error(error); process.exitCode=1;});
