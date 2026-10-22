// F1-B06: ověření pro ADR-003 (varianta „přehrávač FreeFrame vložený do Brightbean, jedno přihlášení").
// Spouštět jen v Dockeru (obraz jrc-dev:local, --network host) proti auditnímu stacku jrc-audit-freeframe
// (návod docs-audit/F1-B02/instalace.md ve worktree F1-B02). Nic neodchází mimo 127.0.0.1.
//
// Co ověřuje:
//  1. Token podepsaný sdíleným tajemstvím JWT_SECRET (tak by ho vydal Brightbean) FreeFrame API přijme.
//  2. Stránka „hostitele" (simuluje Brightbean na jiném portu téhož počítače) vloží přehrávač FreeFrame
//     do <iframe>; FreeFrame to nezakazuje a uvnitř je uživatel přihlášený bez přihlašovacího formuláře
//     (tokeny se uloží stejně jako v apps/web/lib/auth.ts setTokens).
//  3. Ve vloženém přehrávači se video načte a jsou vidět komentáře, desktop i telefon 390×844.
//
// Vstupy: /results/state.json (z audit-api.py), env JWT_SECRET a AUDIT_PASSWORD (z audit/.env),
// OUT = složka na screenshoty a výsledek.
const { chromium } = require('playwright');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');

const API = 'http://127.0.0.1:18020';
const WEB = 'http://127.0.0.1:13020';
const HOST = 'http://127.0.0.1:13999'; // malý server ve skriptu, jen po dobu běhu
const OUT = process.env.OUT || '/out';

const b64 = (o) => Buffer.from(typeof o === 'string' ? o : JSON.stringify(o)).toString('base64url');
function mintAccessToken(userId, secret) {
  const head = b64({ alg: 'HS256', typ: 'JWT' });
  const body = b64({ sub: userId, type: 'access', exp: Math.floor(Date.now() / 1000) + 600, ver: 1 });
  const sig = crypto.createHmac('sha256', secret).update(`${head}.${body}`).digest('base64url');
  return `${head}.${body}.${sig}`;
}

async function api(path, opts = {}) {
  const res = await fetch(API + path, opts);
  return { status: res.status, body: await res.json().catch(() => null) };
}

(async () => {
  const state = JSON.parse(fs.readFileSync('/results/state.json', 'utf8'));
  const result = { checks: {} };

  // Správce se přihlásí heslem jen proto, aby našel id klientky (v ostrém řešení zná id Brightbean).
  const login = await api('/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'honza.audit@example.com', password: process.env.AUDIT_PASSWORD }),
  });
  if (login.status !== 200) throw new Error(`login správce: ${login.status}`);
  // Klientku do projektu přidá správce přes API, tak jak by to dělala synchronizace z Brightbean
  // (role reviewer = smí komentovat a schvalovat, nesmí nahrávat).
  const admin = { Authorization: `Bearer ${login.body.access_token}`, 'Content-Type': 'application/json' };
  const users = await api('/users/search?q=ella', { headers: admin });
  const ella = (users.body || []).find((u) => /ella/i.test(u.email));
  if (!ella) throw new Error('účet klientky Ella ve FreeFrame nenalezen (spusť nejdřív audit-client.py)');
  const add = await api(`/projects/${state.project_id}/members`, {
    method: 'POST', headers: admin, body: JSON.stringify({ user_id: ella.id, role: 'reviewer' }),
  });
  result.checks.sync_add_member_via_api = [201, 400, 409].includes(add.status);

  // 1) token vydaný „zvenku" sdíleným tajemstvím
  const access = mintAccessToken(ella.id, process.env.JWT_SECRET);
  const me = await api('/auth/me', { headers: { Authorization: `Bearer ${access}` } });
  result.checks.minted_token_accepted = me.status === 200 && me.body.id === ella.id;
  const foreign = await api(`/projects/${state.project_id}`, { headers: { Authorization: `Bearer ${access}` } });
  result.checks.ella_sees_project = foreign.status === 200;

  // Stránka „hostitele" běží na skutečném serveru na 127.0.0.1 (podstrčená odpověď přes page.route
  // by Chromium považoval za veřejnou adresu a vložení místní adresy zablokoval).
  const html = `<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width"><title>Brightbean (simulace)</title>
<body style="margin:0;font-family:sans-serif"><div style="padding:8px;background:#eef">Brightbean: obsah „Reel o …“ › Video (IG)</div>
<iframe id=ff src="${WEB}/projects/${state.project_id}/assets/${state.asset_id}" style="border:0;width:100%;height:calc(100vh - 40px)" allow="fullscreen"></iframe></body>`;
  const server = http.createServer((req, res) => { res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); res.end(html); });
  await new Promise((resolve) => server.listen(13999, '127.0.0.1', resolve));
  console.log('API:', JSON.stringify(result.checks));
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    for (const [name, viewport] of [['desktop', { width: 1440, height: 900 }], ['mobil-390', { width: 390, height: 844 }]]) {
      const context = await browser.newContext({ viewport });
      await context.route('**/*', (route) => {
        const url = new URL(route.request().url());
        return ['127.0.0.1', 'localhost'].includes(url.hostname) ? route.continue() : route.abort();
      });
      const page = await context.newPage();
      page.setDefaultTimeout(60000);
      const failed = [];
      page.on('requestfailed', (r) => failed.push(`${r.url()} ${r.failure()?.errorText}`));
      page.on('response', (r) => { if (r.status() >= 300 && (r.url().startsWith(WEB) || r.url().startsWith(API))) failed.push(`${r.status()} ${r.url()}`); });
      // 2) „předání přihlášení": uložit tokeny na původ FreeFrame stejně jako setTokens() v lib/auth.ts.
      //    V navrhovaném řešení to udělá malá stránka FreeFrame /jrc-sso, sem se to simuluje skriptem.
      await page.goto(`${WEB}/login`, { waitUntil: 'domcontentloaded', timeout: 120000 });
      await page.evaluate(([a]) => {
        localStorage.setItem('ff_access_token', a);
        localStorage.setItem('ff_refresh_token', a);
        document.cookie = `ff_access_token=${a}; path=/; max-age=600; SameSite=Lax`;
        document.cookie = `ff_refresh_token=${a}; path=/; max-age=600; SameSite=Lax`;
      }, [access]);
      await page.goto(`${HOST}/obsah/reel`);
      const frame = page.frameLocator('#ff');
      try {
        await frame.locator('video').first().waitFor({ timeout: 240000 });
      } catch (error) {
        await page.screenshot({ path: `${OUT}/${name}-chyba.png` });
        console.error('rámy:', page.frames().map((x) => x.url()));
        console.error('selhané požadavky:', failed.slice(0, 10));
        throw error;
      }
      const ff = page.frames().find((f) => f.url().startsWith(WEB));
      await ff.waitForFunction(() => document.querySelector('video')?.readyState >= 2, null, { timeout: 90000 });
      await page.waitForTimeout(3000);
      const text = await ff.locator('body').innerText();
      result.checks[`iframe_${name}_video_loaded`] = true;
      // Komentáře z auditu musí být vidět (na telefonu je panel sbalený, stačí „Comments · N“).
      result.checks[`iframe_${name}_comments_visible`] = /zkontrolováno v auditu|Ella: celé video|Comments · [1-9]/.test(text);
      if (!result.checks[`iframe_${name}_comments_visible`]) console.error(`${name}: text rámu:`, text.slice(0, 800));
      result.checks[`iframe_${name}_url`] = ff.url().replace(/[0-9a-f-]{36}/g, '<id>');
      await page.waitForTimeout(1500);
      await page.screenshot({ path: `${OUT}/${name}-vlozeny-prehravac.png` });
      await context.close();
    }
  } finally {
    await browser.close();
    server.close();
  }
  result.result = Object.values(result.checks).every((v) => v !== false) ? 'passed' : 'failed';
  fs.writeFileSync(`${OUT}/vysledek-iframe.json`, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
})().catch((error) => { console.error(error); process.exitCode = 1; });
