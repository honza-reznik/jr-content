// Spouštět pouze v Dockeru s obrazem jrc-dev:local a --network host.
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');

(async () => {
  const state = JSON.parse(fs.readFileSync('/results/state.json', 'utf8'));
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  // Audit nesmí navazovat spojení na služby mimo tento stroj.
  await context.route('**/*', route => {
    const host = new URL(route.request().url()).hostname;
    return ['127.0.0.1', 'localhost'].includes(host) ? route.continue() : route.abort();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const shot = name => page.screenshot({ path: `/screens/${name}.png` });
  try {
    await page.goto('http://127.0.0.1:13020/login', { waitUntil: 'domcontentloaded', timeout: 120000 });
    await page.getByRole('button', { name: 'Sign in with password instead' }).click();
    await shot('01-prihlaseni');
    await page.getByLabel('Email address').fill('honza.audit@example.com');
    await page.getByLabel('Password', { exact: true }).fill(process.env.AUDIT_PASSWORD);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.waitForURL('**/projects', { timeout: 120000 });
    await page.getByText('Ella – audit F1-B02', { exact: true }).first().waitFor();
    await shot('02-projekty');
    await page.getByText('Ella – audit F1-B02', { exact: true }).first().click();
    await page.getByText('Reel pro Ellu – IG', { exact: true }).first().waitFor();
    await shot('03-projekt');
    await page.getByText('Reel pro Ellu – IG', { exact: true }).first().dblclick();
    await page.waitForURL(`**/assets/${state.asset_id}`, { timeout: 120000 });
    await page.locator('video').first().waitFor();
    await page.waitForFunction(() => document.querySelector('video')?.readyState >= 2, { timeout: 60000 });
    await page.getByRole('button', { name: 'Play', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('video')?.currentTime > 1);
    await page.getByRole('button', { name: 'Pause', exact: true }).click();
    await page.getByText('Celé video v2: zkontrolováno v auditu.', { exact: true }).waitFor();
    await shot('04-video-v2');
    await page.getByRole('button', { name: 'v2', exact: true }).click();
    await page.getByRole('menuitem', { name: /v1/ }).click();
    await page.getByText('Celé video v1: zkontrolováno v auditu.', { exact: true }).waitFor();
    assert.equal(await page.getByText('Celé video v2: zkontrolováno v auditu.', { exact: true }).count(), 0);
    await page.getByText('V čase 0:17 zkrátit – verze 1.', { exact: true }).click();
    // Klik na komentář skočí v přehrávači na jeho čas; počkat na vykreslený snímek.
    await page.waitForFunction(() => {
      const v = document.querySelector('video');
      return v && Math.abs(v.currentTime - 17) < 0.5 && !v.seeking && v.readyState >= 2;
    }, { timeout: 60000 });
    await page.waitForTimeout(1000);
    await shot('05-video-v1-komentare');
    await page.getByTitle('Compare versions', { exact: true }).click();
    await page.waitForURL(/compare=/);
    await page.waitForFunction(() => {
      // Jen viditelná videa; stránka má i skryté pomocné video pro snímky.
      const videos = [...document.querySelectorAll('video')]
        .filter(v => v.getBoundingClientRect().width > 0);
      return videos.length >= 2 && videos.every(v => v.readyState >= 2);
    }, { timeout: 60000 });
    await page.waitForTimeout(1500);
    await shot('06-porovnani');
    await page.goto(`http://127.0.0.1:13020/projects/${state.project_id}/assets/${state.asset_id}`);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('video').first().waitFor();
    await page.waitForFunction(() => document.querySelector('video')?.readyState >= 2);
    await shot('07-telefon');
    fs.writeFileSync('/results/ui.json', JSON.stringify({ result: 'passed', pageErrors: errors,
      checks: ['přihlášení heslem', 'projekty', 'otevření videa', 'přehrávání HLS',
        'v2 → v1', 'oddělené komentáře', 'porovnání dvou verzí', 'telefon 390×844'] }, null, 2));
    console.log('UI audit prošel; snímky uloženy.');
  } catch (error) {
    await page.screenshot({ path: '/results/ui-failure.png' });
    fs.writeFileSync('/results/ui-failure.txt', await page.locator('body').innerText());
    throw error;
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
