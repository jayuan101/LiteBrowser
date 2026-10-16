// Start (first call) / read (second call) a once-a-second log of YouTube's playback clock vs wall time.
(() => {
  if (!window.__liteClock) {
    window.__liteClock = [];
    const t0 = performance.now();
    setInterval(() => {
      const v = document.querySelector('#movie_player video');
      const p = document.getElementById('movie_player');
      if (v) window.__liteClock.push([Math.round((performance.now() - t0) / 1000), +v.currentTime.toFixed(1), v.playbackRate,
        v.paused ? 'paused' : 'playing', /ad-showing/.test(p ? p.className : '') ? 'AD' : '']);
    }, 1000);
    return 'started';
  }
  return JSON.stringify(window.__liteClock);
})()
