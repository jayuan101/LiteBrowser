// What sits between the eye and CNN's playing video: each layer's size, background and text.
(() => {
  const v = [...document.querySelectorAll('video')].find((x) => (x.currentSrc || '').startsWith('blob:'));
  if (!v) return 'no blob video';
  const r = v.getBoundingClientRect();
  const layers = document.elementsFromPoint(r.left + r.width / 2, r.top + r.height / 2).slice(0, 10);
  return JSON.stringify(layers.map((e) => {
    const s = getComputedStyle(e), b = e.getBoundingClientRect();
    return {el: e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (typeof e.className === 'string' && e.className ? '.' + e.className.trim().split(/\s+/).join('.') : ''),
      box: [Math.round(b.width), Math.round(b.height)], bg: s.backgroundColor, bgImage: s.backgroundImage.slice(0, 60),
      opacity: s.opacity, z: s.zIndex, text: (e.innerText || '').trim().slice(0, 60), video: e === v};
  }));
})()
