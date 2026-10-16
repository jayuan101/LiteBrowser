# PyInstaller spec for Lite.  Build with:  packaging\build.ps1   (or: pyinstaller packaging\lite.spec)
# Produces dist\Lite\Lite.exe (one folder, windowed, with the shield icon).
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))

a = Analysis(
    [os.path.join(ROOT, 'main.py')],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, 'browser', 'ui'), os.path.join('browser', 'ui')),
        (os.path.join(ROOT, 'blocklist.txt'), '.'),
        (os.path.join(ROOT, 'config.json'), '.'),
        (os.path.join(ROOT, 'LICENSE'), '.'),
        (os.path.join(ROOT, 'packaging', 'lite.ico'), 'packaging'),
    ],
    # pythonnet (clr) and pywebview's WebView2 DLLs are collected by PyInstaller's bundled hooks.
    hiddenimports=['adblock', 'anthropic'],
    excludes=['tkinter', 'unittest', 'pydoc', 'test', 'PIL', 'numpy', 'matplotlib', 'scipy', 'pandas', 'IPython', 'jupyter'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name='Lite',
    icon=os.path.join(ROOT, 'packaging', 'lite.ico'),
    console=False,
    debug=False,
    strip=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Lite')
