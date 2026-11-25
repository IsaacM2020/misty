#!/bin/bash
# Puts the Phonon-2 Core ML model (Fermion Research, CC-BY-4.0 weights, 330 MB) where Misty's ears load it:
# ~/Library/Application Support/Misty/Phonon-2-CoreML. It runs on the Neural Engine from Swift, no Python.
# Reuses Ripple's copy when its files match the pinned checksums (APFS clone, no extra disk), else downloads
# from Hugging Face at a pinned commit. Usage: scripts/fetch_ears_model.sh
set -euo pipefail
REV=1143812fd4236522232a8c33e3115f0577e3cd7d
BASE="https://huggingface.co/FermionResearch/Phonon-2-CoreML/resolve/$REV"
DEST="$HOME/Library/Application Support/Misty/Phonon-2-CoreML"
RIPPLE="$HOME/.config/ripple/Phonon-2-CoreML"
FILES="ab3fb5dfd3fc07b1d8a24379d04418f458d0443ba6359d3a5ab2f098a7d64089 manifest.json
36fa7202dda85c603af60d930ab88d7e9f0d2a619490aff46f5384db924cfa70 decoder.bin
6ebc9309aea0e16bda9c7558521fef6307fac0afba9a5b4959fea45ed7cd0429 Phonon-2.mlpackage/Manifest.json
4f6790ec94fe4429b10397c013563d0a51119953ff6d3da466bbcb23a25dee2a Phonon-2.mlpackage/Data/com.apple.CoreML/model.mlmodel
93aa991318a00ed492fdbb724e7bca4e6a69c6cf671ab4f14a14ef6101e7648b Phonon-2.mlpackage/Data/com.apple.CoreML/weights/weight.bin
9ba9550ad48438d0836ddab3da480b3b69ffa0aac7b7878b5a0039e7ab429411 LICENSE-WEIGHTS-CC-BY-4.0.txt
b468a23a1ce2c5181ea4050432bb68713a6468ce2e452927228d24f3d56226be NOTICE"
ok() { [ -f "$2" ] && [ "$(shasum -a 256 "$2" | cut -d' ' -f1)" = "$1" ]; }
while read -r sum f; do
  ok "$sum" "$DEST/$f" && continue
  mkdir -p "$(dirname "$DEST/$f")"
  if ok "$sum" "$RIPPLE/$f"; then cp -c "$RIPPLE/$f" "$DEST/$f"
  else curl -fL --retry 3 -o "$DEST/$f" "$BASE/$f"; ok "$sum" "$DEST/$f" || { echo "checksum mismatch: $f" >&2; exit 1; }
  fi
done <<< "$FILES"
echo "ears model: $DEST ($(du -sh "$DEST" | cut -f1))"

# The Swift package, at the pinned 1.1.2 commit, in app/Vendor/phonon-coreml (gitignored). Its PhononTDT.xcframework
# ships Headers/module.modulemap, the same path the PhononTTS core uses, and Xcode refuses two of them in one app.
# So the headers move into Headers/PhononTDT/ (clang still finds a module map one folder down). Nothing else changes.
PKG_REV=bf59e574639b060e30ee1a3f9c0e003fed5744c8
PKG="$(cd "$(dirname "$0")/.." && pwd)/app/Vendor/phonon-coreml"
if [ "$(cat "$PKG/.rev" 2>/dev/null)" != "$PKG_REV" ]; then
  rm -rf "$PKG" && mkdir -p "$(dirname "$PKG")"
  git -c advice.detachedHead=false clone -q --branch 1.1.2 https://github.com/fermionresearch/phonon-coreml "$PKG"
  [ "$(git -C "$PKG" rev-parse HEAD)" = "$PKG_REV" ] || { echo "phonon-coreml 1.1.2 is not the pinned commit" >&2; exit 1; }
  rm -rf "$PKG/.git"
  for h in "$PKG"/Binaries/PhononTDT.xcframework/*/Headers; do
    mkdir "$h/PhononTDT" && mv "$h/module.modulemap" "$h/phonon2_tdt.h" "$h/PhononTDT/"
  done
  echo "$PKG_REV" > "$PKG/.rev"
fi
echo "ears package: $PKG"
