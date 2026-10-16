// Counts ad leftovers a person could actually see on the current page (used by drive.py js: steps).
(() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 30 && r.height > 20 && s.display !== 'none' && s.visibility !== 'hidden' && +s.opacity > 0.05;
  };
  const frames = [...document.querySelectorAll('iframe')].filter(vis)
    .map((f) => (f.id || f.name || f.src || 'about:blank').slice(0, 70));
  const slots = [...document.querySelectorAll('[id*="google_ads"],[id^="div-gpt-ad"],[class*="ad-slot"],[class*="ad_slot"],' +
    '[class*="adslot"],[data-ad-slot],[class*="ad-container"],[class*="AdContainer"],[class*="taboola"],[id*="taboola"],' +
    '[class*="outbrain"],[class*="dianomi"],ins.adsbygoogle')].filter(vis).length;
  const labels = [...document.querySelectorAll('body *')].filter((e) => e.childElementCount === 0 &&
    /^(ad|ads|advertisement|sponsored|sponsored content|paid content|paid partner content|promoted)$/i.test((e.textContent || '').trim()) &&
    vis(e)).length;
  const videos = [...document.querySelectorAll('video')].map((v) => ({paused: v.paused, t: Math.round(v.currentTime), src: (v.currentSrc || '').slice(0, 50)}));
  return JSON.stringify({visibleIframes: frames, adSlotsVisible: slots, adLabelsVisible: labels, videos});
})()
