#!/bin/bash
# Sets up Fluid Speech Mini (FluidVoice, CC BY 4.0, ONNX, about 290 MB) for the after-class pass, in its own Python
# environment outside the app: ~/Library/Application Support/Misty/fluid-mini/{venv,model,transcribe.py,segments.py}.
# Without it the engine falls back to Phonon-2.5, then Whisper.
# Usage: scripts/fetch_fluid_mini.sh <folder with the model files>   (encoder.onnx, decoder.onnx, joint.onnx, frontend.onnx,
#        encoder.onnx.data, tokenizer.json, config.json, mel_filters.npy)
set -euo pipefail
SRC="${1:?give the folder that holds the model files}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/Application Support/Misty/fluid-mini"
mkdir -p "$DEST/model"
[ -x "$DEST/venv/bin/python" ] || uv venv -q --python 3.12 "$DEST/venv"
uv pip install -q --python "$DEST/venv/bin/python" onnxruntime numpy soundfile soxr
for f in encoder.onnx encoder.onnx.data decoder.onnx joint.onnx frontend.onnx tokenizer.json config.json mel_filters.npy LICENSE NOTICE; do
  [ -e "$DEST/model/$f" ] || cp "$SRC/$f" "$DEST/model/$f"
done
cp "$HERE/fluid_mini/transcribe.py" "$HERE/fluid_mini/segments.py" "$HERE/fluid_mini/serve.py" "$DEST/"
echo "fluid mini ready: $DEST"
