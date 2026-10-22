// F1-B04 prototype: HTML5 <video> with a timecode overlay (not burned into the file)
// and comments with clickable times. No framework, no dependencies.
import { formatTime, splitTimes } from './timecode.js';

const $ = (id) => document.getElementById(id);
const player = $('player');
const stage = $('stage');
const video = $('video');
const overlay = $('timecode');
const playBtn = $('play');
const seek = $('seek');
const clock = $('clock');
const muteBtn = $('mute');
const tenthsBox = $('tenths');
const fsBtn = $('fullscreen');
const form = $('comment-form');
const textarea = $('comment-text');
const insertBtn = $('insert-time');
const list = $('comment-list');

let withTenths = tenthsBox.checked;
let seekDragging = false;

// Prototype only: choose the test video by URL (?video=reel). In MVP1 it comes from the database.
const VIDEOS = { wide: '/media/test-1080p.mp4', reel: '/media/test-reel-1080x1920.mp4' };
const choice = new URLSearchParams(location.search).get('video') === 'reel' ? 'reel' : 'wide';
video.src = VIDEOS[choice];
document.querySelector(`[data-testid="pick-${choice}"]`)?.setAttribute('aria-current', 'page');

// ---------- Overlay ----------

/** Shows time t (seconds) in the overlay, the clock and the seek bar. */
function render(t) {
  const text = formatTime(t, withTenths);
  if (overlay.textContent !== text) overlay.textContent = text;
  overlay.dataset.seconds = t.toFixed(3); // for tests and debugging
  const clockText = `${formatTime(t)} / ${formatTime(video.duration || 0)}`;
  if (clock.textContent !== clockText) clock.textContent = clockText;
  if (!seekDragging) seek.value = String(t);
}

// requestVideoFrameCallback fires once per frame actually shown on screen and tells its exact
// media time, so the overlay matches the picture. Fallback: requestAnimationFrame while playing.
if ('requestVideoFrameCallback' in HTMLVideoElement.prototype) {
  const onFrame = (_now, meta) => {
    render(meta.mediaTime);
    video.requestVideoFrameCallback(onFrame);
  };
  video.requestVideoFrameCallback(onFrame);
} else {
  let raf = 0;
  const loop = () => {
    render(video.currentTime);
    raf = requestAnimationFrame(loop);
  };
  video.addEventListener('play', () => {
    cancelAnimationFrame(raf);
    loop();
  });
  video.addEventListener('pause', () => cancelAnimationFrame(raf));
}
// While playing, frames drive the overlay. Otherwise (pause, seek) show the target time at once.
for (const type of ['loadedmetadata', 'seeking', 'seeked', 'pause', 'ended']) {
  video.addEventListener(type, () => render(video.currentTime));
}

// ---------- Controls ----------

function togglePlay() {
  if (video.paused || video.ended) video.play();
  else video.pause();
}

function seekTo(t) {
  const max = Number.isFinite(video.duration) ? video.duration : t;
  video.currentTime = Math.min(Math.max(t, 0), max);
  render(video.currentTime);
}

video.addEventListener('loadedmetadata', () => {
  seek.max = String(video.duration);
  // The frame (picture + overlay) takes the video's aspect ratio, so the overlay always sits
  // on the picture itself, also for vertical Reels and in fullscreen (no black bars under it).
  player.style.setProperty('--ar', String(video.videoWidth / video.videoHeight));
});
video.addEventListener('play', () => {
  playBtn.textContent = '❚❚';
  playBtn.setAttribute('aria-label', 'Pozastavit');
});
video.addEventListener('pause', () => {
  playBtn.textContent = '▶';
  playBtn.setAttribute('aria-label', 'Přehrát');
});

playBtn.addEventListener('click', togglePlay);
stage.addEventListener('click', togglePlay);

seek.addEventListener('pointerdown', () => (seekDragging = true));
seek.addEventListener('input', () => seekTo(Number(seek.value)));
seek.addEventListener('change', () => (seekDragging = false));
seek.addEventListener('pointerup', () => (seekDragging = false));

muteBtn.addEventListener('click', () => {
  video.muted = !video.muted;
  muteBtn.textContent = video.muted ? 'Zapnout zvuk' : 'Ztlumit';
  muteBtn.setAttribute('aria-pressed', String(video.muted));
});

tenthsBox.addEventListener('change', () => {
  withTenths = tenthsBox.checked;
  render(video.currentTime);
});

// ---------- Fullscreen ----------
// The whole player (video + overlay + controls) goes fullscreen, not just the <video> element:
// a fullscreen <video> would hide everything layered over it, including the overlay.
// iPhone Safari has no fullscreen for other elements than <video>, so there we fall back
// to "pseudo-fullscreen" (the player covers the window via CSS).

const fsElement = () => document.fullscreenElement || document.webkitFullscreenElement || null;
const isFullscreen = () => fsElement() === player || player.classList.contains('pseudo-fs');

async function toggleFullscreen() {
  if (isFullscreen()) {
    if (player.classList.contains('pseudo-fs')) {
      player.classList.remove('pseudo-fs');
      document.documentElement.classList.remove('no-scroll');
    } else if (document.exitFullscreen) await document.exitFullscreen();
    else document.webkitExitFullscreen?.();
  } else if (player.requestFullscreen) {
    await player.requestFullscreen({ navigationUI: 'hide' });
    // Android: turn a landscape video sideways on a phone held upright (not supported everywhere).
    if (video.videoWidth > video.videoHeight && screen.orientation?.lock) {
      screen.orientation.lock('landscape').catch(() => {});
    }
  } else if (player.webkitRequestFullscreen) {
    player.webkitRequestFullscreen();
  } else {
    player.classList.add('pseudo-fs');
    document.documentElement.classList.add('no-scroll');
  }
  updateFsButton();
}

function updateFsButton() {
  const on = isFullscreen();
  player.classList.toggle('is-fullscreen', on);
  fsBtn.textContent = on ? 'Zavřít celou obrazovku' : 'Celá obrazovka';
}

fsBtn.addEventListener('click', () => toggleFullscreen());
document.addEventListener('fullscreenchange', updateFsButton);
document.addEventListener('webkitfullscreenchange', updateFsButton);

// ---------- Keyboard ----------

document.addEventListener('keydown', (e) => {
  const tag = e.target.tagName;
  if (tag === 'TEXTAREA' || tag === 'INPUT' || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === ' ' && tag !== 'BUTTON') {
    e.preventDefault();
    togglePlay();
  } else if (e.key === 'ArrowLeft') seekTo(video.currentTime - 1);
  else if (e.key === 'ArrowRight') seekTo(video.currentTime + 1);
  else if (e.key === 'f' || e.key === 'F') toggleFullscreen();
  else if (e.key === 'Escape' && player.classList.contains('pseudo-fs')) toggleFullscreen();
});

// ---------- Comments ----------

/** Inserts the current time at the cursor in the comment field and pauses the video. */
function insertTime() {
  video.pause();
  const stamp = formatTime(video.currentTime, withTenths);
  const { selectionStart: start, selectionEnd: end, value } = textarea;
  const before = value.slice(0, start);
  const after = value.slice(end);
  const lead = before && !/\s$/.test(before) ? ' ' : '';
  const trail = after.startsWith(' ') ? '' : ' ';
  textarea.value = `${before}${lead}${stamp}${trail}${after}`;
  const caret = (before + lead + stamp + trail).length;
  textarea.focus();
  textarea.setSelectionRange(caret, caret);
}

/** Builds the comment element. Uses text nodes only, so a comment cannot inject HTML. */
function renderComment(text) {
  const li = document.createElement('li');
  li.className = 'comment';
  for (const part of splitTimes(text)) {
    if (part.type === 'text') {
      li.append(document.createTextNode(part.text));
    } else {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'ts';
      btn.textContent = part.text;
      btn.dataset.seconds = String(part.seconds);
      btn.title = `Přejít na ${part.text}`;
      li.append(btn);
    }
  }
  return li;
}

function addComment(text) {
  const clean = text.trim();
  if (!clean) return;
  list.append(renderComment(clean));
}

list.addEventListener('click', (e) => {
  const btn = e.target.closest('button.ts');
  if (!btn) return;
  seekTo(Number(btn.dataset.seconds));
  // On a phone the player may be scrolled away while reading comments.
  const r = player.getBoundingClientRect();
  if (r.top < 0 || r.bottom > window.innerHeight) player.scrollIntoView({ block: 'start', behavior: 'smooth' });
});

insertBtn.addEventListener('click', insertTime);
form.addEventListener('submit', (e) => {
  e.preventDefault();
  addComment(textarea.value);
  textarea.value = '';
});
textarea.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) form.requestSubmit();
});

// Demo comment so the feature is visible right away (prototype only, not saved anywhere).
addComment('Ukázka: 0:05 pruhy jsou moc tmavé, od 0:12.5 do 0:20 zkrátit');

render(0);
