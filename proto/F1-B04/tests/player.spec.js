// End-to-end tests of the F1-B04 player prototype.
//
// How sync is checked: the test video has a black 120x120 px square moving left to right,
// x = 2 px per frame at 30 fps (see scripts/make-video.sh). The test copies the frame that is
// on screen into a canvas, finds the square and computes the time of that frame. That time
// must agree with the overlay. So the check does not trust the player's own clock.
import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { formatTime, splitTimes } from '../public/timecode.js';

const FPS = 30;
const FRAME = 1 / FPS;
const SHOTS = new URL('../screenshoty/', import.meta.url).pathname;
mkdirSync(SHOTS, { recursive: true });

const shot = (page, name, opts = {}) =>
  page.screenshot({ path: `${SHOTS}${test.info().project.name}-${name}.png`, ...opts });

/**
 * Reads the frame on screen and the overlay at the same moment.
 * mode 'next' waits for the next presented frame (playback, after seek);
 * mode 'now' reads the current frame (paused).
 */
async function sample(page, mode = 'next') {
  return page.evaluate(async (mode) => {
    const video = document.getElementById('video');
    const overlay = document.getElementById('timecode');
    const W = video.videoWidth;
    const canvas = document.createElement('canvas');
    canvas.width = W;
    canvas.height = 1;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    const read = () => {
      // one pixel row through the middle of the square (y = 60..180 in the full-size frame)
      ctx.drawImage(video, 0, 120, W, 1, 0, 0, W, 1);
      const px = ctx.getImageData(0, 0, W, 1).data;
      let x = -1;
      for (let i = 0; i < W; i++) {
        const [r, g, b] = [px[i * 4], px[i * 4 + 1], px[i * 4 + 2]];
        if (r < 40 && g < 40 && b < 40) {
          x = i;
          break;
        }
      }
      return {
        // x = (W - 120) * t / 30  =>  t = 30 * x / (W - 120), rounded to a whole frame
        frameTime: x < 0 ? null : Math.round((30 * 30 * x) / (W - 120)) / 30,
        overlayText: overlay.textContent,
        overlaySeconds: Number(overlay.dataset.seconds),
        currentTime: video.currentTime,
        paused: video.paused,
      };
    };
    if (mode === 'now') return read();
    return new Promise((resolve) => video.requestVideoFrameCallback(() => resolve(read())));
  }, mode);
}

/** Overlay must show the time of the frame that is actually on screen (±1 frame). */
function expectInSync(s, withTenths = false) {
  expect(s.frameTime, 'square found in the frame').not.toBeNull();
  expect(Math.abs(s.overlaySeconds - s.frameTime)).toBeLessThanOrEqual(FRAME + 0.002);
  const allowed = [s.frameTime - FRAME, s.frameTime, s.frameTime + FRAME].map((t) => formatTime(t, withTenths));
  expect(allowed).toContain(s.overlayText);
}

async function open(page, path = '/') {
  await page.goto(path);
  await page.waitForFunction(() => document.getElementById('video').readyState >= 2);
  await page.getByTestId('mute').click();
}

/** Overlay lies on the picture itself (not on black bars) and the picture keeps its aspect ratio. */
async function expectOverlayOnPicture(page) {
  const r = await page.evaluate(() => {
    const box = (id) => document.getElementById(id).getBoundingClientRect().toJSON();
    const v = document.getElementById('video');
    return { frame: box('frame'), overlay: box('timecode'), ar: v.videoWidth / v.videoHeight, vw: innerWidth, vh: innerHeight };
  });
  const { frame, overlay } = r;
  expect(frame.width / frame.height).toBeCloseTo(r.ar, 1);
  expect(overlay.left).toBeGreaterThanOrEqual(frame.left);
  expect(overlay.top).toBeGreaterThanOrEqual(frame.top);
  expect(overlay.right).toBeLessThanOrEqual(frame.right);
  expect(overlay.bottom).toBeLessThanOrEqual(frame.bottom);
  expect(frame.left).toBeGreaterThanOrEqual(-0.5);
  expect(frame.right).toBeLessThanOrEqual(r.vw + 0.5);
  return r;
}

async function seekProgrammatically(page, t) {
  // Same as dragging the seek bar to an exact spot (exact clicks on the bar are not repeatable).
  await page.evaluate(
    (t) =>
      new Promise((resolve) => {
        const v = document.getElementById('video');
        v.addEventListener('seeked', () => resolve(), { once: true });
        v.currentTime = t;
      }),
    t,
  );
}

// ---------- Unit tests of the helpers (no browser page needed) ----------

test.describe('pomocné funkce', () => {
  test('formatTime', () => {
    expect(formatTime(0)).toBe('0:00');
    expect(formatTime(17)).toBe('0:17');
    expect(formatTime(16.99995)).toBe('0:17'); // floating-point noise
    expect(formatTime(17.98)).toBe('0:17'); // rounds down like every player
    expect(formatTime(17.25, true)).toBe('0:17.2');
    expect(formatTime(75.5, true)).toBe('1:15.5');
    expect(formatTime(3725)).toBe('1:02:05');
    expect(formatTime(NaN)).toBe('0:00');
  });

  test('splitTimes', () => {
    expect(splitTimes('0:17 zkrátit')).toEqual([
      { type: 'time', text: '0:17', seconds: 17 },
      { type: 'text', text: ' zkrátit' },
    ]);
    const p = splitTimes('od 0:12.5 do 1:02:03, ne 12:345 ani 0:75');
    expect(p.filter((x) => x.type === 'time').map((x) => x.seconds)).toEqual([12.5, 3723]);
    expect(splitTimes('bez času')).toEqual([{ type: 'text', text: 'bez času' }]);
  });
});

// ---------- End-to-end ----------

test('překryv je synchronní při přehrávání a pauze', async ({ page }) => {
  await open(page);
  await expect(page.getByTestId('timecode')).toHaveText('0:00');
  await shot(page, '01-start');

  await page.getByTestId('play').click();
  await page.waitForFunction(() => document.getElementById('video').currentTime > 1.2);
  const texts = new Set();
  for (let i = 0; i < 8; i++) {
    const s = await sample(page);
    expectInSync(s);
    texts.add(s.overlayText);
    await page.waitForTimeout(150);
  }
  expect(texts.size, 'overlay moves while playing').toBeGreaterThan(1);

  // m:ss.f mode while playing
  await page.getByTestId('tenths').check();
  for (let i = 0; i < 4; i++) expectInSync(await sample(page), true);
  await shot(page, '02-prehravani-desetiny');

  await page.getByTestId('play').click(); // pause
  await expect.poll(() => page.evaluate(() => document.getElementById('video').paused)).toBe(true);
  const a = await sample(page, 'now');
  expectInSync(a, true);
  await page.waitForTimeout(600);
  const b = await sample(page, 'now');
  expect(b.overlayText).toBe(a.overlayText); // stands still in pause
  expect(b.frameTime).toBe(a.frameTime);
});

test('posun: táhlo, klávesy i přesný skok', async ({ page }) => {
  await open(page);
  const seek = page.getByTestId('seek');
  const box = await seek.boundingBox();
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height / 2);
  await expect.poll(async () => (await sample(page, 'now')).currentTime).toBeGreaterThan(10);
  await page.waitForTimeout(300);
  let s = await sample(page, 'now');
  expect(s.currentTime).toBeGreaterThan(10);
  expect(s.currentTime).toBeLessThan(20);
  expectInSync(s);

  // keyboard: arrow right = +1 s (focus outside of the text field)
  await page.locator('h1').click();
  const before = s.currentTime;
  await page.keyboard.press('ArrowRight');
  await page.waitForTimeout(300);
  s = await sample(page, 'now');
  expect(s.currentTime).toBeCloseTo(before + 1, 1);
  expectInSync(s);

  // exact seek to several spots, including the end
  for (const t of [0, 7, 17, 29.9]) {
    await seekProgrammatically(page, t);
    await page.waitForTimeout(200);
    s = await sample(page, 'now');
    expectInSync(s);
    expect(s.overlayText).toBe(formatTime(t));
  }
});

test('celá obrazovka: překryv zůstává vidět a synchronní', async ({ page }) => {
  await open(page);
  await seekProgrammatically(page, 5);
  await page.getByTestId('fullscreen').click();
  await expect.poll(() => page.evaluate(() => document.fullscreenElement?.id)).toBe('player');
  await expect(page.getByTestId('fullscreen')).toHaveText('Zavřít celou obrazovku');

  const overlay = page.getByTestId('timecode');
  await expect(overlay).toBeVisible();
  const vp = page.viewportSize();
  const ob = await overlay.boundingBox();
  expect(ob.x).toBeGreaterThanOrEqual(0);
  expect(ob.y).toBeGreaterThanOrEqual(0);
  expect(ob.x + ob.width).toBeLessThanOrEqual(vp.width);
  expect(ob.y + ob.height).toBeLessThanOrEqual(vp.height);
  await expectOverlayOnPicture(page);

  // overlay is on top of the video (not hidden behind it)
  // (overlay has pointer-events: none, which elementFromPoint skips, so switch it on briefly)
  const topId = await page.evaluate(() => {
    const el = document.getElementById('timecode');
    el.style.pointerEvents = 'auto';
    const r = el.getBoundingClientRect();
    const id = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)?.id;
    el.style.pointerEvents = '';
    return id;
  });
  expect(topId).toBe('timecode');

  await page.getByTestId('play').click();
  await page.waitForFunction(() => document.getElementById('video').currentTime > 6);
  for (let i = 0; i < 4; i++) expectInSync(await sample(page));
  await page.getByTestId('play').click();
  await page.waitForTimeout(200);
  expectInSync(await sample(page, 'now'));
  await shot(page, '03-cela-obrazovka');

  // seek in fullscreen with the keyboard
  await page.keyboard.press('ArrowLeft');
  await page.waitForTimeout(300);
  expectInSync(await sample(page, 'now'));

  await page.getByTestId('fullscreen').click();
  await expect.poll(() => page.evaluate(() => document.fullscreenElement)).toBeNull();
  await expect(page.getByTestId('fullscreen')).toHaveText('Celá obrazovka');
});

test('celá obrazovka bez Fullscreen API (jako iPhone): náhradní režim', async ({ page }) => {
  await page.addInitScript(() => {
    delete Element.prototype.requestFullscreen;
    delete Element.prototype.webkitRequestFullscreen;
  });
  await open(page);
  await page.getByTestId('fullscreen').click();
  await expect(page.getByTestId('player')).toHaveClass(/pseudo-fs/);
  const vp = page.viewportSize();
  const pb = await page.getByTestId('player').boundingBox();
  expect(pb.width).toBeCloseTo(vp.width, 0);
  expect(pb.height).toBeCloseTo(vp.height, 0);
  await expect(page.getByTestId('timecode')).toBeVisible();
  await seekProgrammatically(page, 9);
  await page.waitForTimeout(200);
  expectInSync(await sample(page, 'now'));
  await shot(page, '04-nahradni-cela-obrazovka');
  await page.getByTestId('fullscreen').click();
  await expect(page.getByTestId('player')).not.toHaveClass(/pseudo-fs/);
});

test('komentář: vložit čas a klik na čas skočí na místo', async ({ page }) => {
  await open(page);
  await seekProgrammatically(page, 17.4);

  const field = page.getByTestId('comment-text');
  await page.getByTestId('insert-time').click();
  await expect(field).toHaveValue('0:17 ');
  await expect(field).toBeFocused();
  await page.keyboard.type('zkrátit');
  await page.getByTestId('add-comment').click();
  await expect(field).toHaveValue('');

  const comment = page.getByTestId('comment-list').locator('li').last();
  await expect(comment).toHaveText('0:17 zkrátit');
  const link = comment.getByRole('button', { name: '0:17' });
  await expect(link).toBeVisible();

  // inserting into the middle of a text keeps the rest and adds spaces
  await seekProgrammatically(page, 3);
  await field.fill('od do');
  await field.evaluate((el) => el.setSelectionRange(3, 3));
  await page.getByTestId('insert-time').click();
  await expect(field).toHaveValue('od 0:03 do');

  // jump from the comment
  await link.click();
  await expect.poll(() => page.evaluate(() => document.getElementById('video').currentTime)).toBeCloseTo(17, 1);
  await page.waitForTimeout(300);
  const s = await sample(page, 'now');
  expect(s.overlayText).toBe('0:17');
  expectInSync(s);

  // time with tenths in the demo comment
  await page.getByTestId('comment-list').getByRole('button', { name: '0:12.5' }).click();
  await expect.poll(() => page.evaluate(() => document.getElementById('video').currentTime)).toBeCloseTo(12.5, 1);
  await page.getByTestId('tenths').check();
  await expect(page.getByTestId('timecode')).toHaveText('0:12.5');

  // comment text is not interpreted as HTML
  await field.fill('<img src=x onerror="window.__xss=1"> 0:01');
  await page.getByTestId('add-comment').click();
  await expect(page.getByTestId('comment-list').locator('li').last()).toContainText('<img src=x');
  expect(await page.evaluate(() => window.__xss)).toBeUndefined();

  await page.locator('h1').click();
  await seekProgrammatically(page, 17);
  await shot(page, '05-komentare', { fullPage: true });
});

test('rozložení: bez vodorovného posouvání, ovládání v šířce okna', async ({ page }) => {
  await open(page);
  const vp = page.viewportSize();
  const scrollW = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(scrollW).toBeLessThanOrEqual(vp.width);
  for (const id of ['player', 'play', 'seek', 'clock', 'mute', 'tenths', 'fullscreen', 'timecode', 'insert-time', 'add-comment']) {
    const b = await page.getByTestId(id).boundingBox();
    expect(b, id).not.toBeNull();
    expect(b.x, id).toBeGreaterThanOrEqual(0);
    expect(b.x + b.width, id).toBeLessThanOrEqual(vp.width + 0.5);
  }
  // overlay readable: at least 15 px font
  const fontPx = await page.getByTestId('timecode').evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
  expect(fontPx).toBeGreaterThanOrEqual(15);
  await expectOverlayOnPicture(page);
  await seekProgrammatically(page, 12);
  await shot(page, '06-rozlozeni', { fullPage: true });
});

test('video na výšku (Reel 9:16): překryv na obraze, synchronní, i na celé obrazovce', async ({ page }) => {
  await open(page, '/?video=reel');
  expect(await page.evaluate(() => document.getElementById('video').videoHeight)).toBe(1920);
  const r = await expectOverlayOnPicture(page);
  // a vertical video must leave room for the controls: picture + controls fit in the window together
  expect(r.frame.height).toBeLessThanOrEqual(r.vh * 0.75 + 1);
  const playerBox = await page.getByTestId('player').boundingBox();
  expect(playerBox.height).toBeLessThanOrEqual(r.vh);

  await page.getByTestId('play').click();
  await page.waitForFunction(() => document.getElementById('video').currentTime > 1);
  for (let i = 0; i < 4; i++) expectInSync(await sample(page));
  await page.getByTestId('play').click();
  await seekProgrammatically(page, 21);
  await page.waitForTimeout(200);
  expectInSync(await sample(page, 'now'));
  await shot(page, '07-reel');

  await page.getByTestId('fullscreen').click();
  await expect.poll(() => page.evaluate(() => document.fullscreenElement?.id)).toBe('player');
  await page.waitForTimeout(200);
  await expectOverlayOnPicture(page);
  await shot(page, '08-reel-cela-obrazovka');
  await page.getByTestId('fullscreen').click();
});
