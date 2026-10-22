// Pure helpers for time formatting and parsing (no DOM), shared by the player and the tests.

// Floating-point guard: a frame at 17.0 s may be reported as 16.99999 s and must still show 0:17.
const EPSILON = 0.001;

const pad2 = (n) => String(n).padStart(2, '0');

/** Formats seconds as m:ss (or h:mm:ss), optionally with tenths: m:ss.f. Always rounds down. */
export function formatTime(seconds, withTenths = false) {
  const safe = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
  const tenthsTotal = Math.floor((safe + EPSILON) * 10);
  const whole = Math.floor(tenthsTotal / 10);
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  const base = h > 0 ? `${h}:${pad2(m)}:${pad2(s)}` : `${m}:${pad2(s)}`;
  return withTenths ? `${base}.${tenthsTotal % 10}` : base;
}

// m:ss, h:mm:ss, optionally .f; not part of a longer number or time (e.g. 12:30:45:10).
const TIME_RE = /(?<![\d:.])(?:(\d{1,2}):)?(\d{1,2}):([0-5]\d)(?:\.(\d))?(?![\d:])/g;

/**
 * Splits comment text into plain-text and time segments.
 * Returns [{ type: 'text', text }, { type: 'time', text, seconds }].
 */
export function splitTimes(text) {
  const parts = [];
  let last = 0;
  for (const match of text.matchAll(TIME_RE)) {
    const [whole, h, m, s, f] = match;
    if (h !== undefined && Number(m) > 59) continue; // "1:75:00" is not a time
    if (match.index > last) parts.push({ type: 'text', text: text.slice(last, match.index) });
    const seconds = Number(h || 0) * 3600 + Number(m) * 60 + Number(s) + (f ? Number(f) / 10 : 0);
    parts.push({ type: 'time', text: whole, seconds });
    last = match.index + whole.length;
  }
  if (last < text.length) parts.push({ type: 'text', text: text.slice(last) });
  return parts;
}
