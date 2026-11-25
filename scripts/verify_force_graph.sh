#!/bin/bash
# Fails the build when the vendored force-graph.min.js differs from the checksum pinned in config/pinned.toml.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WANT=$(grep -E '^sha256' "$ROOT/config/pinned.toml" | head -1 | sed -E 's/.*"([0-9a-f]+)".*/\1/')
GOT=$(shasum -a 256 "$ROOT/app/Misty/Resources/force-graph.min.js" | cut -d' ' -f1)
if [ "$WANT" != "$GOT" ]; then echo "error: force-graph.min.js sha256 mismatch ($GOT != $WANT)" >&2; exit 1; fi
