#!/bin/bash
# Build dist/CV Drafter.app and dist/CV-Drafter-mac.zip (the download). Only code goes in: never data/, .env or output/.
set -euo pipefail
cd "$(dirname "$0")/.."
UV_VERSION=0.10.9
DIST=dist
APP="$DIST/CV Drafter.app"
rm -rf "$DIST" && mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app"

# Code
rsync -a --exclude __pycache__ app.py requirements.lock .env.example .streamlit cvdrafter ui templates scripts \
  "$APP/Contents/Resources/app/"
cp packaging/loading.html "$APP/Contents/Resources/"
cp packaging/launcher.sh "$APP/Contents/MacOS/CV Drafter" && chmod 755 "$APP/Contents/MacOS/CV Drafter"

# uv (installs Python and the libraries on first start): one binary for Apple Silicon and Intel Macs
TMP="$(mktemp -d)"
for arch in aarch64 x86_64; do
  curl -fsSL "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$arch-apple-darwin.tar.gz" | tar -xz -C "$TMP"
  mv "$TMP/uv-$arch-apple-darwin/uv" "$TMP/uv-$arch"
done
lipo -create -output "$APP/Contents/Resources/uv" "$TMP/uv-aarch64" "$TMP/uv-x86_64"
chmod 755 "$APP/Contents/Resources/uv"

# Icon
.venv/bin/python packaging/make_icon.py "$TMP/icon.png"
mkdir "$TMP/AppIcon.iconset"
for s in 16 32 128 256 512; do
  sips -z $s $s "$TMP/icon.png" --out "$TMP/AppIcon.iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$TMP/icon.png" --out "$TMP/AppIcon.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$TMP/AppIcon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"
cp "$TMP/icon.png" "$DIST/icon.png"
rm -rf "$TMP"

VERSION="$(date +%Y.%m.%d)"
cat >"$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>CV Drafter</string>
  <key>CFBundleDisplayName</key><string>CV Drafter</string>
  <key>CFBundleIdentifier</key><string>io.github.max-1902.cv-drafter</string>
  <key>CFBundleExecutable</key><string>CV Drafter</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# Safety net: nothing personal may ship
if find "$APP" \( -name "profile.yaml" -o -name ".env" -o -name "*.db" -o -name "guardrails.yaml" \
     -o -name "learned_facts.yaml" -o -name "settings.json" \) | grep -q .; then
  echo "Personal file found in the app. Not packaging." >&2; exit 1
fi

# The download: a "CV Drafter" folder with the app and the read-me
mkdir "$DIST/CV Drafter"
mv "$APP" "$DIST/CV Drafter/"
cp packaging/READ-ME-FIRST.txt "$DIST/CV Drafter/"
ditto -c -k --keepParent "$DIST/CV Drafter" "$DIST/CV-Drafter-mac.zip"
APP="$DIST/CV Drafter/CV Drafter.app"
echo "Built $APP and $DIST/CV-Drafter-mac.zip ($(du -h "$DIST/CV-Drafter-mac.zip" | cut -f1))"
