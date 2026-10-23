// Spouští se v jednorázovém kontejneru ff-web s jedinou sítí internal.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { chromium } = require('playwright');

async function main() {
  // Celý web, i stránky nepoužité při krátkém průchodu prohlížečem.
  function checkSource(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (['node_modules', '.next', '.git', '__tests__'].includes(entry.name)) continue;
      const file = path.join(dir, entry.name);
      if (entry.isDirectory()) checkSource(file);
      else if (/\.(tsx?|[cm]?js|css|html|json)$/.test(entry.name)) {
        assert(!/next\/font\/google|fonts\.(googleapis|gstatic)\.com|@sentry\/|google-analytics\.com|googletagmanager\.com|cdn\.jsdelivr\.net|cdnjs\.cloudflare\.com|unpkg\.com/i.test(fs.readFileSync(file, 'utf8')),
          `Externí písmo/analytika/CDN v ${file}`);
      }
    }
  }
  checkSource('/app');
  console.log('OK celý zdroj webu: bez Google Fonts, Sentry, analytiky a známých CDN.');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'jrc-offline-'));
  const app = path.join(tmp, 'app');
  fs.cpSync('/app', app, { recursive: true, filter: source =>
    !['node_modules', '.next', '.git'].includes(path.basename(source)) });
  // Vlastní kopie zachová relativní odkazy pnpm. Symlink na sdílené node_modules
  // by při standalone trasování vedl ven z kořene sestavení a přepisoval závislosti.
  fs.cpSync('/app/node_modules', path.join(app, 'node_modules'), {
    recursive: true, verbatimSymlinks: true, mode: fs.constants.COPYFILE_FICLONE,
  });
  const audit = path.join(tmp, 'network.jsonl');
  const env = { ...process.env, NEXT_PUBLIC_API_URL: 'http://jrc-ff-api:8000',
    JRC_NETWORK_AUDIT: audit, NODE_OPTIONS: '--require=/jrc-scripts/web-network-audit.cjs' };
  const next = path.join(app, 'node_modules/next/dist/bin/next');
  const server = spawn(process.execPath, [next, 'dev', '--hostname', '0.0.0.0', '--port', '3100'],
    { cwd: app, env, stdio: 'inherit' });
  const serverExit = new Promise(resolve => server.on('exit', resolve));
  let browser;
  try {
    // První požadavek skutečně kompiluje: dočasná složka nemá .next ani font cache.
    let ready = false;
    for (let i = 0; i < 120; i++) {
      try {
        const response = await fetch('http://127.0.0.1:3100/video/login', { signal: AbortSignal.timeout(20000) });
        if (response.ok) { ready = true; break; }
      } catch {}
      if (server.exitCode !== null) throw new Error('Next.js předčasně skončil');
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    assert(ready, 'Studený web nenaběhl');
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext();
    const foreign = [];
    const requests = [];
    const allowed = new Set(['127.0.0.1', 'jrc-ff-api']);
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      requests.push(url.pathname);
      if (!allowed.has(url.hostname)) {
        foreign.push(url.origin);
        return route.abort();
      }
      return route.continue();
    });
    const page = await context.newPage();
    const sockets = [];
    page.on('websocket', ws => sockets.push(new URL(ws.url()).hostname));
    await page.goto('http://127.0.0.1:3100/video/login', { waitUntil: 'networkidle' });
    await page.evaluate(() => document.fonts.ready);
    await page.locator('input[type="email"]').waitFor({ state: 'visible' });
    assert(requests.some(url => /\.(ttf|woff2?)(\?|$)/.test(url)), 'Chybí místní písmo');
    assert.equal(await page.evaluate(() => [...document.fonts].some(font => font.status === 'loaded')), true);
    await page.goto('http://127.0.0.1:3100/video/setup', { waitUntil: 'networkidle' });
    assert.deepEqual(foreign, [], 'Prohlížeč požádal o cizí doménu');
    assert(sockets.every(host => allowed.has(host)), 'Cizí WebSocket');
    console.log(`OK offline prohlížeč: ${requests.length} místních požadavků, písmo načtené, žádná cizí doména.`);
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
    await serverExit;
  }
  // Stejné zdroje sestavíme bez internetu a bez cache vývojového serveru.
  fs.rmSync(path.join(app, '.next'), { recursive: true, force: true });
  const build = spawn(process.execPath, [next, 'build'], { cwd: app, env, stdio: 'inherit' });
  assert.equal(await new Promise(resolve => build.on('exit', resolve)), 0, 'Offline next build');
  const records = fs.readFileSync(audit, 'utf8').trim().split('\n').map(line => JSON.parse(line));
  const allowedHosts = new Set(['jrc-ff-api', 'localhost', '127.0.0.1', '::1', '0.0.0.0']);
  assert(records.some(row => row.host === 'jrc-ff-api'), 'Audit nezachytil ani místní API');
  assert.deepEqual(records.filter(row => !allowedHosts.has(row.host)), [], 'Next.js zkusil cizí spojení');
  console.log(`OK offline server: ${records.length} spojení/DNS, studený start i produkční build bez cizí domény.`);
  fs.rmSync(tmp, { recursive: true, force: true });
}
main().catch(error => { console.error(error); process.exitCode = 1; });
