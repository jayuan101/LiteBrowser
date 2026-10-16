// YouTube: is an ad playing or queued, and are ad slots visible? (used by drive.py js: steps)
(() => {
  const p = document.getElementById('movie_player');
  const cls = p ? p.className : '';
  const ipr = window.ytInitialPlayerResponse;
  const v = document.querySelector('#movie_player video');
  const shown = (e) => e.getBoundingClientRect().height > 0 && getComputedStyle(e).display !== 'none';
  return JSON.stringify({
    adShowing: /\bad-showing\b|\bad-interrupting\b/.test(cls),
    adPlacements: !ipr ? 'no player response' : !('adPlacements' in ipr) ? 'absent' : ipr.adPlacements ? ipr.adPlacements.length : 'removed',
    playerAds: ipr ? !!ipr.playerAds : null,
    visibleAdSlots: [...document.querySelectorAll('ytd-ad-slot-renderer, ytd-in-feed-ad-layout-renderer, ytd-promoted-sparkles-web-renderer, ' +
      '#masthead-ad, ytd-display-ad-renderer, ytd-companion-slot-renderer, ytd-action-companion-ad-renderer, ytd-banner-promo-renderer, ' +
      '.ytp-ad-module .ytp-ad-player-overlay, .ytp-ad-text, ytd-player-legacy-desktop-watch-ads-renderer')].filter(shown).length,
    video: v ? {paused: v.paused, t: Math.round(v.currentTime), duration: Math.round(v.duration || 0)} : null,
    adblockWarning: !!document.querySelector('ytd-enforcement-message-view-model'),
    title: document.title.slice(0, 60),
  });
})()
