# Windows 版を作る。  powershell -ExecutionPolicy Bypass -File build_win.ps1 [-OneFile]
#   通常:     $Out\dist\DialMatrix\DialMatrix.exe (フォルダごと使う)
#   -OneFile: $Out\dist_onefile\DialMatrix.exe     (1ファイルだけ。起動は少し遅い)
# 先に: python -m pip install pyinstaller hidapi pywebview pillow
param([string]$Python = "python", [string]$Out = "$env:USERPROFILE\DialMatrix_build", [switch]$OneFile)   # OneDrive の外に出力する(同期がファイルをつかんで消せなくなるのを避ける)
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"
& $Python make_notices.py      # THIRD_PARTY_NOTICES.md (goes into the app)
New-Item -ItemType Directory -Force "_ed_lite" | Out-Null
'EDITION = "lite"' | Set-Content -Encoding ascii "_ed_lite\revomate_edition.py"
& $Python make_icon.py         # AppIcon.ico (Mac 版と同じ絵。Pillow が必要)
$mode = if ($OneFile) { "--onefile" } else { "--onedir" }
$dist = if ($OneFile) { "$Out\dist_onefile" } else { "$Out\dist" }
& $Python -m PyInstaller --noconfirm --windowed $mode --name DialMatrix --icon "$PSScriptRoot\AppIcon.ico" `
  --distpath $dist --workpath "$Out\work" --specpath "$Out\spec" `
  --add-data "$PSScriptRoot\..\revomate_studio.html;." --add-data "$PSScriptRoot\..\THIRD_PARTY_NOTICES.md;." `
  --paths .. --paths "$PSScriptRoot\_ed_lite" `
  --hidden-import webview --hidden-import revomate_ledpreview --hidden-import revomate_sound `
  --hidden-import revomate_edition --hidden-import revomate_config --hidden-import revomate_scripts `
  ..\revomate_app.py
if ($OneFile) { Write-Host "できあがり: $dist\DialMatrix.exe" } else { Write-Host "できあがり: $dist\DialMatrix\DialMatrix.exe" }
