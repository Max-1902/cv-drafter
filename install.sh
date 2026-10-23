#!/bin/bash
# Install CV Drafter into ~/Applications and open it:
#   curl -fsSL https://raw.githubusercontent.com/Max-1902/cv-drafter/main/install.sh | bash
set -euo pipefail
URL="https://github.com/Max-1902/cv-drafter/releases/latest/download/CV-Drafter-mac.zip"
TMP="$(mktemp -d)"
echo "Downloading CV Drafter…"
curl -fL --progress-bar "$URL" -o "$TMP/cv-drafter.zip"
ditto -x -k "$TMP/cv-drafter.zip" "$TMP"
mkdir -p "$HOME/Applications"
rm -rf "$HOME/Applications/CV Drafter.app"  # an older version; your data lives elsewhere and is kept
mv "$TMP/CV Drafter/CV Drafter.app" "$HOME/Applications/"
rm -rf "$TMP"
echo "Installed to ~/Applications/CV Drafter.app. Opening it… (the first start takes about 2 minutes)"
open "$HOME/Applications/CV Drafter.app"
