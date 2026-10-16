// Which element is on top of each playing/blob <video>, and is the video really rendering frames?
(() => {
  const out = [];
  for (const v of document.querySelectorAll('video')) {
    if (!(v.currentSrc || '').startsWith('blob:')) continue;
    const r = v.getBoundingClientRect();
    const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    const chain = [];
    for (let e = top; e && chain.length < 4; e = e.parentElement) {
      chain.push(e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (typeof e.className === 'string' && e.className ? '.' + e.className.split(' ').slice(0, 2).join('.') : ''));
    }
    const q = v.getVideoPlaybackQuality ? v.getVideoPlaybackQuality() : {};
    out.push({rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
      paused: v.paused, muted: v.muted, readyState: v.readyState, size: [v.videoWidth, v.videoHeight],
      frames: q.totalVideoFrames, opacity: getComputedStyle(v).opacity, visibility: getComputedStyle(v).visibility,
      onTop: chain, sameAsVideo: top === v});
  }
  return JSON.stringify(out);
})()
