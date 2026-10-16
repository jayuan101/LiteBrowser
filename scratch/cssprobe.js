// What cosmetic CSS did Lite manage to put into this page?
(() => JSON.stringify({
  adopted: document.adoptedStyleSheets.map((s) => s.cssRules.length),
  sample: document.adoptedStyleSheets.flatMap((s) => [...s.cssRules].slice(0, 3).map((r) => r.cssText.slice(0, 80))),
  webview: !!(window.chrome && window.chrome.webview),
  check: window.__check ? JSON.parse(window.__check()) : null,
}))()
