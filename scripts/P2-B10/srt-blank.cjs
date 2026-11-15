// Regrese SRT s více prázdnými řádky. Spouštět jen přes run-browser.sh.
process.env.LANG = process.env.LC_ALL = 'C.UTF-8';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
assert.equal(process.env.JRC_PROJECT, 'jrc-p2b10');
const fixture = JSON.parse(fs.readFileSync('ops/test-output/P2-B10/fixture.json'));
const base = 'http://127.0.0.1:13910/jrc';
const expected = Number(process.argv[2] || 200);
const text = '1\n00:00:00,000 --> 00:00:01,500\nPrvní titulek\n\n\n\n2\n00:00:01,500 --> 00:00:03,000\nDruhý titulek\n';
(async () => {
  const browser = await chromium.launch({headless:true,args:['--disable-dev-shm-usage','--js-flags=--max-old-space-size=2048']});
  try {
    for (const width of [1440,390]) {
      const context = await browser.newContext({viewport:{width,height:width===1440?900:844}});
      await context.route(/^https?:\/\/(?!127\.0\.0\.1:13910(?:\/|$))/, r => r.abort());
      await context.addCookies([{...fixture.owner.cookie,url:base+'/'}]);
      const page = await context.newPage(); page.setDefaultTimeout(20000);
      const errors=[]; page.on('pageerror',e=>errors.push(e.message));
      await page.goto(`${base}/workspace/${fixture.workspace}/content/${fixture.post}/?tab=video`);
      const row=page.locator('[data-video-channel]:visible [data-version]').first();
      await row.locator('details[open]').waitFor();
      const pending=page.waitForResponse(r=>r.url().endsWith('/transcript/') && r.request().method()==='POST');
      await row.locator('[data-transcript-file]').setInputFiles({name:'Prázdné řádky.srt',mimeType:'text/plain',buffer:Buffer.from(text)});
      assert.equal((await pending).status(),expected);
      if (expected===200) {
        await row.locator('[data-transcript-preview]').click();
        await row.locator('[data-transcript-text]:visible').waitFor();
        assert.equal(await row.locator('[data-transcript-text]').textContent(),text);
      } else await row.locator('[data-transcript-error]:visible').waitFor();
      await row.locator('[data-transcript]').scrollIntoViewIfNeeded();
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      await page.screenshot({path:`ops/screenshoty/sprint-feedback/P2-B10/srt-blank-${expected}-${width}.png`});
      assert.deepEqual(errors,[]);
      await context.close();
    }
    console.log(`OK: SRT blank lines HTTP ${expected}, desktop and mobile`);
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
