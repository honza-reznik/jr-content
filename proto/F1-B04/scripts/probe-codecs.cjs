// Zjistí, které formáty videa umí Chromium z obrazu jrc-dev přehrát.
const { chromium } = require('playwright');
(async () => {
  for (const headless of [true]) {
    const b = await chromium.launch({ headless });
    const p = await b.newPage();
    const r = await p.evaluate(() => {
      const v = document.createElement('video');
      const t = {
        h264_high: 'video/mp4; codecs="avc1.640028"',
        h264_main_aac: 'video/mp4; codecs="avc1.4d401f, mp4a.40.2"',
        aac: 'audio/mp4; codecs="mp4a.40.2"',
        vp9_opus: 'video/webm; codecs="vp9, opus"',
        av1: 'video/mp4; codecs="av01.0.08M.08"',
        hls: 'application/vnd.apple.mpegurl',
      };
      const o = {};
      for (const [k, s] of Object.entries(t)) o[k] = v.canPlayType(s) + ' / MSE:' + (window.MediaSource ? MediaSource.isTypeSupported(s) : 'n/a');
      o.rvfc = 'requestVideoFrameCallback' in HTMLVideoElement.prototype;
      return o;
    });
    console.log(b.version(), headless, r);
    await b.close();
  }
})();
