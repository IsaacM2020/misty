#!/bin/bash
# Compiles app/Misty/Core + app/MistyTests into an XCTest bundle and runs it with Xcode's xctest.
# Works without accepting the Xcode licence (direct toolchain paths, no xcrun). Usage: scripts/swift_test.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEV=/Applications/Xcode.app/Contents/Developer
SWIFTC=$DEV/Toolchains/XcodeDefault.xctoolchain/usr/bin/swiftc
SDK=$(ls -d $DEV/Platforms/MacOSX.platform/Developer/SDKs/MacOSX*.sdk | tail -1)
FW=$DEV/Platforms/MacOSX.platform/Developer/Library/Frameworks
LIB=$DEV/Platforms/MacOSX.platform/Developer/usr/lib
OUT="${TMPDIR:-/tmp}/misty-swift-tests"
rm -rf "$OUT" && mkdir -p "$OUT/MistyTests.xctest/Contents/MacOS"
# the module is compiled together with its tests so @testable import works without a separate library
mkdir -p "$OUT/src"
find "$ROOT"/app/Misty/Core -name "*.swift" -exec cp {} "$OUT/src/" \;
for f in "$ROOT"/app/MistyTests/*.swift; do sed 's/^@testable import MistyCore$//' "$f" > "$OUT/src/$(basename "$f")"; done
$SWIFTC -sdk "$SDK" -swift-version 6 -parse-as-library -module-name MistyTests \
  -F "$FW" -I "$LIB" -Xlinker -rpath -Xlinker "$FW" -L "$LIB" -Xlinker -rpath -Xlinker "$LIB" \
  -Xlinker -bundle -o "$OUT/MistyTests.xctest/Contents/MacOS/MistyTests" "$OUT"/src/*.swift
cat > "$OUT/MistyTests.xctest/Contents/Info.plist" <<'PL'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>MistyTests</string>
<key>CFBundleIdentifier</key><string>com.isaacmiranda.MistyTests</string>
<key>CFBundleName</key><string>MistyTests</string>
<key>CFBundlePackageType</key><string>BNDL</string>
<key>CFBundleVersion</key><string>1</string>
</dict></plist>
PL
MISTY_ENGINE_CMD="${MISTY_ENGINE_CMD:-env PYTHONPATH=$ROOT/engine/src MISTY_NO_WARM=1 $ROOT/engine/.venv/bin/python -m misty_engine}" MISTY_CONTRACT_DIR="$ROOT/tests/contract" DYLD_FRAMEWORK_PATH="$FW" DYLD_LIBRARY_PATH="$LIB" "$DEV/usr/bin/xctest" "$OUT/MistyTests.xctest" 2>&1
