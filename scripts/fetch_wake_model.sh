#!/bin/bash
# Everything the "Edith" wake word needs, from the official sherpa-onnx GitHub releases only. Run once; safe to re-run.
#   1. The sherpa-onnx C library (static, arm64, no TTS) and its C header, into app/Vendor/sherpa-onnx (gitignored).
#      The app links it by itself (project.yml), so there is no manual Xcode step. Apache-2.0.
#   2. The streaming keyword-spotting model (zipformer, 3.3M parameters, about 6 MB) into ~/Misty/models/wake. Apache-2.0.
# Without step 1 the app still builds (the wake word is simply compiled out); without step 2 it stays off and says so in the log.
# Usage: scripts/fetch_wake_model.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOME_DIR="${MISTY_HOME:-$HOME/Misty}"
VERSION=v2.0.2
LIB_NAME="sherpa-onnx-$VERSION-osx-arm64-static-no-tts-lib"
LIB_SHA=80b98612de9a50038c9a3ac1812ec4e6833982dd58fc58dbec1eafeff072aec2     # from the release's own checksum.txt
HDR_SHA=1f738de63a206328253ff471edda96b011fa1bd6f2a028f82967a7761abc2edd     # sherpa-onnx/c-api/c-api.h at the same tag
KWS_NAME=sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01
KWS_SHA=f170013b4716e41b62b9bfd809687c207cef798ef9bc6534d524e17af9b6561a     # the kws-models release publishes none: pinned here
GH=https://github.com/k2-fsa/sherpa-onnx
sha() { shasum -a 256 "$1" | cut -d' ' -f1; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# ---- 1. the library -------------------------------------------------------------------------------------------------
V="$ROOT/app/Vendor/sherpa-onnx"
if [ "$(cat "$V/.rev" 2>/dev/null)" != "$VERSION-$LIB_SHA-$HDR_SHA-L2" ]; then
  curl -fL --retry 3 -o "$TMP/lib.tar.bz2" "$GH/releases/download/$VERSION/$LIB_NAME.tar.bz2"
  [ "$(sha "$TMP/lib.tar.bz2")" = "$LIB_SHA" ] || { echo "checksum mismatch: sherpa-onnx library" >&2; exit 1; }
  curl -fL --retry 3 -o "$TMP/c-api.h" "https://raw.githubusercontent.com/k2-fsa/sherpa-onnx/$VERSION/sherpa-onnx/c-api/c-api.h"
  [ "$(sha "$TMP/c-api.h")" = "$HDR_SHA" ] || { echo "checksum mismatch: c-api.h" >&2; exit 1; }
  rm -rf "$V" && mkdir -p "$V/lib" "$V/include"
  tar xjf "$TMP/lib.tar.bz2" -C "$TMP"
  # only what the keyword spotter pulls in; the linker drops the rest of each archive
  for a in sherpa-onnx-c-api sherpa-onnx-core onnxruntime kaldi-native-fbank-core kissfft-float utf8proc \
           sherpa-onnx-fst sherpa-onnx-fstfar sherpa-onnx-kaldifst-core ssentencepiece_core kaldi-decoder-core kaldi-native-io-core kaldi-hmm-gmm-core; do
    cp "$TMP/$LIB_NAME/lib/lib$a.a" "$V/lib/"
  done
  cp "$TMP/c-api.h" "$V/include/c-api.h"
  cat > "$V/include/module.modulemap" <<'MAP'
module SherpaOnnxC [system] {
    header "c-api.h"
    link "c++"
    export *
}
MAP
  echo "$VERSION-$LIB_SHA-$HDR_SHA-L2" > "$V/.rev"
fi
# app/Base.xcconfig includes this file when it exists (#include?), so a checkout without the library still builds, wake word compiled out
cat > "$V/wake.xcconfig" <<'XC'
SWIFT_INCLUDE_PATHS = $(inherited) $(SRCROOT)/Vendor/sherpa-onnx/include
LIBRARY_SEARCH_PATHS = $(inherited) $(SRCROOT)/Vendor/sherpa-onnx/lib
OTHER_LDFLAGS = $(inherited) -lsherpa-onnx-c-api -lsherpa-onnx-core -lonnxruntime -lkaldi-native-fbank-core -lkissfft-float -lutf8proc -lsherpa-onnx-fst -lsherpa-onnx-fstfar -lsherpa-onnx-kaldifst-core -lssentencepiece_core -lkaldi-decoder-core -lkaldi-native-io-core -lkaldi-hmm-gmm-core
XC
echo "wake library: $V ($(du -sh "$V" | cut -d$'\t' -f1))"

# ---- 2. the model ---------------------------------------------------------------------------------------------------
M="$HOME_DIR/models/wake/$KWS_NAME"
FILES="decoder-epoch-12-avg-2-chunk-16-left-64.onnx encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx tokens.txt bpe.model"
SUMS="f61ebd3eed3773a44d088d53dfae92dbb6aec4839f4dcaee2d402414741663a3 1e721676515bcd42a186979733981213c66c80db680e1cc582dfedf3be76e678 eae9da0c7e1e6c6a3f4cc42d167899c388f6c6701b94cb96320e4f55df79624c fd2ded4050a55d2b1578870ba8697d02371980217806b7558bd0a5cc60f3ba53 c8a2a0129c4ab8e463164c142f82d25649661b122c8cd0b7aab5c9e80b90ad24"
have=1
set -- $SUMS
for f in $FILES; do [ -f "$M/$f" ] && [ "$(sha "$M/$f")" = "$1" ] || have=0; shift; done
if [ "$have" = 0 ]; then
  curl -fL --retry 3 -o "$TMP/kws.tar.bz2" "$GH/releases/download/kws-models/$KWS_NAME.tar.bz2"
  [ "$(sha "$TMP/kws.tar.bz2")" = "$KWS_SHA" ] || { echo "checksum mismatch: keyword model" >&2; exit 1; }
  tar xjf "$TMP/kws.tar.bz2" -C "$TMP"
  mkdir -p "$M"
  for f in $FILES; do cp "$TMP/$KWS_NAME/$f" "$M/$f"; done
fi
echo "wake model: $M ($(du -sh "$M" | cut -d$'\t' -f1))"
