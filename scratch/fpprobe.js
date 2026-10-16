// Fingerprint probe: what a tracker would hash. Run with  drive.py --step T:js:@scratch/fpprobe.js
(() => {
  const hash = (s) => { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return (h >>> 0).toString(16); };
  const c = document.createElement('canvas');
  c.width = 220; c.height = 40;
  const ctx = c.getContext('2d');
  ctx.textBaseline = 'top'; ctx.font = '16px Arial';
  ctx.fillStyle = '#f60'; ctx.fillRect(10, 5, 80, 25);
  ctx.fillStyle = '#069'; ctx.fillText('Lite fingerprint 123', 4, 8);
  const url1 = c.toDataURL(), url2 = c.toDataURL();
  const px = ctx.getImageData(0, 0, 220, 40).data;
  const buf = new OfflineAudioContext(1, 4096, 44100).createBuffer(1, 4096, 44100);
  buf.copyToChannel(new Float32Array(4096).fill(0.5), 0);
  const samples = buf.getChannelData(0);
  let sum = 0; for (let i = 0; i < samples.length; i++) sum += samples[i];
  return JSON.stringify({
    canvas: hash(url1), stable: url1 === url2, pixels: hash(Array.from(px).join(',')),
    audio: sum.toFixed(5), cores: navigator.hardwareConcurrency,
    native: HTMLCanvasElement.prototype.toDataURL.toString() === 'function toDataURL() { [native code] }',
  });
})()
