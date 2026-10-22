// Pomocný běh Playwright uvnitř jrc-dev: node pw.js <krok.js>
// Krok je modul exportující async (page, ctx, h) => {...}. Přihlášení se drží v /tmp/bb-state.json.
const { chromium } = require('playwright');
const fs = require('fs');
const BASE = process.env.BB_BASE || 'http://app:8000';
// Každý uživatel má vlastní soubor s přihlášením (BB_STATE), výchozí je admin.
const STATE = process.env.BB_STATE || '/tmp/bb-state.json';
(async () => {
  const step = require(require('path').resolve(process.argv[2]));
  // Chromium jinak u neznámých názvů hostitele zkouší HTTPS.
  const browser = await chromium.launch({ args: ['--disable-features=HttpsUpgrades,HttpsFirstBalancedModeAutoEnable'] });
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 860 },
    storageState: fs.existsSync(STATE) ? STATE : undefined,
  });
  const page = await ctx.newPage();
  // Ven smí jen veřejné knihovny z cdn.jsdelivr.net (aplikace je načítá); vše ostatní mimo stack se blokuje.
  await page.route((u) => !u.href.startsWith(BASE) && !u.href.startsWith('https://cdn.jsdelivr.net/'), (r) => {
    console.log('BLOKOVÁNO', r.request().url().slice(0, 100));
    return r.abort();
  });
  const h = {
    BASE,
    go: (p) => page.goto(BASE + p, { waitUntil: 'networkidle' }),
    shot: (name) => page.screenshot({ path: `/tmp/bb-shots/${name}.png` }),
    text: async () => (await page.innerText('body')).replace(/\n{2,}/g, '\n').slice(0, 4000),
    fields: () => page.$$eval('input,select,textarea,button', (els) => els.map((e) =>
      `${e.tagName.toLowerCase()} name=${e.name || ''} type=${e.type || ''} id=${e.id || ''} txt=${(e.innerText || e.value || '').trim().slice(0, 40)}`)),
  };
  fs.mkdirSync('/tmp/bb-shots', { recursive: true });
  try {
    await step(page, ctx, h);
  } catch (e) {
    console.error('CHYBA:', e.message);
    await page.screenshot({ path: '/tmp/bb-shots/_chyba.png' }).catch(() => {});
    console.log('URL:', page.url());
    process.exitCode = 1;
  }
  await ctx.storageState({ path: STATE });
  await browser.close();
})();
