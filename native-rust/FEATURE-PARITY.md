# Feature parity worklist

Full feature parity is required. These Rust apps remain previews until the missing behavior is implemented, Windows builds pass, and the installed app is exercised. A repository commit alone is not an app update.

Initial audit: 2026-11-06. Compared original README and source/module inventory with Rust source; this is a living checklist, not an exhaustive acceptance result. “Added” means source implementation, not installed or interactively verified.

| Feature | Current status | Original source reference |
|---|---|---|
| Adblock engine/filter updates, scriptlets and Twitch handling | Missing. | `browser/blocker.py; filters.py; twitch.py` |
| Per-site Shields, counters and exceptions | Missing. | `browser/tab.py; stats.py` |
| Tracking URL cleanup, GPC, fingerprint randomization | Missing. | `browser/privacy.py` |
| Private tabs with separate ephemeral state | Missing. | `browser/tab.py` |
| Auto cookie cleanup with open/bookmarked domain exceptions | Missing. | `browser/privacy.py` |
| Find in page and per-site zoom | Missing. | `browser/app.py` |
| Reopen closed tabs | Added in source: last ten URLs, toolbar action. Global shortcut pending. | `browser/app.py` |
| History/bookmark search, bookmarks bar and browser imports | Partial: searchable list added; bar/imports missing. | `browser/importer.py; store.py` |
| Pinned/sleeping/lazy tabs and session options | Partial: simple tab/session restore only. | `browser/tab.py` |
| Single instance, external links and default browser registration | Missing. | `browser/instance.py; default_browser.py` |
| Passwords with DPAPI and explicit origin-bound fill | Missing. | `browser/passwords.py; autofill.py` |
| Signed Chrome extension install/update/management | Missing. | `browser/extensions.py` |
| Reader mode, Lite Search and full settings | Missing. | `browser/search.py; pages.py` |
| Download manager, executable confirmation and AMSI | Missing; downloads blocked in preview. | `browser/downloads.py; scanner.py` |
| Malware/scam navigation warnings | Missing. | `browser/guard.py` |
| App update download, installation and rollback | Partial: checked ZIP import only. | `browser/updater.py` |

## Completion criteria for every row

- Match the original user workflow and failure/recovery behavior.
- Add appropriate regression tests; test Windows runtime behavior and needed hardware/providers.
- Push tested source to this app’s repository and build a matching executable.
- Install that exact verified build under this app’s D: folder and verify START.cmd opens it.
- Preserve the original app and user data until replacement acceptance.

## Current installation limitation

No Rust compiler is installed locally. The agent terminal could not download build archives due to restricted network access. GitHub CI can compile them, but a successful CI run does not mean the binaries are installed on this PC.
