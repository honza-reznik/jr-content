const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const origin = process.env.JRC_PUBLIC_ORIGIN;
const basePath = process.env.JRC_BASE_PATH ?? '/jrc';
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({ ignoreHTTPSErrors: true });
    const page = await context.newPage();
    for (let i = 0; i < 30; i++) {
      try { await page.goto(origin + basePath + '/health/'); break; }
      catch (error) { if (i === 29) throw error; await new Promise(r => setTimeout(r, 500)); }
    }
    if (basePath) assert.equal((await context.request.get(origin + '/')).status(), 404);
    for (const suffix of ['/admin/', '/video/setup', '/video-api/setup', '/video-api/docs', '/video-api/openapi.json']) {
      assert.equal((await context.request.get(origin + basePath + suffix)).status(), 404, suffix);
    }
    const unsigned = await context.request.get(origin + basePath + '/storage/jrc-video/private-missing.mp4');
    assert.equal(unsigned.status(), 403);
    assert(unsigned.headers()['content-security-policy'].includes('sandbox'));
    const login = await page.goto(origin + basePath + '/accounts/login/');
    assert.equal(login.status(), 200);
    const hsts = login.headers()['strict-transport-security'];
    assert(hsts && !/includeSubDomains|preload/i.test(hsts));
    const csrf = (await context.cookies()).find(cookie => cookie.name === 'csrftoken');
    assert(csrf?.secure && csrf.path === basePath + '/');
    // Podvržená X-Real-IP nesmí rozdělit limit, rozdílní skuteční klienti ano.
    const limited = async (ip, forged) => context.request.get(origin + basePath + '/video-api/share/p1-missing', {
      headers: { 'X-Test-Client-IP': ip, 'X-Real-IP': forged },
    });
    const subnet = Math.floor(Math.random() * 250) + 1;
    const clientA = `198.18.${subnet}.70`;
    const clientB = `198.18.${subnet}.71`;
    for (let i = 0; i < 30; i++) assert.equal((await limited(clientA, `198.51.100.${i + 1}`)).status(), 404);
    assert.equal((await limited(clientA, '198.51.100.99')).status(), 429);
    assert.equal((await limited(clientB, '198.51.100.99')).status(), 404);
    // REST/MCP je přítomné pod předponou a vyžaduje přístupové údaje.
    assert.equal((await context.request.get(origin + basePath + '/api/v1/me/')).status(), 401);
    const mcp = await context.request.post(origin + basePath + '/api/v1/mcp/', {
      headers: { Accept: 'application/json, text/event-stream' },
      data: { jsonrpc: '2.0', id: 1, method: 'initialize', params: {} },
    });
    assert.equal(mcp.status(), 401);
    const metadataUrl = /resource_metadata="([^"]+)"/.exec(mcp.headers()['www-authenticate'])?.[1];
    assert.equal(metadataUrl, origin + basePath + '/.well-known/oauth-protected-resource/api/v1/mcp');
    const resource = await (await context.request.get(metadataUrl)).json();
    assert.equal(resource.resource, origin + basePath + '/api/v1/mcp');
    const oauth = await (await context.request.get(origin + basePath + '/.well-known/oauth-authorization-server')).json();
    assert.equal(oauth.authorization_endpoint, origin + basePath + '/oauth/authorize/');
    console.log('OK HTTPS: předpona, soukromé vstupy 404, Secure CSRF, HSTS bez subdomén, skutečná IP a REST.');
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
