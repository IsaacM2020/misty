#!/bin/bash
# R1's paid check, run on a day the Misty cap has room (about $0.35 for both). NOT run on 16 Nov: that day's $0.30 was spent by R0.
# Step 1: the R0 baseline again, on the code as it was before R1 (commit dfd468b) and his file before the open_url relabel.
# Step 2: the R1 3-run, on this branch with his relabelled file.
# The bar for R1: step 2's mean is not more than 2 lines under step 1's mean.
set -e
# Split across two days so the $0.30 daily cap holds: `r1_paid_check.sh 1` one day, `r1_paid_check.sh 2` the next (no arg runs both).
STEP="${1:-both}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$REPO/engine/.venv/bin/python"
OLD="$HOME/Misty/backup/r1-baseline-worktree"
[ -d "$OLD" ] || git -C "$REPO" worktree add "$OLD" dfd468b
if [ "$STEP" != 2 ]; then
echo "== 1. R0 baseline again (code dfd468b, his file before the relabel) =="
(cd "$OLD" && PYTHONPATH="$OLD/engine/src" "$PY" scripts/checks/toolpick.py --runs 3 \
   --set "$OLD/tests/engine/fixtures/toolpick.sample.yaml" "$HOME/Misty/backup/toolpick-pre-R1-open_url-2026-11-16.yaml")
fi
if [ "$STEP" != 1 ]; then
echo "== 2. R1 (this branch, his relabelled file) =="
(cd "$REPO" && "$PY" scripts/checks/toolpick.py --runs 3)
fi
