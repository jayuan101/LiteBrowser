# Lite

A lightweight, privacy-first web browser for Windows, built on WebView2 (the
Chromium engine already installed with Windows) with Brave-style Shields.

## What it does

- **No ads.** Runs the same blocking engine Brave uses (adblock-rust) with the
  nine filter lists uBlock Origin enables by default. Lists download and update
  themselves. YouTube plays without video ads.
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
  or have open keep their cookies, so you stay logged in where you want and
  everything else forgets you.
- **Find in page** (Ctrl+F): highlight-and-scroll search bar on any page.
- **Settings page** (Ctrl+, or the menu): every switch in one place - cookie
  auto-deletion, tab sleeping and its delay, the update check, plus a button
  that wipes all cookies right away. Changes apply immediately and are saved.
- **Bookmarks and history** (Ctrl+D to star, Ctrl+H for history, or the
  library button): one searchable, built-in page manages both.
- **Reader mode** (the book button or View menu): an article page re-rendered
  as clean text with no layout, fonts or scripts from the site.
- **Tab sleeping**: background tabs you have not used for 30 minutes (settable
  in settings) release their memory and reload when you come back to them.
- **Update check**: Lite checks GitHub releases at start and quietly shows a
  banner when a newer version is out. It never auto-installs anything.
- **Safety.** Known malware and scam sites get a warning page before anything
  loads; downloads from known malware links are stopped; program files ask
  "Download anyway?"; every finished download is scanned by the antivirus
  installed on the PC through AMSI.
- **New tab page** with a clock and running totals. Lite never shows ads or
  sponsored content of its own.

## Download

Grab `Lite-windows-x64.zip` from the
[latest release](https://github.com/jayuan101/LiteBrowser/releases/latest),
unzip it anywhere and run `Lite.exe`. No Python needed. Windows 10/11 with the
WebView2 runtime (ships with Windows 11 and Edge) is required.

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
| `browser/store.py` | Bookmarks, history and settings (JSON files in the profile) |
| `browser/updater.py` | GitHub release update check |
| `browser/pages.py` | Built-in new tab, reader, find bar, library and warning pages |
| `browser/ui/` | Toolbar and panel HTML |
| `packaging/` | Icon generator, PyInstaller spec and build script for the downloadable app |
| `scratch/` | Tests (`test_*.py`) and `drive.py`, a scripted driver for the real browser |

Run the tests with `python scratch/test_filters.py` (and the other `test_*.py`).

## License

MIT - see [LICENSE](LICENSE).
