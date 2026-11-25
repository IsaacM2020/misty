#!/bin/bash
# Restart the dev Misty app (build/dev) with the engine running from source, so engine edits take effect.
# Usage: scripts/stream_dev.sh [--build]
set -euo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"
[ "${1:-}" = "--build" ] && bash "$R/scripts/dev_app.sh" | tail -1
pkill -x Misty || true
sleep 2
open --env "MISTY_ENGINE_CMD=env PYTHONPATH=$R/engine/src $R/engine/.venv/bin/python -m misty_engine" "$R/build/dev/Misty.app"
cd "$R/engine" && .venv/bin/python - <<'PY'
import asyncio
from misty_engine.stream.bench.__main__ import rpc
async def go():
    for _ in range(40):
        try:
            await rpc("hands.call", {"op": "permissions"}, 12); print("stream ready"); return
        except Exception:
            await asyncio.sleep(1)
    print("hands not ready"); raise SystemExit(1)
asyncio.run(go())
PY
