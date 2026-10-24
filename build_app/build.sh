#!/bin/bash
# Mac アプリを作る。  ./build.sh        → DialMatrix.app
# 先に: python3 -m venv .venv && .venv/bin/pip install pyinstaller hidapi pywebview pillow
cd "$(dirname "$0")"
"$(dirname "$0")/.venv/bin/python" "$(dirname "$0")/make_notices.py"      # THIRD_PARTY_NOTICES.md (goes into the app)
build_one() {
  local ed="$1" name page icon bid hidden signed=0
  name="DialMatrix"; page="revomate_studio.html"; icon="AppIcon.icns"; bid="com.dialmatrix.lite"
  hidden="--hidden-import revomate_ledpreview"
  mkdir -p "_ed_$ed" && echo "EDITION = \"$ed\"" > "_ed_$ed/revomate_edition.py"
  .venv/bin/python -m PyInstaller --noconfirm --windowed --name "$name" --icon "$PWD/$icon" --osx-bundle-identifier "$bid" \
    --workpath "_work_$ed" --specpath "_spec_$ed" \
    --add-data "$PWD/../$page:." --add-data "$PWD/../THIRD_PARTY_NOTICES.md:." --paths .. --paths "$PWD/_ed_$ed" \
    --hidden-import webview --hidden-import ServiceManagement $hidden --hidden-import revomate_sound --hidden-import AppKit --hidden-import Quartz \
    --hidden-import revomate_edition --hidden-import revomate_config --hidden-import revomate_scripts ../revomate_app.py
  # tell macOS the app speaks Japanese and English, so its system panels (e.g. "save as") follow the Mac's language
  plutil -replace CFBundleLocalizations -json '["en","ja"]' "dist/$name.app/Contents/Info.plist"
  plutil -replace CFBundleDevelopmentRegion -string "en" "dist/$name.app/Contents/Info.plist"
  codesign --force --deep -s - "dist/$name.app" 2>&1 | tail -1
  xattr -cr "dist/$name.app"
  rm -rf "dist/$name"      # PyInstaller also leaves a plain folder next to the .app; not needed
  touch "dist/$name.app"
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "dist/$name.app" >/dev/null 2>&1
  echo "できあがり: $(pwd)/dist/$name.app"
}
build_one lite
