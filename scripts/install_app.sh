#!/bin/bash
# Build + test Misty.app and install the one real copy to ~/Applications.
# Build output goes to build/xcode.noindex so Spotlight/Alfred never list a second Misty.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DD="$ROOT/build/xcode.noindex"
# the "Edith" wake word's library and model (official sherpa-onnx releases, checksummed). Quick when already there; if it can't
# be fetched the app still builds, with the wake word compiled out.
bash "$ROOT/scripts/fetch_wake_model.sh" >/dev/null || echo "wake word: fetch failed, building without it" >&2
cd "$ROOT/app" && xcodegen >/dev/null
xcodebuild -project Misty.xcodeproj -scheme Misty -destination 'platform=macOS' -derivedDataPath "$DD" test \
  > "$DD.log" 2>&1 || { grep -E "error:|failed" "$DD.log" | head -20; exit 1; }
APP="$DD/Build/Products/Debug/Misty.app"
codesign --verify --strict "$APP"
pkill -f "Contents/MacOS/Misty" || true; sleep 1
rm -rf "$HOME/Applications/Misty.app" "$HOME/Applications/EDITH.app" && mkdir -p "$HOME/Applications" && ditto "$APP" "$HOME/Applications/EDITH.app"
open "$HOME/Applications/EDITH.app"
echo "installed $HOME/Applications/EDITH.app"
