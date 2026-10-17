# Lite

A lightweight, privacy-first web browser for Windows, built on WebView2 (the
Chromium engine already installed with Windows) with Brave-style Shields.

## What it does

- **No ads.** Runs the same blocking engine Brave uses (adblock-rust) with the
  nine filter lists uBlock Origin enables by default. Lists download and update
  themselves. YouTube plays without video ads. Twitch stitches its video ads into
  the stream itself, where no list can reach them: during an ad break Lite plays
  the same stream as Twitch's embedded player gets it, without the ads, or - when
  every copy has them, as with most pre-rolls - cuts the ad clips out, and the
  video waits out the break instead of showing it.
- **Shields panel** per site: on/off switch, counts, and what was blocked on the
  page. One click turns Shields off for a site that misbehaves.
- **Privacy.** Tracking codes such as `fbclid` are stripped from links, every
  request carries the Global Privacy Control ("do not sell my data") signal,
  and pop-ups opened without a click are blocked.
- **Fingerprinting protection**, Brave-style: canvas, WebGL and audio readouts
  and the CPU core count are subtly randomized per site and per session, so a
  site cannot recognise you across sites or across restarts. Private tabs use
  their own key. The Shields panel shows how many scripts were fed randomized
  values; turning Shields off for a site turns this off too.
- **Private tabs** (Ctrl+Shift+N) keep nothing once closed.
- **Auto cookie deletion.** Every 30 seconds Lite deletes the cookies of sites
  you have not starred and that are not open in a tab. Sites you star (Ctrl+D)
  or have open keep their cookies - and so do the sites an open page loads
  from, such as the sign-in check Twitch runs from another domain - so you stay
  logged in where you want and everything else forgets you.
- **Find in page** (Ctrl+F): highlight-and-scroll search bar on any page.
- **Lite Search**: pick "Lite" as the search engine in Settings and typing a
  search shows Lite's own results page - plain rows of title, address and
  snippet, fetched from a clean index in the background. No ads, no tracking
  cookies, no search-history profile; the query is sent once and nothing is
  stored. Any other engine still works the same as before.
- **Zoom** (Ctrl +/−/0): per-site and remembered across restarts. Ctrl+scroll
  zooms the page in front of you too, but only the keys are remembered.
- **Reopen closed tab** (Ctrl+Shift+T): the last ten closed tabs, one press each.
- **Your tabs come back.** Close Lite (or restart it to update) and the next
  start reopens the tabs you had, pinned ones still pinned. They wait asleep
  and load when you click them, so the start stays fast. Private tabs never
  come back. Switch it off in Settings > Tabs.
- **Back and forward lists**: right-click or hold the Back or Forward button
  to see the pages that way and jump several at once.
- **One Lite at a time.** A link opened from another app becomes a new tab in
  the Lite that is already open, instead of a second copy whose cookie
  clean-up and saved files would fight with the first.
- **Pin tab** (Ctrl+Shift+P or the menu): a pinned tab never sleeps, so a site
  you keep around stays exactly where it is - at the cost of keeping its
  renderer alive (~30 MB per pinned site).
- **Settings page** (Ctrl+, or the menu): every switch in one place - cookie
  auto-deletion, tab sleeping and its delay, the update check, plus a button
  that wipes all cookies right away. Changes apply immediately and are saved.
- **Bookmarks and history** (Ctrl+D to star, Ctrl+H for history, or the
  library button): one searchable, built-in page manages both.
- **Bookmarks bar** (toggle in Settings): your starred pages in a row under
  the toolbar - left-click opens, middle-click opens a new tab.
- **Import bookmarks**: one click pulls them from Chrome, Edge, Brave or
  Firefox on this PC (read-only), or from any browser's bookmarks.html export.
- **Passwords**: when you sign in, Lite offers to save the password (or to
  update a changed one); on a sign-in page the key button in the address bar
  fills a saved one in. It fills only when you pick a login from that button -
  Lite's own window, which no page can click - and only on the site the login
  belongs to, so a hidden form or a script on a page cannot collect it. You can
  also import a CSV exported from another browser's password manager.
  Passwords are encrypted with Windows DPAPI to your account, never shown on
  screen and never sent anywhere.
- **Reader mode** (the book button or View menu): an article page re-rendered
  as clean text with no layout, fonts or scripts from the site.
- **Make Lite the default browser** (Settings > System): registers Lite's
  http/https handling in one click (no admin needed) and opens Windows'
  Default apps page on Lite's entry to confirm.
- **High performance mode** (Settings): every tab tells its renderer to keep
  memory low and background tabs let go of their memory after 5 minutes.
- **Tab sleeping**: background tabs you have not used for 30 minutes (settable
  in settings) release their memory and reload when you come back to them.
- **Over-the-air updates**: Lite checks GitHub for a newer release once a day,
  downloads it in the background and shows a quiet "Restart to update" note on
  the new tab page - one click and it restarts into the new version. Nothing
  opens, nothing to download manually. The Check button in Settings looks on
  demand.
- **Safety.** Known malware and scam sites get a warning page before anything
  loads; downloads from known malware links are stopped; program files ask
  "Download anyway?"; every finished download is scanned by the antivirus
  installed on the PC through AMSI.
- **New tab page** with a clock and running totals. Lite never shows ads or
  sponsored content of its own.

## Download

Grab `Lite-windows-x64.zip` from the
[latest release](https://github.com/jayuan101/LiteBrowser/releases/latest),
unzip it to a folder of its own (for example `%LOCALAPPDATA%\Programs\Lite`, no
admin needed) and run `Lite.exe`. No Python needed. Windows 10/11 with the
WebView2 runtime (ships with Windows 11 and Edge) is required.

From 0.2.1 on, Lite updates itself in place. Copies of 0.2.0 or older only open
the release page: download the zip once by hand, close Lite, and unzip the
`Lite` folder's contents over the old copy. Don't run Lite from the repo's
`dist\` folder, because every build replaces it.

Windows SmartScreen may warn on first run because the build is not code-signed;
choose "More info" > "Run anyway".

## Running from source

Python 3.12+:

```
pip install pythonnet pywebview adblock
python main.py [url ...]
```

Without the `adblock` package Lite falls back to a short built-in blocklist.

## Building the app

```
pip install pyinstaller pillow
.\packaging\build.ps1
```

This draws `packaging/lite.ico`, runs PyInstaller with `packaging/lite.spec`
and writes `dist\Lite\Lite.exe` plus `dist\Lite-windows-x64.zip`.

## Layout

| Path | What it is |
| --- | --- |
| `browser/app.py` | Window, toolbar, tabs, downloads, Shields panel wiring |
| `browser/tab.py` | One tab: navigation guard, request blocking, scriptlets |
| `browser/filters.py` | Filter lists, the adblock-rust engine, cosmetic and scriptlet rules |
| `browser/blocker.py` | Shields state per site, built-in fallback blocklist |
| `browser/privacy.py` | Tracking-link cleaning, GPC, fingerprinting randomization script |
| `browser/guard.py` | Phishing-style URL checks (punycode, `user@host`, `javascript:`) |
| `browser/downloads.py` | Download checks and AMSI antivirus scanning |
| `browser/scanner.py` | Optional page-check hook (disabled placeholder) |
| `browser/store.py` | Bookmarks, history, the open tabs and settings (JSON files in the profile) |
| `browser/passwords.py` | The password vault (DPAPI) and which site a login belongs to |
| `browser/autofill.py` | The sign-in page script, and the script that fills a chosen login |
| `browser/twitch.py` | Twitch's video ads, which are stitched into the stream |
| `browser/instance.py` | One Lite per profile: later starts hand their links over |
| `browser/updater.py` | GitHub release update check |
| `browser/pages.py` | Built-in new tab, reader, find bar, library and warning pages |
| `browser/ui/` | Toolbar and panel HTML |
| `packaging/` | Icon generator, PyInstaller spec and build script for the downloadable app |
| `scratch/` | Tests (`test_*.py`) and `drive.py`, a scripted driver for the real browser |

Run the tests with `python scratch/test_filters.py` (and the other `test_*.py`).

## License

MIT - see [LICENSE](LICENSE).
