// Ověření skutečných veřejných adres; pouze Docker se sítí hostitele.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const port = (name, fallback) => process.env[name] || fallback;
const url = (name, fallback) => `http://127.0.0.1:${port(name, fallback)}`;

(async () => {
  const bb = (process.env.JRC_PUBLIC_ORIGIN || url('JRC_APP_PORT', 13000)) + (process.env.JRC_BASE_PATH ?? '/jrc');
  const api = `${bb}/video-api`;
  const web = bb;
  const s3 = url('JRC_S3_PORT', 13900);
  assert.equal((await fetch(`${api}/health`)).status, 200);
  assert.equal((await fetch(`${bb}/health/`)).status, 200);
  assert.equal((await fetch(`${s3}/`)).status, 403);
  // Správa FreeFrame a dokumentace API nejsou přes společný vstup dostupné.
  for (const route of ['/admin/users', '/ADMIN/users', '/%61dmin/users', '/admin']) {
    assert.equal((await fetch(`${api}${route}`)).status, 404, route);
  }
  for (const route of ['/docs', '/redoc', '/openapi.json']) {
    assert.equal((await fetch(`${api}${route}`)).status, 404, route);
  }
  // Bezpečnostní hlavičky: Brightbean se nevkládá nikam, /video jen do stejné adresy.
  for (const [route, frame] of [[`${bb}/health/`, 'DENY'], [`${web}/video/login`, 'SAMEORIGIN'],
    [`${api}/health`, null]]) {
    const headers = (await fetch(route)).headers;
    assert.equal(headers.get('x-content-type-options'), 'nosniff', route);
    assert.equal(headers.get('referrer-policy'), 'same-origin', route);
    if (frame) assert.equal(headers.get('x-frame-options'), frame, route);
  }
  // F2-B05: vynucená CSP /video; úložiště jen přesný původ místní brány S3.
  const csp = (await fetch(`${web}/video/login`)).headers;
  assert.equal(csp.get('content-security-policy-report-only'), null);
  const directives = Object.fromEntries(csp.get('content-security-policy').split(';')
    .map(item => item.trim().split(/\s+/)).map(([name, ...values]) => [name, values]));
  assert.deepEqual(directives['frame-ancestors'], ["'self'"]);
  assert.deepEqual(directives['default-src'], ["'self'"]);
  assert.deepEqual(directives['img-src'], ["'self'", 'data:', 'blob:']);
  assert.deepEqual(directives['media-src'], ["'self'", 'blob:']);
  assert.deepEqual(directives['connect-src'], ["'self'"]);
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ serviceWorkers: 'block' });
    const foreign = [];
    const fonts = [];
    const responses = [];
    const allowed = new Set([new URL(web).origin]);
    await context.routeWebSocket('**/*', socket => {
      const address = new URL(socket.url());
      address.protocol = address.protocol === 'wss:' ? 'https:' : 'http:';
      if (!allowed.has(address.origin)) {
        foreign.push(socket.url());
        return socket.close();
      }
      socket.connectToServer();
    });
    await context.route('**/*', route => {
      const request = route.request();
      if (!allowed.has(new URL(request.url()).origin)) {
        foreign.push(request.url());
        return route.abort();
      }
      if (request.resourceType() === 'font') fonts.push(request.url());
      return route.continue();
    });
    const page = await context.newPage();
    page.on('response', response => responses.push([response.url(), response.status()]));
    const sockets = [];
    page.on('websocket', socket => sockets.push(socket.url()));
    // Login nemusí API sám volat. Požadavek z kontextu stránky výslovně ověří
    // skutečnou veřejnou cestu přes proxy, včetně pravidel prohlížeče.
    await page.goto(`${web}/video/login`, { waitUntil: 'networkidle', timeout: 60000 });
    await page.locator('input[type="email"]').waitFor({ state: 'visible' });
    const health = await page.evaluate(async address => {
      const response = await fetch(address);
      return { status: response.status, body: await response.json() };
    }, `${api}/health`);
    assert.equal(health.status, 200);
    assert.equal(health.body.status, 'ok');
    await page.evaluate(() => document.fonts.ready);
    assert(fonts.length > 0 && fonts.every(font => font.startsWith(`${web}/video/_next/`)));
    assert(responses.some(([address, status]) => address.startsWith(api) && status === 200), 'Prohlížeč nedosáhne na API');
    assert.deepEqual(foreign, [], 'Skutečný web zkusil cizí doménu');
    assert(sockets.every(address => {
      const socket = new URL(address);
      return socket.hostname === '127.0.0.1' && socket.port === port('JRC_APP_PORT', 13000).toString();
    }), 'Skutečný web zkusil cizí WebSocket');
    console.log('OK brána: API, Brightbean, S3; /admin 404, docs 404, bezpečnostní hlavičky; Chromium přes skutečný web načetl místní písmo/API bez cizí domény.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
