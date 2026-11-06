# LiteBrowser-Rust

Standalone Rust Windows preview. This folder contains only this app, with its own Cargo workspace, build output, installer, launch button, and app-local support code. It does not depend on the other Rust project folders.

Tabs, address/search entry, navigation, bookmarks/history and saved sessions. Original Shields/privacy protections, private tabs, extensions, password manager, downloads and updates are NOT ported. Do not treat this preview as having the original browser's privacy features.

## Locations

- Source: `D:\Projects\LiteBrowser-Rust`
- App data: `D:\UserData\Apps\LiteBrowser-Rust`
- Builds: this project's `target` and `dist` folders
- Local Rust tools/cache: this project's `.tools` folder
- Temporary files and logs: this project's `.tmp` and `logs` folders
- Repository: https://github.com/jayuan101/LiteBrowser
- Branch: `main`; source: `native-rust/`

The original Windows app and its data remain unchanged. This folder holds the app's source; `.repo` is its Git clone, kept in step by `scripts\release.py sync`.

## Use

Run `UPDATE.cmd` to download and install the verified build pinned in `ci/latest-build.json` using your signed-in GitHub CLI. Close the running app first, then open `START.cmd`. `INSTALL.cmd` installs a manually downloaded build ZIP with the same checks.

For local development, open `OPEN-PROJECT.cmd`, run `scripts\install-rust.ps1` once, then `scripts\local-test.ps1`. Windows CI builds the executable that gets installed; local builds are for testing.

Automated tests and a launch check do not replace trying each feature with real hardware and provider accounts. See `VALIDATION.json` for what has actually been checked and [FEATURE-PARITY.md](FEATURE-PARITY.md) for remaining work.
