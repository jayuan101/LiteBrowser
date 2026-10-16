# Builds the downloadable Lite app:  dist\Lite\Lite.exe  and  dist\Lite-windows-x64.zip
#   .\packaging\build.ps1
# Needs: pip install pyinstaller pillow   (plus Lite's own requirements: pythonnet pywebview adblock)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python packaging\make_icon.py
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\lite.spec
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }

$zip = Join-Path $root 'dist\Lite-windows-x64.zip'
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path (Join-Path $root 'dist\Lite') -DestinationPath $zip -CompressionLevel Optimal
Write-Host "built $zip ($([math]::Round((Get-Item $zip).Length / 1MB)) MB)"
