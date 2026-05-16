#!/usr/bin/env bash
# Build mac/Resources/AppIcon.icns from the SwiftUI-rendered source PNG.
#
# Re-run when the icon design changes — the .icns is committed to the repo so
# end users don't need to regenerate it.
#
#   ./mac/scripts/make-icon.sh
#
# Requires: swift, sips, iconutil  (all preinstalled on macOS).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAC_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$MAC_DIR/build"
SRC_PNG="$BUILD_DIR/icon-source.png"
ICONSET="$BUILD_DIR/AppIcon.iconset"
OUT_ICNS="$MAC_DIR/Resources/AppIcon.icns"

mkdir -p "$BUILD_DIR" "$MAC_DIR/Resources"

echo "→ Rendering 1024x1024 source via swift"
swift "$SCRIPT_DIR/render-icon.swift" "$SRC_PNG"

echo "→ Building iconset"
rm -rf "$ICONSET"
mkdir -p "$ICONSET"

# Apple's expected sizes for a complete .icns:
#   16, 16@2x (=32), 32, 32@2x (=64), 128, 128@2x (=256),
#   256, 256@2x (=512), 512, 512@2x (=1024)
sips -z 16   16   "$SRC_PNG" --out "$ICONSET/icon_16x16.png"        > /dev/null
sips -z 32   32   "$SRC_PNG" --out "$ICONSET/icon_16x16@2x.png"     > /dev/null
sips -z 32   32   "$SRC_PNG" --out "$ICONSET/icon_32x32.png"        > /dev/null
sips -z 64   64   "$SRC_PNG" --out "$ICONSET/icon_32x32@2x.png"     > /dev/null
sips -z 128  128  "$SRC_PNG" --out "$ICONSET/icon_128x128.png"      > /dev/null
sips -z 256  256  "$SRC_PNG" --out "$ICONSET/icon_128x128@2x.png"   > /dev/null
sips -z 256  256  "$SRC_PNG" --out "$ICONSET/icon_256x256.png"      > /dev/null
sips -z 512  512  "$SRC_PNG" --out "$ICONSET/icon_256x256@2x.png"   > /dev/null
sips -z 512  512  "$SRC_PNG" --out "$ICONSET/icon_512x512.png"      > /dev/null
cp "$SRC_PNG"            "$ICONSET/icon_512x512@2x.png"

echo "→ iconutil -c icns"
iconutil -c icns "$ICONSET" -o "$OUT_ICNS"

echo "✓ Wrote $OUT_ICNS"
du -h "$OUT_ICNS" | awk '{print "  size: " $1}'
