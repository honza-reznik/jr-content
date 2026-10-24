// Ověření vloženého FreeFrame; pouze Docker a vlastní projekt jrc-f5b06.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');

assert.equal(process.env.JRC_PROJECT, 'jrc-f5b06');
assert.equal(process.env.JRC_APP_PORT, '13253');
assert.equal(process.env.JRC_POSTGRES_PORT, '15503');
assert.equal(process.env.JRC_S3_PORT, '13973');
assert.equal(process.env.JRC_BB_PORT, '18073');
const origin = `http://127.0.0.1:${process.env.JRC_APP_PORT}`;
const api = `${origin}/video-api`;
const output = path.resolve('ops/test-output/F5-B06');
const screenshots = path.resolve('ops/screenshoty/F5-B06');
fs.mkdirSync(output, { recursive: true });
fs.mkdirSync(screenshots, { recursive: true });
let token;

async function request(method, route, data) {
  const response = await fetch(api + route, { method, headers: {
    'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }, body: data === undefined ? undefined : JSON.stringify(data) });
  assert(response.ok, `${method} ${route}: ${response.status}`);
  return response.status === 204 ? null : response.json();
}

async function upload(projectId) {
  const video = path.join(output, 'embedded-review.mp4');
  execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y',
    '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=25:duration=8',
    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=8',
    '-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
    '-c:a', 'aac', video]);
  const bytes = fs.readFileSync(video);
  const upload = await request('POST', '/upload/initiate', { project_id: projectId,
    asset_name: 'F5-B06 embedded review', original_filename: 'embedded-review.mp4',
    mime_type: 'video/mp4', file_size_bytes: bytes.length });
  const parts = [];
  for (let start = 0; start < bytes.length; start += upload.chunk_size_bytes) {
    const signed = await request('POST', '/upload/presign-part', {
      s3_key: upload.s3_key, upload_id: upload.upload_id, part_number: parts.length + 1 });
    const response = await fetch(signed.presigned_url, {
      method: 'PUT', body: bytes.subarray(start, start + upload.chunk_size_bytes),
    });
    assert(response.ok);
    parts.push({ PartNumber: parts.length + 1, ETag: response.headers.get('etag') });
  }
  await request('POST', '/upload/complete', { upload_id: upload.upload_id,
    s3_key: upload.s3_key, asset_id: upload.asset_id, version_id: upload.version_id, parts });
  for (let attempt = 0; attempt < 90; attempt++) {
    const versions = await request('GET', `/assets/${upload.asset_id}/versions`);
    const version = versions.find(item => item.id === upload.version_id);
    assert.notEqual(version.processing_status, 'failed');
    if (version.processing_status === 'ready') return { ...upload, version };
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new Error('Lokální testovací video nebylo převedeno včas.');
}

async function inspectEmbed(frame, viewportWidth) {
  const video = frame.locator('video').first();
  await video.waitFor({ state: 'visible' });
  await video.evaluate(element => { element.muted = true; });
  await frame.getByRole('button', { name: 'Play', exact: true }).first().click();
  await frame.locator('video').first().evaluate(element => element.play());
  await video.evaluate(element => new Promise(resolve => {
    if (element.currentTime > 0.5) return resolve();
    const timer = setTimeout(resolve, 3000);
    element.addEventListener('timeupdate', () => { clearTimeout(timer); resolve(); }, { once: true });
  }));
  await frame.getByTestId('video-timecode').waitFor({ state: 'visible' });
  await frame.getByText('F5-B06 timecode comment', { exact: true }).waitFor({ state: 'visible' });
  assert.equal(await frame.getByText('New Version', { exact: true }).count(), 0);
  assert.equal(await frame.getByText('Share', { exact: true }).count(), 0);
  assert.equal(await frame.getByText('Approve', { exact: true }).count(), 0);
  assert.equal(await frame.getByText('Reject', { exact: true }).count(), 0);
  assert.equal(await frame.getByText('Fields', { exact: true }).count(), 0);
  const geometry = await frame.locator('body').evaluate(() => {
    const video = document.querySelector('video').getBoundingClientRect();
    const timecode = document.querySelector('[data-testid="video-timecode"]').getBoundingClientRect();
    const comment = [...document.querySelectorAll('*')].find(el => el.childElementCount === 0 && el.textContent?.trim() === 'F5-B06 timecode comment');
    const commentBox = comment.getBoundingClientRect();
    return { width: innerWidth, scroll: document.documentElement.scrollWidth,
      video: { left: video.left, right: video.right, top: video.top, bottom: video.bottom },
      comment: { left: commentBox.left, right: commentBox.right, top: commentBox.top, bottom: commentBox.bottom },
      timecode: { left: timecode.left, right: timecode.right },
      header: Boolean(document.querySelector('header')) };
  });
  assert(geometry.scroll <= geometry.width, JSON.stringify(geometry));
  assert(geometry.timecode.left >= 0 && geometry.timecode.right <= geometry.width, JSON.stringify(geometry));
  assert.equal(geometry.header, false, 'Vložený přehrávač nemá navigační hlavičku');
  if (viewportWidth >= 768) assert(geometry.comment.left >= geometry.video.right - 1, JSON.stringify(geometry));
  return geometry;
}

async function main() {
  await request('GET', '/health');
  const credentials = await request('POST', '/auth/login', {
    email: process.env.JRC_FF_SYNC_EMAIL, password: process.env.JRC_FF_SYNC_PASSWORD,
  });
  token = credentials.access_token;
  const project = await request('POST', '/projects', { name: 'F5-B06 embed test' });
  const asset = await upload(project.id);
  await request('POST', `/assets/${asset.asset_id}/comments`, {
    version_id: asset.version_id, timecode_start: 2.5, body: 'F5-B06 timecode comment',
  });

  const browser = await chromium.launch({ headless: true });
  const report = [];
  try {
    for (const [name, viewport] of [
      ['desktop-1440', { width: 1440, height: 900 }],
      ['mobile-390', { width: 390, height: 844 }],
    ]) {
      const context = await browser.newContext({ viewport });
      const foreign = [];
      await context.route('**/*', route => {
        if (route.request().frame().parentFrame() === null && route.request().resourceType() === 'script')
          return route.abort();
        const requestOrigin = new URL(route.request().url()).origin;
        if (requestOrigin !== origin && requestOrigin !== `http://127.0.0.1:${process.env.JRC_S3_PORT}`) {
          foreign.push(route.request().url());
          return route.abort();
        }
        return route.continue();
      });
      await context.addCookies([{ name: 'ff_access_token', value: token, url: origin }]);
      await context.addInitScript(value => {
        localStorage.setItem('ff_access_token', value.access);
        localStorage.setItem('ff_refresh_token', value.refresh);
      }, { access: credentials.access_token, refresh: credentials.refresh_token });
      const page = await context.newPage();
      page.setDefaultTimeout(45000);
      const pageErrors = [];
      page.on('pageerror', error => pageErrors.push(error.message));
      // Keep the same-origin server HTML as host, while blocking its scripts
      // so hydration cannot replace the iframe after it is installed.
      await page.goto(`${origin}/video/projects`);
      await page.setContent(`<body style="margin:0;width:100vw;height:100vh;overflow:hidden"><iframe title="FreeFrame embedded review" src="/video/projects/${project.id}/assets/${asset.asset_id}?embed=1" style="display:block;width:100%;height:100%;border:0"></iframe></body>`);
      const frame = page.frameLocator('iframe[title="FreeFrame embedded review"]');
      let geometry;
      try {
        geometry = await inspectEmbed(frame, viewport.width);
      } catch (error) {
        console.log('Iframe diagnostics:', JSON.stringify({
          src: await page.locator('iframe').getAttribute('src'),
          errors: pageErrors,
        }));
        throw error;
      }
      await page.screenshot({ path: path.join(screenshots, `${name}.png`) });
      if (viewport.width === 1440) {
        const frameHref = await page.locator('iframe').getAttribute('src');
        assert(frameHref.includes('embed=1'));
        assert.equal(new URL(frameHref, origin).origin, origin, 'Iframe je stejného původu');
      }
      if (viewport.width === 390) {
        await page.setViewportSize({ width: 844, height: 390 });
        await frame.getByText('F5-B06 timecode comment', { exact: true }).waitFor({ state: 'visible' });
        const landscape = await inspectEmbed(frame, 844);
        assert(landscape.comment.left >= landscape.video.right - 1, JSON.stringify(landscape));
        await page.screenshot({ path: path.join(screenshots, 'mobile-landscape-844.png') });
        report.push({ viewport: 'mobile-landscape-844', geometry: landscape, result: 'OK' });
        console.log('OK mobile-landscape-844: komentáře vedle přehrávače po otočení telefonu.');
      }
      assert.deepEqual(foreign, []);
      report.push({ viewport: name, geometry, result: 'OK' });
      console.log(`OK ${name}: vložená stránka, přehrávač, časový štítek, komentář, bez navigace a vodorovného posunu.`);
      await context.close();
    }

    // Exercise the actual anonymous iframe -> login -> validated return path.
    const loginContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
    // The host route redirects to login and hydrates after setContent; without
    // blocking only host scripts, hydration replaces the iframe fixture.
    await loginContext.route('**/*', route => {
      if (route.request().frame().parentFrame() === null && route.request().resourceType() === 'script')
        return route.abort();
      return route.continue();
    });
    const loginPage = await loginContext.newPage();
    loginPage.setDefaultTimeout(45000);
    await loginPage.goto(`${origin}/video/projects`);
    await loginPage.setContent(`<body style="margin:0;width:100vw;height:100vh"><iframe title="Anonymous review" src="/video/projects/${project.id}/assets/${asset.asset_id}?embed=1" style="width:100%;height:100vh;border:0"></iframe></body>`);
    const loginFrame = loginPage.frameLocator('iframe[title="Anonymous review"]');
    await loginFrame.getByLabel('Email address').waitFor({ state: 'visible' });
    const iframeFrame = () => loginPage.frames().find(frame => frame.parentFrame() === loginPage.mainFrame());
    const currentLoginUrl = iframeFrame()?.url();
    assert(currentLoginUrl.includes('/video/login?'));
    assert.equal(new URL(currentLoginUrl).searchParams.get('from'), `/projects/${project.id}/assets/${asset.asset_id}?embed=1`);
    if (await loginFrame.getByLabel('Password').count() === 0) {
      await loginFrame.getByRole('button', { name: /password instead/i }).click();
    }
    await loginFrame.getByLabel('Email address').fill(process.env.JRC_FF_SYNC_EMAIL);
    await loginFrame.getByLabel('Password').fill(process.env.JRC_FF_SYNC_PASSWORD);
    await loginFrame.getByRole('button', { name: 'Sign in', exact: true }).click();
    await loginFrame.locator('video').first().waitFor({ state: 'visible' });
    const returnedUrl = iframeFrame()?.url();
    assert(returnedUrl.includes('embed=1'), `Login návrat zachoval embed: ${returnedUrl}`);
    assert.equal(new URL(returnedUrl, origin).origin, origin);
    await loginPage.screenshot({ path: path.join(screenshots, 'anonymous-login-return.png') });
    report.push({ viewport: 'anonymous-login-return', result: 'OK' });
    console.log('OK anonymous-login-return: iframe bez relace se přihlásí a vrátí na ověřenou embed cestu.');
    await loginContext.close();
    fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error.message.replace(/v1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, '[redacted]'));
  process.exitCode = 1;
});
