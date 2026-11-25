#!/bin/bash
# Development bundle without Xcode: compiles the app with swiftc and assembles Misty.app in build/dev.
# The real build is `cd app && xcodegen && xcodebuild` (needs the Xcode licence accepted). Usage: scripts/dev_app.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEV=/Applications/Xcode.app/Contents/Developer
SWIFTC=$DEV/Toolchains/XcodeDefault.xctoolchain/usr/bin/swiftc
SDK=$(ls -d $DEV/Platforms/MacOSX.platform/Developer/SDKs/MacOSX*.sdk | tail -1)
APP="$ROOT/build/dev/Misty.app"
# Voice needs the PhononTTS Swift package, which plain swiftc can't link: build with Xcode and copy the app here
# (same path, same signing identity, so macOS keeps Isaac's permission grants). MISTY_SWIFTC=1 keeps the old way.
if [ -z "${MISTY_SWIFTC:-}" ]; then
  bash "$ROOT/scripts/fetch_voice_models.sh" >/dev/null
  bash "$ROOT/scripts/fetch_ears_model.sh" >/dev/null
  bash "$ROOT/scripts/fetch_wake_model.sh" >/dev/null || echo "wake word: fetch failed, building without it" >&2
  (cd "$ROOT/app" && xcodegen -q && xcodebuild -project Misty.xcodeproj -scheme Misty -configuration Debug \
     -derivedDataPath "$ROOT/build/xcode.noindex" -destination 'platform=macOS,arch=arm64' build -quiet) >&2
  rm -rf "$APP" && mkdir -p "$(dirname "$APP")"
  ditto "$ROOT/build/xcode.noindex/Build/Products/Debug/Misty.app" "$APP"
  echo "$APP"; exit 0
fi
rm -rf "$APP" && mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
M="$ROOT/app/Misty"
$SWIFTC -sdk "$SDK" -swift-version 6 -parse-as-library -target arm64-apple-macosx15.0 -O \
  -o "$APP/Contents/MacOS/Misty" $(find "$M/Core" -name "*.swift") $(find "$M/Views" -name "*.swift") "$M"/Setup/*.swift "$M"/MistyApp.swift
cp "$M/Resources/graph.html" "$M/Resources/force-graph.min.js" "$APP/Contents/Resources/"
sed -e 's/$(EXECUTABLE_NAME)/Misty/' -e 's/$(PRODUCT_BUNDLE_IDENTIFIER)/com.isaacmiranda.Misty/' -e 's/$(PRODUCT_NAME)/Misty/' \
    -e 's/$(MARKETING_VERSION)/0.1.0/' -e 's/$(CURRENT_PROJECT_VERSION)/1/' -e 's/$(MACOSX_DEPLOYMENT_TARGET)/15.0/' \
    "$M/Info.plist" > "$APP/Contents/Info.plist"
IDENTITY="$(cat "$ROOT/config/signing.local" 2>/dev/null || echo -)"
codesign --force --sign "$IDENTITY" "$APP" 2>&1 | tail -1 || true
echo "$APP"
