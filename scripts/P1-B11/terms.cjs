// P1-B11: Chromium jen v Dockeru jrc-p1b11 (kontejner jrc-dev), místní data, cizí adresy zablokované.
const {chromium} = require('/opt/jrc-tools/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const net = require('node:net');

(async () => {
  const origin = 'http://127.0.0.1:13543';
  const base = `${origin}/jrc`;
  const root = '/workspace/ops';
  const seed = JSON.parse(await fs.readFile(`${root}/test-output/P1-B11/session.json`, 'utf8'));
  // Prohlížeč běží v kontejneru jrc-dev: veřejný původ (kvůli CSRF a cookies) přeposíláme na proxy v síti Dockeru.
  const forward = net.createServer(socket => {
    const upstream = net.connect(8080, 'jrc-proxy');
    socket.pipe(upstream).pipe(socket);
    socket.on('error', () => upstream.destroy());
    upstream.on('error', () => socket.destroy());
  });
  await new Promise(resolve => forward.listen(13543, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true});
  const errors = [], external = [], visited = [], log = [];
  const newContext = async width => {
    const context = await browser.newContext({timezoneId: 'Europe/Prague', viewport: {width, height: width === 390 ? 844 : 1050}});
    // Ostatní adresy blokujeme; místní původ jde přes přeposílání níže.
    await context.route(url => new URL(url).origin !== origin, route => { external.push(route.request().url()); return route.abort(); });
    context.on('request', request => { if (request.isNavigationRequest()) visited.push(new URL(request.url()).pathname); });
    context.on('page', page => page.on('pageerror', error => errors.push(String(error))));
    return context;
  };
  const noBrightbean = async (page, name) => {
    const html = await page.content();
    assert(!html.includes('brightbean.xyz'), `brightbean.xyz na ${name}`);
    assert(!html.includes('Almost there'), `Almost there na ${name}`);
  };
  const capture = async (page, name, width) => {
    await page.evaluate(() => document.fonts.ready);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Přetečení ${name} ${width}`);
    await page.screenshot({path: `${root}/screenshoty/P1-B11/${name}-${width}.png`, fullPage: true});
    log.push(`${name}-${width}.png`);
  };
  try {
    for (const width of [1440, 390]) {
      // 1) Nový owner z createsuperuser (bez souhlasu) se přihlásí a jde rovnou do aplikace.
      const ownerContext = await newContext(width);
      const page = await ownerContext.newPage();
      await page.goto(`${base}/accounts/login/`, {waitUntil: 'networkidle'});
      await noBrightbean(page, 'login');
      await capture(page, '01-prihlaseni', width);
      await page.locator('input[name=login]').fill(seed.owner.email);
      await page.locator('input[name=password]').fill(seed.owner.password);
      await Promise.all([page.waitForEvent('load'), page.locator('form button[type=submit]').first().click()]);
      await page.waitForLoadState('networkidle');
      assert(!visited.some(p => p.includes('accept-terms')), visited.join(' '));
      assert.equal(await page.locator('input[name=password]').count(), 0, 'Owner zůstal na přihlášení');
      await noBrightbean(page, 'owner po přihlášení');
      await capture(page, '02-owner-po-prihlaseni', width);
      // Přímá adresa souhlasu v jrc neexistuje.
      const terms = await page.goto(`${base}/accounts/accept-terms/`);
      assert.equal(terms.status(), 404);
      visited.pop();
      await ownerContext.close();

      // 2) Pozvaný klient: pozvánka → registrace bez podmínek brightbean.xyz → rovnou do aplikace.
      const invite = seed.invites[String(width)];
      const clientContext = await newContext(width);
      const cpage = await clientContext.newPage();
      await cpage.goto(`${base}/members/invite/${invite.token}/accept/`, {waitUntil: 'networkidle'});
      await noBrightbean(cpage, 'pozvánka');
      await capture(cpage, '03-pozvanka', width);
      await cpage.goto(`${base}/accounts/signup/`, {waitUntil: 'networkidle'});
      await noBrightbean(cpage, 'registrace');
      assert(!(await cpage.content()).includes('Terms of Service'));
      await capture(cpage, '04-registrace', width);
      assert.equal(await cpage.locator('input[name=email]').inputValue(), invite.email);
      await cpage.locator('input[name=password1]').fill(seed.owner.password + '-c');
      await Promise.all([cpage.waitForEvent('load'), cpage.locator('form button[type=submit]').first().click()]);
      await cpage.waitForLoadState('networkidle');
      assert(!visited.some(p => p.includes('accept-terms')), visited.join(' '));
      assert.equal(await cpage.locator('input[name=password1]').count(), 0, 'Klient zůstal na registraci');
      await noBrightbean(cpage, 'klient po registraci');
      await capture(cpage, '05-klient-po-registraci', width);
      await clientContext.close();
    }
    assert.deepEqual(errors, [], errors.join('\n'));
    assert.deepEqual(external, [], external.join('\n'));
    console.log('OK P1-B11', log.join(', '));
    console.log('NAVIGACE', visited.join(' '));
  } finally {
    await browser.close();
    forward.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
