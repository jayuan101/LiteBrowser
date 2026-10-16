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
- **Safety.** Known malware and scam sites get a warning page before anything
  loads; downloads from known malware links are stopped; program files ask
  "Download anyway?"; every finished download is scanned by the antivirus
  installed on the PC through AMSI. Optionally, Claude can check new sites for
  phishing (`ANTHROPIC_API_KEY`).
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
| `browser/scanner.py` | Optional Claude page scan |
| `browser/pages.py` | Built-in new tab and warning pages |
| `browser/ui/` | Toolbar and panel HTML |
| `packaging/` | Icon generator, PyInstaller spec and build script for the downloadable app |
| `scratch/` | Tests (`test_*.py`) and `drive.py`, a scripted driver for the real browser |

Run the tests with `python scratch/test_filters.py` (and the other `test_*.py`).

## License

MIT - see [LICENSE](LICENSE).
