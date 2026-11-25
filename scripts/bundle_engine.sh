#!/bin/bash
# Xcode post-build phase: copies a standalone Python + the engine and its dependencies into Misty.app/Contents/Resources/engine.
# Skipped when MISTY_ENGINE_CMD is set (development builds run the engine from the repo).
set -euo pipefail
if [ -n "${MISTY_ENGINE_CMD:-}" ]; then exit 0; fi
DEST="${BUILT_PRODUCTS_DIR}/${CONTENTS_FOLDER_PATH}/Resources/engine"
STAMP="$DEST/.engine-stamp"
SRC_HASH=$( (cd "${SRCROOT}/../engine" && find src pyproject.toml uv.lock -type f ! -name '*.pyc' | sort | xargs shasum; \
             shasum "$0" "${SRCROOT}/../config/pinned.toml" "${SRCROOT}/../config/bootstrap-about-isaac.md" "${SRCROOT}/../config/default-ways.toml") \
           | shasum | cut -d' ' -f1)   # this script and the shipped config are part of the hash too
if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$SRC_HASH" ]; then exit 0; fi
rm -rf "$DEST/python"; mkdir -p "$DEST"
PYBIN=$(uv python find --managed-python 3.12)
PYROOT=$(dirname "$(dirname "$(readlink -f "$PYBIN")")")
cp -R "$PYROOT" "$DEST/python"
# uv marks its own Python as externally managed; this copy belongs to Misty.app, so drop the marker (copy only)
rm -f "$DEST"/python/lib/python3.*/EXTERNALLY-MANAGED
cd "${SRCROOT}/../engine"
uv export --no-dev --frozen --no-hashes --no-emit-project -o /tmp/misty-req.txt
uv pip install --python "$DEST/python/bin/python3.12" -r /tmp/misty-req.txt
rm -rf /tmp/misty-wheel && uv build --wheel -o /tmp/misty-wheel
uv pip install --python "$DEST/python/bin/python3.12" --no-deps /tmp/misty-wheel/*.whl
find "$DEST/python" -name "__pycache__" -type d -prune -exec rm -rf {} +
# config the engine reads (the launcher sets MISTY_CONFIG); named files only, signing.local never ships
rm -rf "$DEST/config" && mkdir -p "$DEST/config"
cp "${SRCROOT}/../config/pinned.toml" "${SRCROOT}/../config/bootstrap-about-isaac.md" "${SRCROOT}/../config/default-ways.toml" "$DEST/config/"
# the app only runs bin/python3.12; script entry points (pip, uvicorn, ...) would be unsigned nested code
for f in "$DEST"/python/bin/*; do file -b "$f" | grep -q "Mach-O" || rm -f "$f"; done
# no GUI toolkit, headers or docs: Tcl/Tk package dirs look like broken bundles to codesign
rm -rf "$DEST"/python/{include,share,BUILD} "$DEST"/python/lib/{itcl*,tcl9*,tk9*,thread*,libtcl*,pkgconfig} \
       "$DEST"/python/lib/python3.*/{tkinter,idlelib,turtledemo} "$DEST"/python/lib/python3.*/lib-dynload/_tkinter*
# bytecode is compiled here, before signing; at run time the engine never writes any (PYTHONDONTWRITEBYTECODE)
"$DEST/python/bin/python3.12" -m compileall -q -j 0 "$DEST/python/lib" >/dev/null 2>&1 || true
# sign every Mach-O (nested code first), then the bundle is signed by Xcode
IDENTITY="${EXPANDED_CODE_SIGN_IDENTITY_NAME:-${CODE_SIGN_IDENTITY:--}}"
find "$DEST" -type f \( -name "*.so" -o -name "*.dylib" -o -perm -u+x \) -print0 | while IFS= read -r -d '' f; do
  if file "$f" | grep -q "Mach-O"; then codesign --force --sign "$IDENTITY" --timestamp=none "$f" >/dev/null 2>&1 || true; fi
done
echo "$SRC_HASH" > "$STAMP"
