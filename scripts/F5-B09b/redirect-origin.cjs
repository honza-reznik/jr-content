// Izolované ověření přesměrování stejného původu se síťovým zachytáváním Playwrightu.
// Jen Docker, dočasné servery na 127.0.0.1, bez tokenů a bez požadavků do aplikace.
const http = require('node:http');
const { chromium } = require('playwright');
(async () => {
  const seen = [];
  const target = http.createServer((req, res) => {
    seen.push(req.headers.origin || '(none)');
    if (req.headers.origin === origin) res.setHeader('Access-Control-Allow-Origin', origin);
    res.end('segment');
  });
  await new Promise(resolve => target.listen(0, '127.0.0.1', resolve));
  const source = http.createServer((req, res) => {
    if (req.url === '/redirect') {
      res.writeHead(302, { Location: `http://127.0.0.1:${target.address().port}/segment` });
    }
    res.end('<!doctype html>local origin check');
  });
  await new Promise(resolve => source.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${source.address().port}`;
  const browser = await chromium.launch({ headless: true });
  try {
    for (const intercept of [false, true]) {
      const context = await browser.newContext();
      // Like F10-B02: only URLs outside the two local origins match this route.
      if (intercept) await context.route(/^https?:\/\/(?!127\.0\.0\.1:)/, route => route.abort());
      const page = await context.newPage();
      await page.goto(origin);
      const result = await page.evaluate(() => new Promise(resolve => {
        const xhr = new XMLHttpRequest();
        xhr.open('GET', '/redirect');
        xhr.onload = () => resolve(`HTTP ${xhr.status}`);
        xhr.onerror = () => resolve('CORS failure');
        xhr.send();
      }));
      const actual = seen.pop();
      console.log(`routing=${intercept}: ${result}; target Origin=${actual === origin ? 'page origin' : actual}`);
      await context.close();
    }
  } finally {
    await browser.close();
    source.close();
    target.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
