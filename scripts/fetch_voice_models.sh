#!/bin/bash
# Downloads the Phonon 31m English Core ML bundle (Apache 2.0) that Misty speaks with, into
# app/VoiceModels/PhononModels (gitignored). The app bundles that folder and installs it on first launch.
# Pinned to the same release as the PhononTTS package in app/project.yml. Usage: scripts/fetch_voice_models.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION=v0.4.0
URL="https://github.com/gradium-ai/xn-ptts/releases/download/$VERSION/phonon-31m-en-coreml.zip"
SHA256=10f3a99fd39256db44f826267ef21d78cca959b16c0554926344c09fde27b436
DEST="$ROOT/app/VoiceModels/PhononModels"
if [ -f "$DEST/bundle.json" ] && [ "$(cat "$DEST/.release" 2>/dev/null)" = "$VERSION" ]; then
  echo "voice models already here: $DEST"; exit 0
fi
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
curl -fL --retry 3 -o "$TMP/models.zip" "$URL"
echo "$SHA256  $TMP/models.zip" | shasum -a 256 -c - >/dev/null || { echo "checksum mismatch for $URL" >&2; exit 1; }
unzip -q "$TMP/models.zip" -d "$TMP/x"
rm -rf "$DEST" && mkdir -p "$(dirname "$DEST")"
mv "$TMP/x/phonon-31m-en-coreml" "$DEST"
echo "$VERSION" > "$DEST/.release"
echo "voice models: $DEST ($(du -sh "$DEST" | cut -f1))"
