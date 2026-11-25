#!/bin/bash
# Sets up what the after-class pass reads recordings with: Phonon-2.5 English (Fermion Research, CC-BY-4.0 weights,
# 158 MB) and the `fermion-research` runner, in its own Python environment at
# ~/Library/Application Support/Misty/fermion. It lives outside Misty.app on purpose: signing the app re-signs the
# runner's speech library, and the runner then refuses to load it. Without this the engine falls back to Whisper.
# Usage: scripts/fetch_class_model.sh
set -euo pipefail
DEST="$HOME/Library/Application Support/Misty/fermion"
VERSION=0.2.15
[ -x "$DEST/bin/python" ] || uv venv -q --python 3.12 "$DEST"
uv pip install -q --python "$DEST/bin/python" "fermion-research[mlx]==$VERSION"
"$DEST/bin/python" -c "import sys; from fermion.cli import main; sys.exit(main())" transcribe phonon-2.5-en --download-only
echo "class model ready: $DEST"
