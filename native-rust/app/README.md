# LiteBrowser Rust preview

A native Rust Windows host using Tao and Wry/WebView2. The original project at `D:\LiteBrowser` is unchanged. This preview is an independent Rust workspace; it does not run Python or the original application.

Implemented: multiple tabs (up to 20), address/search entry, back/forward/reload, bookmarks, recent history (up to 1,000 URLs), session save/restore, and a separate profile on D:. The address bar reads the current WebView URL, including same-document changes. Tab titles and URLs are inserted as text, not HTML.

Storage: `D:\UserData\Apps\LiteBrowser-Rust`. Browser content and the trusted toolbar use separate WebView2 contexts under that folder. Only the toolbar has a Rust IPC handler; remote websites do not receive native app commands. Top-level navigation permits HTTP/HTTPS and `about:blank`. Pop-ups, external app protocols, and downloads are blocked in this preview.

**Original Shields, filter lists, fingerprinting protection, cookie cleanup, private tabs, password manager, extensions, reader mode, custom search, sleeping tabs, default-browser registration, download scanning, and updates are not ported.** Do not treat this preview as having the original browser's privacy features. The window states this limitation.

Use `START.cmd` at the workspace root after installing a verified build. The Microsoft Edge WebView2 runtime is required. The app does not change your default browser or import another browser's passwords/profile.

Validation: URL handling, bookmark identity, and restored-state bounds have Rust tests. A successful build is not interactive acceptance: verify navigation, DPI resize, tab switching, session restore, and WebView2 startup on the intended Windows PC.

Primary implementation references: [Wry WebViewBuilder](https://docs.rs/wry/0.53.5/wry/struct.WebViewBuilder.html), [Wry native example](https://github.com/tauri-apps/wry/blob/dev/examples/simple.rs).
